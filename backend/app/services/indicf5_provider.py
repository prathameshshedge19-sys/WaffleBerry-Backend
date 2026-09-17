"""Local-only IndicF5 adapter for the isolated L21.4 synthesis worker.

All heavyweight imports happen after immutable artifact verification. The
adapter has no Hugging Face download path and never receives authentication
tokens, memory, prompts, tools, object keys, or user credentials.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import os
from pathlib import Path
import tempfile

from app.services.voice_providers import (
    ClonedSpeech, ClonedSpeechRequest, VoiceProviderFailure,
    validate_synthesis_request,
)
from app.services.voice_synthesis_manifest import INFERENCE_CONFIG, VerifiedManifest


class IndicF5Provider:
    provider_name = "indicf5-local-pinned"

    def __init__(self, manifest: VerifiedManifest, *, device: str = "cuda", loader=None):
        if device != "cuda":
            raise VoiceProviderFailure("voice_cuda_required")
        self.manifest = manifest
        self.device = device
        loaded = (loader or self._load_once)(manifest)
        self._model, self._vocoder, self._infer = loaded[:3]
        self._preprocess = loaded[3] if len(loaded) > 3 else None
        self.load_report = loaded[4] if len(loaded) > 4 else None

    def _load_once(self, manifest):
        # Offline mode fences dependency code that might otherwise try a
        # convenience download. All paths below were hash-verified first.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import torch
            from f5_tts.infer.utils_infer import infer_process, load_model, preprocess_ref_audio_text
            from f5_tts.model import DiT
            from safetensors import safe_open
            from vocos import Vocos
        except ImportError as exc:
            raise VoiceProviderFailure("voice_synthesis_dependency_missing") from exc
        if not torch.cuda.is_available():
            raise VoiceProviderFailure("voice_cuda_unavailable")
        model_dir = manifest.root / "indicf5"
        code = model_dir / "model.py"
        spec = importlib.util.spec_from_file_location("legarya_reviewed_indicf5_model", code)
        if spec is None or spec.loader is None:
            raise VoiceProviderFailure("voice_model_code_invalid")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model_type = getattr(module, "INF5Model", None)
        config_type = getattr(module, "INF5Config", None)
        if model_type is None or config_type is None:
            raise VoiceProviderFailure("voice_model_code_invalid")

        # The published wrapper constructor downloads Vocos/vocab and does not
        # restore model.safetensors. Recreate its reviewed architecture locally,
        # then restore the compiled EMA payload explicitly and fail closed on
        # any mismatch. The 83 embedded compiled Vocos keys are intentionally
        # ignored in favor of the separately pinned Vocos snapshot below.
        acoustic = load_model(DiT, {
            "dim": 1024, "depth": 22, "heads": 16, "ff_mult": 2,
            "text_dim": 512, "conv_layers": 4,
        }, mel_spec_type="vocos", vocab_file=str(model_dir / "checkpoints" / "vocab.txt"),
            device="cpu")
        expected = set(acoustic.state_dict())
        prefix = "ema_model._orig_mod."
        checkpoint_path = model_dir / "model.safetensors"
        with safe_open(str(checkpoint_path), framework="pt", device="cpu") as checkpoint:
            keys = set(checkpoint.keys())
            mapped = {key.removeprefix(prefix): checkpoint.get_tensor(key)
                for key in keys if key.startswith(prefix)}
        missing = sorted(expected - set(mapped))
        unexpected = sorted(set(mapped) - expected)
        auxiliary = sorted(key for key in keys if key.startswith("vocoder._orig_mod."))
        unknown = sorted(keys - {prefix + key for key in mapped} - set(auxiliary))
        if missing or unexpected or unknown or len(auxiliary) != 83:
            raise VoiceProviderFailure("voice_model_checkpoint_incompatible")
        result = acoustic.load_state_dict(mapped, strict=True, assign=True)
        if result.missing_keys or result.unexpected_keys:
            raise VoiceProviderFailure("voice_model_checkpoint_incompatible")
        del mapped
        gc.collect()
        acoustic = acoustic.eval().to(self.device)

        vocos_dir = manifest.root / "vocos"
        vocoder = Vocos.from_hparams(str(vocos_dir / "config.yaml"))
        state = torch.load(str(vocos_dir / "pytorch_model.bin"), map_location="cpu", weights_only=True)
        vocoder_result = vocoder.load_state_dict(state, strict=False)
        if vocoder_result.missing_keys or vocoder_result.unexpected_keys:
            raise VoiceProviderFailure("voice_vocoder_checkpoint_incompatible")
        vocoder = vocoder.eval().to(self.device)
        report = {
            "acoustic_keys": len(expected), "embedded_vocoder_keys": len(auxiliary),
            "model_missing_keys": tuple(result.missing_keys),
            "model_unexpected_keys": tuple(result.unexpected_keys),
            "vocoder_missing_keys": tuple(vocoder_result.missing_keys),
            "vocoder_unexpected_keys": tuple(vocoder_result.unexpected_keys),
        }
        return acoustic, vocoder, infer_process, preprocess_ref_audio_text, report

    def warmup(self) -> ClonedSpeech:
        # Startup performs a real bounded kernel/model pass through synthesize;
        # the worker supplies its own synthetic reference fixture.
        raise VoiceProviderFailure("voice_warmup_fixture_required")

    async def synthesize(self, request: ClonedSpeechRequest) -> ClonedSpeech:
        validate_synthesis_request(request)
        return await asyncio.to_thread(self._synthesize_sync, request)

    def _synthesize_sync(self, request):
        try:
            import numpy as np
        except ImportError as exc:
            raise VoiceProviderFailure("voice_synthesis_dependency_missing") from exc
        with tempfile.TemporaryDirectory(prefix="legarya-synthesis-") as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(request.reference_audio)
            processed = str(reference)
            try:
                transcript = request.reference_transcript
                if self._preprocess is not None:
                    processed, transcript = self._preprocess(str(reference), transcript,
                        show_info=lambda *_: None, device=self.device)
                generated, sample_rate, _spectrogram = self._infer(
                    processed, transcript, request.authoritative_text,
                    self._model, self._vocoder, "vocos", show_info=lambda *_: None,
                    progress=None, target_rms=INFERENCE_CONFIG["target_rms"],
                    cross_fade_duration=INFERENCE_CONFIG["cross_fade_duration"],
                    nfe_step=INFERENCE_CONFIG["nfe_step"],
                    cfg_strength=INFERENCE_CONFIG["cfg_strength"],
                    sway_sampling_coef=INFERENCE_CONFIG["sway_sampling_coef"],
                    speed=INFERENCE_CONFIG["speed"], device=self.device,
                )
            finally:
                if processed != str(reference):
                    Path(processed).unlink(missing_ok=True)
        values = np.asarray(generated, dtype=np.float32).reshape(-1)
        if (sample_rate != 24000 or values.size == 0 or values.size > 24000 * 120
                or not np.isfinite(values).all() or float(np.max(np.abs(values))) > 1.0):
            raise VoiceProviderFailure("voice_provider_output_invalid")
        pcm = (np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return ClonedSpeech(pcm, 24000, 1, request.authoritative_text_digest,
            request.operation_generation)

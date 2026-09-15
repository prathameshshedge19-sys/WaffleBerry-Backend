"""Isolated L21.3 media preparation and exact-segment Whisper boundary.

The caller supplies private bytes from the registered original. Media tools see
only fixed worker-created paths in a private per-job directory. The selected
24 kHz mono PCM/WAV is frozen and hashed before it is passed to ASR.
"""

from __future__ import annotations

import asyncio
from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import wave

from app.config import Settings, get_settings
from app.services.voice_providers import (
    REFERENCE_RECIPE, WHISPER_MODEL, WHISPER_REVISION, PreparedReference,
    reference_binding_digest,
)

SUPPORTED_MIME_TYPES = frozenset({
    "audio/wav", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/webm",
    "audio/ogg", "video/mp4", "video/webm",
})
_AUDIO_CODECS = frozenset({
    "aac", "flac", "mp3", "opus", "pcm_f32le", "pcm_s16be", "pcm_s16le",
    "pcm_s24le", "pcm_s32le", "vorbis",
})
_NETWORK_PROTOCOLS = "http,https,tcp,tls,udp,rtp,ftp,gopher,crypto,data,concat,subfile"
_FRAME_MS = 20
_MAX_TOOL_OUTPUT = 256 * 1024
_MAX_TEMP_FILES = 4
_MARATHI_MARKERS = frozenset({
    "आहे", "आहेत", "आणि", "आम्ही", "आवडते", "आठवणी", "करून", "खूप",
    "जतन", "जुन्या", "तुम्ही", "नंतर", "नाही", "मध्ये", "मधून", "मला",
    "माझे", "माझ्या", "महत्त्वाचे", "म्हणून", "साठी", "होते",
})


class VoicePreparationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MediaFacts:
    format_name: str
    duration_ms: int
    audio_codec: str
    channels: int
    sample_rate: int
    has_video: bool
    actual_mime_type: str


@dataclass(frozen=True)
class ReferenceSelection:
    wav_bytes: bytes
    start_ms: int
    duration_ms: int
    voiced_ratio: float
    silence_ratio: float
    clipping_ratio: float
    snr_db: float


def normalize_transcript(value: str) -> str:
    if not isinstance(value, str):
        raise VoicePreparationError("voice_transcript_empty")
    value = unicodedata.normalize("NFC", value)
    value = "".join(" " if unicodedata.category(char).startswith("C") else char
        for char in value)
    value = " ".join(value.split()).strip()
    if not value or len(value) > 4096:
        raise VoicePreparationError("voice_transcript_empty")
    return value


def _is_supported_marathi(transcript: str, detected: str) -> bool:
    if not any("\u0900" <= char <= "\u097f" for char in transcript):
        return False
    if detected == "mr":
        return True
    if detected != "hi":
        return False
    # Whisper commonly confuses closely related Marathi with Hindi. Accept the
    # ambiguous token only with affirmative Marathi lexical/script evidence;
    # never reinterpret arbitrary non-Marathi audio as supported.
    words = set(re.findall(r"[\u0900-\u097f]+", transcript))
    return "ळ" in transcript or bool(words & _MARATHI_MARKERS)


def _pcm_wav(samples: array, sample_rate: int = 24000) -> bytes:
    import io
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return output.getvalue()


def _wav_samples(data: bytes) -> tuple[array, int]:
    import io
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 24000):
                raise VoicePreparationError("voice_decode_invalid")
            frames = handle.getnframes()
            payload = handle.readframes(frames)
            if handle.readframes(1):
                raise VoicePreparationError("voice_decode_invalid")
    except (EOFError, wave.Error):
        raise VoicePreparationError("voice_decode_invalid") from None
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise VoicePreparationError("voice_speech_insufficient")
    return samples, 24000


def _frame_metrics(samples: array, frame_size: int):
    result = []
    for offset in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[offset:offset + frame_size]
        squares = sum(value * value for value in frame)
        rms = math.sqrt(squares / len(frame))
        clipped = sum(abs(value) >= 32700 for value in frame) / len(frame)
        crossings = sum((frame[index] < 0) != (frame[index - 1] < 0)
            for index in range(1, len(frame))) / max(1, len(frame) - 1)
        result.append((rms, clipped, crossings))
    return result


def select_reference_segment(normalized_wav: bytes, settings: Settings) -> ReferenceSelection:
    samples, sample_rate = _wav_samples(normalized_wav)
    frame_size = sample_rate * _FRAME_MS // 1000
    metrics = _frame_metrics(samples, frame_size)
    minimum = math.ceil(settings.voice_reference_min_seconds * 1000 / _FRAME_MS)
    preferred = round(settings.voice_reference_preferred_seconds * 1000 / _FRAME_MS)
    maximum = math.floor(settings.voice_reference_max_seconds * 1000 / _FRAME_MS)
    if len(metrics) < minimum:
        raise VoicePreparationError("voice_speech_insufficient")

    ordered = sorted(item[0] for item in metrics)
    noise = min(ordered[max(0, min(len(ordered) - 1, len(ordered) // 5))],
        ordered[len(ordered) // 2] * 0.30)
    threshold = max(260.0, noise * 2.5)
    raw_voice = [rms >= threshold and 0.002 <= crossings <= 0.36
        for rms, _clipped, crossings in metrics]
    # Bridge only short natural pauses; never concatenate disjoint utterances.
    voice = raw_voice[:]
    index = 0
    while index < len(voice):
        if voice[index]:
            index += 1
            continue
        end = index
        while end < len(voice) and not voice[end]:
            end += 1
        if index > 0 and end < len(voice) and end - index <= 25:
            voice[index:end] = [True] * (end - index)
        index = end

    all_runs = []
    index = 0
    while index < len(voice):
        if not voice[index]:
            index += 1
            continue
        end = index
        while end < len(voice) and voice[end]:
            end += 1
        all_runs.append((index, end))
        index = end
    runs = [(start, end) for start, end in all_runs if end - start >= minimum]
    if not runs:
        substantial = [(start, end) for start, end in all_runs
            if end - start >= 1000 // _FRAME_MS]
        if len(substantial) >= 2 and sum(end - start for start, end in substantial) >= minimum:
            raise VoicePreparationError("voice_speaker_uncertain")
        raise VoicePreparationError("voice_speech_insufficient")

    candidates = []
    for run_start, run_end in runs:
        window = min(preferred, maximum, run_end - run_start)
        if window < minimum:
            continue
        last = run_end - window
        for start in range(run_start, last + 1, 5):
            end = start + window
            window_metrics = metrics[start:end]
            voiced_ratio = sum(raw_voice[start:end]) / window
            clipping_ratio = sum(item[1] for item in window_metrics) / window
            rms = math.sqrt(sum(item[0] ** 2 for item in window_metrics) / window)
            rms_mean = sum(item[0] for item in window_metrics) / window
            rms_variation = math.sqrt(sum((item[0] - rms_mean) ** 2
                for item in window_metrics) / window) / max(1.0, rms_mean)
            snr_db = 20 * math.log10(max(1.0, rms) / max(1.0, noise))
            mean_crossings = sum(item[2] for item in window_metrics) / window
            score = (voiced_ratio * 100 + min(snr_db, 30) - clipping_ratio * 500
                - abs(window - preferred) * 0.02)
            candidates.append((score, start, end, voiced_ratio,
                clipping_ratio, snr_db, mean_crossings, rms_variation))
    if not candidates:
        raise VoicePreparationError("voice_speech_insufficient")
    (_score, start, end, voiced_ratio, clipping_ratio, snr_db,
        crossings, rms_variation) = max(candidates)
    silence_ratio = 1 - voiced_ratio
    if clipping_ratio > 0.02:
        raise VoicePreparationError("voice_clipping_severe")
    if voiced_ratio < 0.60 or silence_ratio > 0.40:
        raise VoicePreparationError("voice_speech_insufficient")
    if snr_db < 6 or crossings < 0.002 or crossings > 0.32:
        raise VoicePreparationError("voice_background_noise")
    if rms_variation < 0.025:
        raise VoicePreparationError("voice_non_speech")
    selected = samples[start * frame_size:end * frame_size]
    wav_bytes = _pcm_wav(selected, sample_rate)
    duration_ms = round(len(selected) * 1000 / sample_rate)
    if duration_ms > settings.voice_reference_max_seconds * 1000:
        raise VoicePreparationError("voice_reference_too_long")
    return ReferenceSelection(wav_bytes, start * _FRAME_MS, duration_ms,
        voiced_ratio, silence_ratio, clipping_ratio, snr_db)


def _resource_limiter(timeout_seconds: int, max_bytes: int):
    if os.name == "nt":
        return None

    def limit():
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds + 2, timeout_seconds + 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (8, 8))
    return limit


class FfmpegPreparation:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _run(self, arguments: list[str], *, cwd: Path, output_limit=_MAX_TOOL_OUTPUT):
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(arguments, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                timeout=self.settings.voice_enrollment_process_timeout_seconds,
                check=False, creationflags=creationflags,
                preexec_fn=_resource_limiter(
                    self.settings.voice_enrollment_process_timeout_seconds,
                    self.settings.voice_enrollment_decode_max_bytes + _MAX_TOOL_OUTPUT))
        except (FileNotFoundError, PermissionError):
            raise VoicePreparationError("voice_media_tool_unavailable") from None
        except subprocess.TimeoutExpired:
            raise VoicePreparationError("voice_media_timeout") from None
        if len(result.stdout) > output_limit or len(result.stderr) > output_limit:
            raise VoicePreparationError("voice_media_tool_output_exceeded")
        if result.returncode != 0:
            raise VoicePreparationError("voice_media_invalid")
        return result.stdout

    @staticmethod
    def _actual_mime(formats: set[str], has_video: bool) -> str | None:
        if "wav" in formats:
            return "audio/wav"
        if "mp3" in formats:
            return "audio/mpeg"
        if formats & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
            return "video/mp4" if has_video else "audio/mp4"
        if formats & {"matroska", "webm"}:
            return "video/webm" if has_video else "audio/webm"
        if "ogg" in formats:
            return "audio/ogg"
        return None

    def probe(self, source: Path, declared_mime: str, workdir: Path) -> MediaFacts:
        output = self._run([
            self.settings.voice_ffprobe_path, "-v", "error",
            "-protocol_whitelist", "file,pipe", "-protocol_blacklist", _NETWORK_PROTOCOLS,
            "-show_entries", "format=format_name,duration:stream=index,codec_type,codec_name,channels,sample_rate,duration",
            "-of", "json", str(source),
        ], cwd=workdir)
        try:
            value = json.loads(output)
            streams = value["streams"]
            format_value = value["format"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise VoicePreparationError("voice_media_invalid") from None
        if not isinstance(streams, list) or not 1 <= len(streams) <= self.settings.voice_enrollment_max_streams:
            raise VoicePreparationError("voice_media_streams_exceeded")
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        video = [item for item in streams if item.get("codec_type") == "video"]
        if len(audio) != 1 or len(video) > 1 or len(audio) + len(video) != len(streams):
            raise VoicePreparationError("voice_media_streams_exceeded")
        stream = audio[0]
        try:
            channels = int(stream["channels"])
            sample_rate = int(stream["sample_rate"])
            duration = float(format_value.get("duration") or stream.get("duration"))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise VoicePreparationError("voice_media_invalid") from None
        if (not math.isfinite(duration) or duration <= 0
                or duration > self.settings.voice_enrollment_max_duration_seconds):
            raise VoicePreparationError("voice_media_duration_exceeded")
        if channels < 1 or channels > self.settings.voice_enrollment_max_channels:
            raise VoicePreparationError("voice_media_channels_exceeded")
        if sample_rate < 8000 or sample_rate > self.settings.voice_enrollment_max_sample_rate:
            raise VoicePreparationError("voice_media_sample_rate_exceeded")
        codec = str(stream.get("codec_name", ""))
        if codec not in _AUDIO_CODECS:
            raise VoicePreparationError("voice_media_unsupported")
        formats = {item.strip() for item in str(format_value.get("format_name", "")).split(",") if item.strip()}
        actual = self._actual_mime(formats, bool(video))
        if actual is None:
            raise VoicePreparationError("voice_media_unsupported")
        compatible = actual == declared_mime or {actual, declared_mime} <= {"audio/mp4", "audio/x-m4a"}
        if not compatible:
            raise VoicePreparationError("voice_media_type_mismatch")
        return MediaFacts(",".join(sorted(formats)), round(duration * 1000),
            codec, channels, sample_rate, bool(video), actual)

    def decode(self, source: Path, output: Path, workdir: Path) -> bytes:
        self._run([
            self.settings.voice_ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file,pipe", "-protocol_blacklist", _NETWORK_PROTOCOLS,
            "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-af", "loudnorm=I=-20:LRA=7:TP=-2:linear=true",
            "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
            "-c:a", "pcm_s16le", "-f", "wav", str(output),
        ], cwd=workdir)
        try:
            info = output.lstat()
            if (not output.is_file() or output.is_symlink() or info.st_size <= 44
                    or info.st_size > self.settings.voice_enrollment_decode_max_bytes):
                raise VoicePreparationError("voice_decode_size_exceeded")
            files = [item for item in workdir.iterdir()]
            if len(files) > _MAX_TEMP_FILES or any(item.is_symlink() for item in files):
                raise VoicePreparationError("voice_media_temp_invalid")
            return output.read_bytes()
        except OSError:
            raise VoicePreparationError("voice_decode_invalid") from None


def _resample_24k_to_16k(samples: array) -> list[float]:
    output_length = len(samples) * 2 // 3
    result = []
    for index in range(output_length):
        position = index * 1.5
        left = int(position)
        fraction = position - left
        right = min(left + 1, len(samples) - 1)
        result.append(((1 - fraction) * samples[left] + fraction * samples[right]) / 32768.0)
    return result


class WhisperLargeV3Turbo:
    """Warm, pinned, no-remote-code ASR model loaded once by the worker."""
    model_name = WHISPER_MODEL
    revision = WHISPER_REVISION

    def __init__(self, settings: Settings):
        try:
            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        except ImportError:
            raise VoicePreparationError("voice_asr_dependency_unavailable") from None
        kwargs = {"revision": self.revision,
            "local_files_only": settings.voice_whisper_local_files_only,
            "trust_remote_code": False}
        try:
            self.processor = AutoProcessor.from_pretrained(self.model_name, **kwargs)
            self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
                self.model_name, use_safetensors=True, **kwargs)
            self.model.to("cpu").eval()
        except Exception:
            raise VoicePreparationError("voice_asr_model_unavailable") from None
        self.torch = torch

    def transcribe(self, selected_wav: bytes, language: str) -> tuple[str, str]:
        samples, _rate = _wav_samples(selected_wav)
        audio = _resample_24k_to_16k(samples)
        try:
            inputs = self.processor(audio, sampling_rate=16000,
                return_attention_mask=True, return_tensors="pt")
            with self.torch.inference_mode():
                language_ids = self.model.detect_language(inputs.input_features)
                generated = self.model.generate(inputs.input_features,
                    attention_mask=inputs.attention_mask, language="marathi",
                    task="transcribe", max_new_tokens=192)
            text = self.processor.batch_decode(generated, skip_special_tokens=True)[0]
        except Exception:
            raise VoicePreparationError("voice_asr_failed") from None
        language_id = int(language_ids[0])
        detected = next((token[2:-2] for token, token_id
            in self.model.generation_config.lang_to_id.items()
            if token_id == language_id and token.startswith("<|")
            and token.endswith("|>")), "und")
        return text, detected


class RealReferencePreparationProvider:
    provider_name = "whisper-reference-preparation"

    def __init__(self, settings: Settings | None = None, *, transcriber=None):
        self.settings = settings or get_settings()
        self.transcriber = transcriber or WhisperLargeV3Turbo(self.settings)
        self.media = FfmpegPreparation(self.settings)

    async def prepare(self, *, source: bytes, language: str,
                      operation_generation: int, declared_mime: str = "audio/wav") -> PreparedReference:
        return await asyncio.to_thread(self._prepare_sync, source, language,
            operation_generation, declared_mime)

    def _prepare_sync(self, source: bytes, language: str,
                      operation_generation: int, declared_mime: str) -> PreparedReference:
        if language != "mr" or declared_mime not in SUPPORTED_MIME_TYPES:
            raise VoicePreparationError("voice_language_unsupported")
        if not isinstance(source, bytes) or not 0 < len(source) <= self.settings.voice_enrollment_max_bytes:
            raise VoicePreparationError("voice_media_size_invalid")
        root = Path(self.settings.voice_temp_path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="prepare-", dir=root))
        try:
            try:
                work.chmod(0o700)
            except OSError:
                pass
            source_path = work / "source.media"
            source_path.write_bytes(source)
            self.media.probe(source_path, declared_mime, work)
            normalized = self.media.decode(source_path, work / "normalized.wav", work)
            selected = select_reference_segment(normalized, self.settings)
            # This digest is frozen before ASR. The ASR adapter receives these
            # exact selected bytes (and only resamples their same time span).
            audio_digest = hashlib.sha256(selected.wav_bytes).hexdigest()
            raw, detected = self.transcriber.transcribe(selected.wav_bytes, language)
            normalized_text = normalize_transcript(raw)
            if not _is_supported_marathi(normalized_text, detected):
                raise VoicePreparationError("voice_language_mismatch")
            if len(normalized_text) / max(1, selected.duration_ms / 1000) > 35:
                raise VoicePreparationError("voice_transcript_implausible")
            raw_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            transcript_digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
            binding = reference_binding_digest(audio_digest=audio_digest,
                transcript_digest=transcript_digest,
                raw_transcript_digest=raw_digest, language=language,
                asr_model=WHISPER_MODEL, asr_revision=WHISPER_REVISION,
                recipe_revision=REFERENCE_RECIPE)
            return PreparedReference(normalized_text, transcript_digest,
                audio_digest, binding, 24000, 1, selected.duration_ms,
                operation_generation, selected.wav_bytes, raw_digest, detected,
                selected.start_ms, WHISPER_MODEL, WHISPER_REVISION,
                REFERENCE_RECIPE)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def sweep_voice_temp_root(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    root = Path(settings.voice_temp_path).resolve()
    if not root.exists():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink() and child.name.startswith("prepare-"):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def get_reference_preparation_provider(settings: Settings | None = None):
    settings = settings or get_settings()
    if settings.voice_reference_provider != "whisper":
        raise VoicePreparationError("voice_reference_provider_not_configured")
    return RealReferencePreparationProvider(settings)

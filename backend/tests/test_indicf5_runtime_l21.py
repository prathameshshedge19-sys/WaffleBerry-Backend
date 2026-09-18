import asyncio
import hashlib
import threading
import time
import sys
from types import SimpleNamespace

import pytest

from app.services.indicf5_provider import IndicF5Provider
from app.services.indicf5_runtime import inference_runtime
from app.services.voice_providers import ClonedSpeechRequest, VoiceProviderFailure
from app.services.voice_synthesis_manifest import test_manifest as fake_manifest


class Model:
    def __init__(self):
        self.calls = []; self.hook = None
        def forward(text, seq_len, drop_text=False):
            self.calls.append((text, seq_len, drop_text))
            return (text, seq_len, drop_text)
        self.transformer = SimpleNamespace(text_embed=SimpleNamespace(forward=forward),
            register_forward_pre_hook=self.register)
    def register(self, callback):
        self.hook = callback
        return SimpleNamespace(remove=lambda: setattr(self, "hook", None))
    def sample(self, text):
        values = []
        for _ in range(48):
            if self.hook: self.hook()
            for drop in (False, True):
                values.append(self.transformer.text_embed.forward(text, 16, drop_text=drop))
        return values


def test_cancellation_hook_preserves_sampling_and_is_removed_after_success_failure_cancel():
    model = Model(); text = object(); expected = model.sample(text); model.calls.clear()
    original = model.transformer.text_embed.forward
    with inference_runtime(model, threading.Event()):
        assert model.sample(text) == expected and len(model.calls) == 96
        assert model.sample(text) == expected and len(model.calls) == 192
    assert model.transformer.text_embed.forward == original and model.hook is None
    with pytest.raises(RuntimeError):
        with inference_runtime(model, threading.Event()):
            model.sample(object()); raise RuntimeError("synthetic OOM")
    assert model.transformer.text_embed.forward == original and model.hook is None
    stop = threading.Event()
    with pytest.raises(VoiceProviderFailure, match="cancelled"):
        with inference_runtime(model, stop):
            stop.set(); model.sample(text)
    assert model.transformer.text_embed.forward == original and model.hook is None


def test_cancelling_provider_joins_thread_before_successor_and_preserves_model(monkeypatch):
    model = Model()
    provider = IndicF5Provider(fake_manifest(), loader=lambda _: (model, None, None))
    text = "Synthetic QA."; digest = hashlib.sha256(text.encode()).hexdigest()
    reference = b"synthetic"
    request = ClonedSpeechRequest(1, "version", text, digest, reference,
        hashlib.sha256(reference).hexdigest(), text, digest, "a" * 64, "mr", fake_manifest().digest, "live", 1)
    running = 0; peak = 0; exited = threading.Event()
    def inference(_):
        nonlocal running, peak
        running += 1; peak = max(peak, running)
        try:
            for _ in range(100):
                model.hook(); time.sleep(.001)
            return "completed"
        finally: running -= 1; exited.set()
    monkeypatch.setattr(provider, "_synthesize_sync", inference)
    async def run():
        first = asyncio.create_task(provider.synthesize(request))
        second = asyncio.create_task(provider.synthesize(request))
        await asyncio.sleep(.01); first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        assert exited.is_set()
        assert await second == "completed"
    asyncio.run(run())
    assert running == 0 and peak == 1 and model.hook is None


def test_oom_clears_traceback_and_recovers_before_next_serial_request(monkeypatch):
    model = Model()
    provider = IndicF5Provider(fake_manifest(), loader=lambda _: (model, None, None))
    cleared = []
    monkeypatch.setitem(sys.modules, "torch",
        SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: cleared.append(True))))
    def oom(_):
        raise RuntimeError("CUDA out of memory (synthetic)")
    monkeypatch.setattr(provider, "_synthesize_sync", oom)
    with pytest.raises(VoiceProviderFailure, match="voice_gpu_out_of_memory") as failure:
        provider._serialized_synthesis(None, threading.Event())
    assert failure.value.__context__.__traceback__ is None
    assert model.hook is None and provider._recover_cuda
    monkeypatch.setattr(provider, "_synthesize_sync", lambda _: "recovered")
    assert provider._serialized_synthesis(None, threading.Event()) == "recovered"
    assert cleared == [True] and not provider._recover_cuda and model.hook is None

"""Cooperative diffusion cancellation; no caching or sampling changes.

The provider holds its inference lock for this entire context.
"""
from contextlib import contextmanager

from app.services.voice_providers import VoiceProviderFailure


@contextmanager
def inference_runtime(model, stop):
    def check(*_):
        if stop.is_set():
            raise VoiceProviderFailure("voice_synthesis_cancelled")

    transformer = getattr(model, "transformer", None)
    hook = transformer.register_forward_pre_hook(check) if transformer is not None else None
    try:
        check()
        yield
        check()
    finally:
        if hook is not None:
            hook.remove()

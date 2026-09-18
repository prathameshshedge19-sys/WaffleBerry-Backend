"""L21 worker cancellation and failure recovery (whole frozen answers)."""
import asyncio
import threading

from sqlalchemy import select

from app.models.voice_profile import VoiceJob, VoiceAsset
from tests.test_realtime_l15 import live
from tests.test_realtime_speech_audit_l21 import speech_world


def test_worker_observes_cancellation_while_provider_is_running_and_recovers(speech_world):
    w = speech_world
    from app.services.voice_providers import FakeClonedSpeechProvider
    class SlowProvider(FakeClonedSpeechProvider):
        def __init__(self): self.entered = threading.Event(); self.exited = threading.Event()
        async def synthesize(self, request):
            self.entered.set()
            try: await asyncio.sleep(10)
            finally: self.exited.set()
    async def run():
        for _ in range(3):
            slow = SlowProvider(); w.worker.provider = slow
            identity, _ = await asyncio.to_thread(w.renderer._admit, w.output, w.answer, "worker", w.live[3])
            w.output.preserved_job_id = identity
            task = asyncio.create_task(asyncio.to_thread(w.worker.run_once))
            assert await asyncio.to_thread(slow.entered.wait, 3)
            await w.renderer.cancel_current(w.output)
            assert await asyncio.wait_for(task, 3) == "stale"
            assert slow.exited.is_set()
        w.worker.provider = FakeClonedSpeechProvider()
        identity, _ = await asyncio.to_thread(w.renderer._admit, w.output, w.answer, "worker", w.live[3])
        assert await asyncio.to_thread(w.worker.run_once) == "ready"
        w.output.preserved_job_id = identity
        await w.renderer.cancel_current(w.output)
    asyncio.run(run())


def test_simulated_oom_has_no_partial_asset_and_next_job_recovers(speech_world):
    w = speech_world
    from app.services.voice_providers import FakeClonedSpeechProvider
    class OOM(FakeClonedSpeechProvider):
        async def synthesize(self, request): raise RuntimeError("CUDA out of memory (synthetic)")
    identity, _ = w.renderer._admit(w.output, w.answer, "worker", w.live[3])
    w.worker.provider = OOM()
    assert w.worker.run_once() == "failed"
    with w.live[1]() as db:
        assert db.get(VoiceJob, identity).state == "failed"
        assert db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == identity)) is None
    w.worker.provider = FakeClonedSpeechProvider()
    successor, _ = w.renderer._admit(w.output, w.answer, "worker", w.live[3])
    assert successor != identity and w.worker.run_once() == "ready"
    w.output.preserved_job_id = successor
    asyncio.run(w.renderer.cancel_current(w.output))

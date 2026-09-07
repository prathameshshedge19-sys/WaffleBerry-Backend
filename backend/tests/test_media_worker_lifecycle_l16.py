"""A persistent async provider must keep one live loop across worker jobs."""
import asyncio

from sqlalchemy import func, select

from app.models.memory import Memory
from app.services.media_intelligence import RuleBasedSourceAnalysisProvider
from app.services.media_worker import MediaIntelligenceWorker
from tests.test_media_intelligence_l16 import phase_c_db, _source


def test_same_worker_keeps_provider_loop_alive_across_jobs_and_closes(phase_c_db):
    factory, storage = phase_c_db

    class LoopBoundProvider(RuleBasedSourceAnalysisProvider):
        loop = None
        calls = 0

        async def analyze(self, *args):
            current = asyncio.get_running_loop()
            if self.loop is not None:
                assert not self.loop.is_closed(), 'Provider connection loop was closed between jobs'
                assert current is self.loop
            self.loop = current
            self.calls += 1
            return await super().analyze(*args)

    provider = LoopBoundProvider()
    class Client:
        closed = False

        async def close(self):
            assert asyncio.get_running_loop() is provider.loop
            self.closed = True

    provider.client = Client()
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage, provider=provider)
    try:
        for _ in range(3):
            _source(factory, storage)
            assert worker.run_once() == 'candidates_ready'
        assert provider.calls == 3
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Memory)) == 0
    finally:
        if hasattr(worker, 'close'):
            worker.close()
    assert provider.loop.is_closed()
    assert provider.client.closed

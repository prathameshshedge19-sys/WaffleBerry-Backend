"""Bounded expiry cleanup. Enabled only with the Phase B feature flag."""
import asyncio
from contextlib import asynccontextmanager, suppress
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import get_db
from app.services.realtime_sessions import sweep
from app.services import turn_observability as obs


@asynccontextmanager
async def lifespan(app):
    def recovery_pass():
        # Follow the application's DB dependency, including isolated test DBs.
        source = app.dependency_overrides.get(get_db, get_db)()
        try:
            db = next(source)
            sweep(db, get_settings())
        except Exception:
            # No raw SQL/provider errors in logs; expired leases remain fenced.
            obs.emit("component_degraded", category="persistence_failed")
        finally:
            source.close()
    async def recover():
        while True:
            await run_in_threadpool(recovery_pass)
            await asyncio.sleep(5)

    def usage_pass():
        from app.services.plan_usage import reconcile
        from app.services.plan_auxiliary import drain
        source = app.dependency_overrides.get(get_db, get_db)()
        try:
            db = next(source)
            reconcile(db)
            drain(db)
        except Exception:
            import logging
            logging.getLogger("app.plans").warning("plan_shadow_reconciliation_degraded")
        finally:
            source.close()

    async def recover_usage():
        while True:
            await run_in_threadpool(usage_pass)
            await asyncio.sleep(15)

    task = asyncio.create_task(recover()) if get_settings().realtime_enabled else None
    usage_task = asyncio.create_task(recover_usage()) if get_settings().plans_tracking_enabled else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if usage_task:
            usage_task.cancel()
            with suppress(asyncio.CancelledError):
                await usage_task

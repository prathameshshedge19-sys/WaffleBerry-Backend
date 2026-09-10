"""Bounded best-effort auxiliary request telemetry; never a quota authority.

No bodies, URLs with identifiers, tokens or user content are queued. Process
death may drop queued auxiliary metrics; completed text/voice has its own ledger.
"""
import logging
from queue import Queue, Empty, Full
import re
from uuid import uuid4

from app.config import get_settings
from app.services.plan_usage import now, _put, warn

pending = Queue(maxsize=2048)
logger = logging.getLogger("app.plans")


def feature_for(path):
    fixed = {"/api/v1/voice/transcribe": "dictation_requests",
             "/api/v1/voice/synthesize": "read_aloud_requests",
             "/api/v1/voice/preview": "voice_preview_requests"}
    if path in fixed:
        return fixed[path]
    if re.fullmatch(r"/api/v1/stories/[^/]+/generate", path):
        return "story_generation_requests"
    if re.fullmatch(r"/api/v1/legacies/\d+/sources/[^/]+/retry", path):
        return "media_retry_requests"
    return None


class AuxiliaryUsageMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        feature = feature_for(scope.get("path", "")) if scope["type"] == "http" and scope.get("method") == "POST" else None
        if not feature or not get_settings().plans_tracking_enabled:
            return await self.app(scope, receive, send)
        status, cached = 500, False
        timestamp = now()

        async def observed_send(message):
            nonlocal status, cached
            if message["type"] == "http.response.start":
                status = message["status"]
                cached = any(k.lower() == b"x-voice-cache" and v == b"hit" for k, v in message.get("headers", []))
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            actor = scope.get("state", {}).get("plan_actor_id")
            if type(actor) is int:
                try:
                    pending.put_nowait((str(uuid4()), actor, feature, timestamp, 200 <= status < 300, cached))
                except Full:
                    warn("plan_auxiliary_queue_dropped")


def drain(db, *, limit=500):
    processed = 0
    while processed < limit:
        try:
            identity, actor, feature, timestamp, success, cached = pending.get_nowait()
        except Empty:
            break
        try:
            _put(db, key="request:" + identity, user_id=actor, feature=feature,
                 day=timestamp.date(), amount=int(success), released=int(not success),
                 state="completed" if success else "failed")
            if cached:
                _put(db, key="cache:" + identity, user_id=actor, feature="voice_cache_hits",
                     day=timestamp.date(), amount=1)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            warn("plan_auxiliary_storage_dropped")
            break
        finally:
            pending.task_done()
    return processed

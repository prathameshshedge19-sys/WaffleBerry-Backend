"""Live PostgreSQL upload-route snapshot regression for Phase D."""
import asyncio

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.api.routes import media_sources
from app.models.media_source import MediaArtifact, MediaSource
from app.models.user import User
from app.services.media_sources import MediaSourceService
from tests.test_media_sources_postgresql_l16 import pg, _reserve


def test_pg_delete_during_upload_body_cannot_reuse_preflight_snapshot(pg, monkeypatch):
    factory, storage, _ = pg
    source_id = _reserve(factory, storage)
    monkeypatch.setattr(media_sources, 'MediaSourceService', lambda: MediaSourceService(storage=storage))

    async def receive():
        # Preflight authorization has already loaded the source in the route's
        # session. Commit deletion independently before releasing body bytes.
        with factory() as deleting:
            MediaSourceService(storage=storage).delete(deleting, deleting.get(User, 1), 1, source_id)
        return {'type': 'http.request', 'body': b'hello', 'more_body': False}

    with factory() as uploading:
        request = Request({'type': 'http', 'method': 'PUT', 'path': '/', 'headers': []}, receive=receive)
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(media_sources.receive_source(1, source_id, request, uploading.get(User, 1), uploading))
        assert rejected.value.status_code == 409

    with factory() as db:
        source = db.get(MediaSource, source_id)
        assert source.state == 'deleting' and source.generation == 2
        artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.source_id == source_id))
        assert artifact.state != 'available'
        assert source.uploaded_at is None

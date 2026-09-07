"""HTTP contracts and private provenance for the L16 product integration."""
import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.user import User
from app.models.media_intelligence import SourceMemoryCandidate
from app.models.media_source import MediaSource
from app.models.memory import Memory
from app.services.media_intelligence import RuleBasedSourceAnalysisProvider
from app.services.media_worker import MediaIntelligenceWorker
from tests.test_media_intelligence_l16 import phase_c_db, _source
from tests.conftest import FakeMemoryProvider


@pytest.fixture
def product(phase_c_db, monkeypatch):
    factory, storage = phase_c_db
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    monkeypatch.setenv('MEDIA_LOCAL_STORAGE_PATH', str(storage.root))
    get_settings.cache_clear()
    previous = dict(app.dependency_overrides)
    actor = [1]
    def db():
        with factory() as session:
            yield session
    def user():
        with factory() as session:
            return session.get(User, actor[0])
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = user
    monkeypatch.setattr('app.api.routes.media_review.get_memory_provider', lambda: FakeMemoryProvider())
    with factory.begin() as session:
        session.add(User(id=3, full_name='Visitor', email='visitor-l16@example.com', password_hash='x', is_verified=True))
    with TestClient(app) as client:
        yield client, factory, storage, actor
    app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
    get_settings.cache_clear()


def processed(product):
    client, factory, storage, _ = product
    source_id = _source(factory, storage)
    assert MediaIntelligenceWorker(sessions=factory, storage=storage, provider=RuleBasedSourceAnalysisProvider()).run_once() == 'candidates_ready'
    candidates = client.get(f'/api/v1/media-review/legacies/1/sources/{source_id}/candidates').json()
    return source_id, candidates[0]


def review(client, candidate, action='preserve'):
    return client.post(f"/api/v1/media-review/legacies/1/candidates/{candidate['id']}/review", json={
        'action': action, 'expected_version': candidate['version'], 'review_request_key': str(uuid4())})


@pytest.mark.parametrize('actor,allowed,reviewer', [(1, True, True), (2, True, False), (3, False, False)])
def test_capabilities_respect_roles_and_do_not_advertise_unconfigured_transcription(product, actor, allowed, reviewer):
    client, _, _, current = product; current[0] = actor
    response = client.get('/api/v1/legacies/1/sources/capabilities')
    if not allowed:
        assert response.status_code in {403, 404}; return
    assert response.status_code == 200
    data = response.json()
    assert data['enabled'] and data['can_review'] is reviewer
    assert not data['audio_video_intelligence'] and not data['scanned_pdf_intelligence']
    assert {row['kind'] for row in data['formats']} == {'image', 'document'}
    assert response.headers['cache-control'] == 'private, no-store'


def test_upload_http_contract_unicode_download_and_noncanonical_processing(product):
    client, factory, storage, _ = product
    payload = {'kind':'document','filename':'पत्र.txt','mime_type':'text/plain','size_bytes':29,'upload_request_key':str(uuid4())}
    data = b'Pallavi loved jasmine flowers.'; payload['size_bytes'] = len(data)
    response = client.post('/api/v1/legacies/1/sources', json=payload)
    assert response.status_code == 201
    source = response.json(); assert source['uploader_name'] == 'Owner'
    content = f"/api/v1/legacies/1/sources/{source['id']}/content"
    assert client.put(content, content=data, headers={'Content-Type':'application/octet-stream'}).status_code == 202
    original = client.get(content)
    assert original.status_code == 200 and original.content == data
    assert "filename*=UTF-8''" in original.headers['content-disposition']
    assert original.headers['x-content-type-options'] == 'nosniff'
    assert MediaIntelligenceWorker(sessions=factory, storage=storage, provider=RuleBasedSourceAnalysisProvider()).run_once() == 'candidates_ready'
    with factory() as db:
        assert db.scalar(select(Memory)) is None


def test_source_evidence_and_candidates_are_private_and_cross_scope_rejected(product):
    client, _, _, actor = product
    source_id, candidate = processed(product)
    evidence_path = f'/api/v1/legacies/1/sources/{source_id}/evidence'
    assert client.get(evidence_path).json()[0]['text'] == 'Pallavi loved jasmine flowers.'
    assert client.get(evidence_path.replace('/legacies/1/', '/legacies/999/')).status_code == 404
    for user in (2, 3):
        actor[0] = user
        assert client.get(evidence_path).status_code in {403, 404}
        assert client.get(f'/api/v1/legacies/1/sources/{source_id}/content').status_code in {403, 404}
        assert review(client, candidate).status_code in {403, 404}


def test_collaborator_uploads_and_reads_own_source_but_cannot_review(product):
    client, factory, storage, actor = product; actor[0] = 2
    data = b'Pallavi loved jasmine flowers.'
    response = client.post('/api/v1/legacies/1/sources', json={'kind':'document','filename':'own.txt','mime_type':'text/plain','size_bytes':len(data),'upload_request_key':str(uuid4())})
    assert response.status_code == 201
    source_id = response.json()['id']
    assert client.put(f'/api/v1/legacies/1/sources/{source_id}/content', content=data).status_code == 202
    MediaIntelligenceWorker(sessions=factory, storage=storage, provider=RuleBasedSourceAnalysisProvider()).run_once()
    candidate = client.get(f'/api/v1/media-review/legacies/1/sources/{source_id}/candidates').json()[0]
    assert review(client, candidate).status_code == 403
    assert client.delete(f'/api/v1/legacies/1/sources/{source_id}').status_code == 403
    assert client.post(f'/api/v1/legacies/1/sources/{source_id}/retry').status_code == 403


def test_preserve_provenance_redaction_and_deleted_tombstone(product):
    client, _, _, actor = product
    source_id, candidate = processed(product)
    result = review(client, candidate); assert result.status_code == 200
    path = f"/api/v1/memories/{result.json()['canonical_memory_id']}?legacy_id=1"
    source = client.get(path).json()['source_provenance'][0]
    assert source['can_open'] and source['source_id'] == source_id and source['filename'] == 'letter.txt'
    actor[0] = 2
    source = client.get(path).json()['source_provenance'][0]
    assert source['source_id'] is None and source['filename'] is None and source['locator'] is None and not source['can_open']
    actor[0] = 1
    assert client.delete(f'/api/v1/legacies/1/sources/{source_id}').status_code == 202
    memory = client.get(path).json()
    assert memory['canonical_text'] == candidate['proposal']['canonical_text']
    assert memory['source_provenance'][0]['state'] == 'unavailable'
    assert not memory['source_provenance'][0]['can_open']
    assert client.get(f'/api/v1/legacies/1/sources/{source_id}/evidence').status_code == 410


def test_edit_preview_then_preserve_uses_explicit_revised_version(product):
    client, _, _, _ = product
    _, candidate = processed(product)
    response = client.put(f"/api/v1/media-review/legacies/1/candidates/{candidate['id']}/draft", json={'canonical_text':'Pallavi enjoyed gardening.','expected_version':1})
    assert response.status_code == 200
    draft = response.json(); assert draft['version'] == 2 and draft['review_state'] == 'pending'
    assert client.get('/api/v1/memories?legacy_id=1').json() == []
    assert review(client, candidate).status_code == 409
    preserved = review(client, draft, 'edit_preserve')
    assert preserved.status_code == 200
    assert any(e['locator']['kind'] == 'owner_edit' for e in preserved.json()['evidence'])
    assert client.get('/api/v1/memories?legacy_id=1').json()[0]['canonical_text'] == 'Pallavi enjoyed gardening.'


def test_skip_keeps_original_and_creates_no_canonical_memory(product):
    client, _, _, _ = product
    source_id, candidate = processed(product)
    result = review(client, candidate, 'skip')
    assert result.status_code == 200 and result.json()['review_state'] == 'skipped'
    assert client.get('/api/v1/memories?legacy_id=1').json() == []
    assert client.get(f'/api/v1/legacies/1/sources/{source_id}/content').status_code == 200


def test_changed_canonical_wording_marks_old_provenance_historical(product):
    client, factory, _, _ = product
    _, candidate = processed(product)
    memory_id = review(client, candidate).json()['canonical_memory_id']
    with factory.begin() as db:
        db.get(Memory, memory_id).canonical_text = 'Owner changed this wording.'
    data = client.get(f'/api/v1/memories/{memory_id}?legacy_id=1').json()
    assert data['source_provenance'][0]['state'] == 'stale'


def test_worker_cli_emits_only_safe_outcomes(monkeypatch, capsys):
    from app.services import media_worker
    monkeypatch.setenv('MEDIA_ENABLED', 'true'); get_settings.cache_clear()
    monkeypatch.setattr('sys.argv', ['media_worker', '--once'])
    class Worker:
        def run_once(self):
            raise RuntimeError('PRIVATE transcript and credentials')
    monkeypatch.setattr(media_worker, 'MediaIntelligenceWorker', Worker)
    media_worker.main()
    output = capsys.readouterr().out
    assert 'worker_unavailable' in output and 'PRIVATE' not in output
    get_settings.cache_clear()

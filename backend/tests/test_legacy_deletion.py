from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.dependencies import get_current_user
from app.api.routes.legacies import router
from app.database import get_db
from app.models.legacy import Legacy
from app.models.user import User
from app.models.memory import Memory
from app.models.conversation import Conversation, Message
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource
from app.models.viewer import LegacyViewerAccess
from app.services.authorization import require_legacy, require_persona_legacy
from app.services.legacy_deletion import preview, request_deletion, finalize_one
from app.services.media_sources import MediaSourceService
from app.services.media_storage import StorageError
from app.services.media_worker import MediaWorker
from tests.test_media_sources_l16 import media_db, _reserve
from tests.test_legacy_persona_l6 import add_memory


def request(factory, storage, legacy_id=1, owner_id=1, confirmation=None):
    with factory() as db:
        return request_deletion(db, db.get(User, owner_id), legacy_id,
            confirmation if confirmation is not None else f"DELETE LEGACY {legacy_id}",
            source_service=MediaSourceService(storage=storage))


def test_preview_names_target_counts_and_never_mutates(media_db):
    factory, storage, _ = media_db
    add_memory(factory, 1, "Synthetic memory")
    with factory() as db:
        result=preview(db, 1, 1)
        assert result['subject_name']=='Asha' and result['confirmation_text']=='DELETE LEGACY 1'
        assert result['counts']['memories']==1 and result['status']=='available'
        assert db.get(Legacy, 1).deletion_requested_at is None


@pytest.mark.parametrize('owner_id,confirmation,status',[(2,'DELETE LEGACY 1',404),(3,'DELETE LEGACY 1',404),(1,'DELETE LEGACY 2',422),(1,'delete legacy 1',422),(1,'',422)])
def test_wrong_role_or_confirmation_cannot_delete(media_db,owner_id,confirmation,status):
    factory,storage,_=media_db
    with pytest.raises(HTTPException) as error:request(factory,storage,owner_id=owner_id,confirmation=confirmation)
    assert error.value.status_code==status
    with factory() as db:assert db.get(Legacy,1).deletion_requested_at is None


def test_request_is_durable_idempotent_and_revokes_all_roles(media_db):
    factory,storage,_=media_db
    with factory.begin() as db:
        db.add(LegacyViewerAccess(legacy_id=1,user_id=3,status='active'))
    assert request(factory,storage)['status']=='deleting'
    assert request(factory,storage)['status']=='deleting'
    with factory() as db:
        assert db.get(Legacy,1).setup_status=='archived'
        for actor in (1,2,3):
            with pytest.raises(HTTPException):require_legacy(db,actor,1)
        with pytest.raises(HTTPException):require_persona_legacy(db,3,1)
        assert db.get(Legacy,2).deletion_requested_at is None


def test_file_erasure_precedes_registry_and_canonical_deletion(media_db):
    factory,storage,_=media_db
    source_id=_reserve(factory,storage)
    with factory() as db:MediaSourceService(storage=storage).receive(db,db.get(User,1),1,source_id,b'Hello')
    own=add_memory(factory,1,'Own synthetic fact');other=add_memory(factory,2,'Other synthetic fact')
    with factory.begin() as db:
        db.get(User,1).active_legacy_id=1
        chat=Conversation(legacy_id=1,user_id=1,title='Synthetic QA',mode='rya');db.add(chat);db.flush()
        db.add(Message(conversation_id=chat.id,role='user',content='Synthetic QA'))
    request(factory,storage)
    assert finalize_one(factory,storage)=='legacy_cleanup_pending'
    with factory() as db:
        assert db.get(Memory,own) is not None
        key=db.scalar(select(MediaArtifact.object_key).where(MediaArtifact.source_id==source_id))
    assert storage.exists(key)
    assert MediaWorker(factory,storage).run_once()=='purged'
    assert finalize_one(factory,storage)=='legacy_erased'
    assert not storage.exists(key)
    with factory() as db:
        assert db.get(Legacy,1) is None and db.get(Memory,own) is None
        assert db.get(Memory,other) is not None and db.get(Legacy,2) is not None
        assert db.get(User,1) is not None and db.get(User,1).active_legacy_id is None
        assert db.scalar(select(func.count()).select_from(MediaArtifact))==0
        assert db.scalar(select(func.count()).select_from(Conversation))==0
    assert finalize_one(factory,storage)=='idle'


def test_storage_failure_keeps_durable_registry_for_retry(media_db,monkeypatch):
    factory,storage,_=media_db;source_id=_reserve(factory,storage)
    request(factory,storage);worker=MediaWorker(factory,storage)
    original=storage.delete
    monkeypatch.setattr(storage,'delete',lambda *a,**k:(_ for _ in ()).throw(StorageError('storage_delete_failed')))
    assert worker.run_once()=='retry_wait'
    assert finalize_one(factory,storage)=='legacy_cleanup_pending'
    with factory.begin() as db:
        assert db.get(Legacy,1) is not None and db.get(MediaSource,source_id) is not None
        job=db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.kind=='purge'));job.next_attempt_at=datetime.now(timezone.utc)
    monkeypatch.setattr(storage,'delete',original)
    assert worker.run_once()=='purged'
    assert finalize_one(factory,storage)=='legacy_erased'


def test_stale_source_and_canonical_writers_cannot_resurrect_deleting_legacy(media_db):
    from app.services.memory import LivingMemoryService
    factory,storage,_=media_db
    with factory() as stale:
        user=stale.get(User,1);legacy=stale.get(Legacy,1)
        request(factory,storage)
        assert legacy.deletion_requested_at is None  # Hold a genuinely stale identity-map entry.
        with pytest.raises(HTTPException):require_legacy(stale,1,1)
        with pytest.raises(HTTPException):
            MediaSourceService(storage=storage).create(stale,user,1,kind='document',filename='qa.txt',mime_type='text/plain',size_bytes=5,upload_request_key=str(uuid4()))
        stale.rollback()
        with pytest.raises(ValueError):LivingMemoryService.lock_canonical_legacy(stale,1)


def test_pending_file_cleanup_does_not_starve_another_legacy(media_db):
    factory,storage,_=media_db;_reserve(factory,storage)
    request(factory,storage);request(factory,storage,legacy_id=2,owner_id=3)
    assert finalize_one(factory,storage)=='legacy_erased'
    with factory() as db:assert db.get(Legacy,1) is not None and db.get(Legacy,2) is None


def test_http_requires_acknowledgement_hides_deleting_legacy_and_preserves_account(media_db,monkeypatch):
    factory,storage,_=media_db
    app=FastAPI();app.include_router(router,prefix='/api/v1')
    def database():
        with factory() as db:yield db
    def owner():
        with factory() as db:return db.get(User,1)
    app.dependency_overrides[get_db]=database;app.dependency_overrides[get_current_user]=owner
    monkeypatch.setattr('app.services.legacy_deletion.MediaSourceService',lambda:MediaSourceService(storage=storage))
    with TestClient(app) as client:
        path='/api/v1/legacies/1'
        for body in ({'confirmation':'DELETE LEGACY 1'},{'confirmation':'DELETE LEGACY 1','acknowledge_permanent':False}):
            assert client.request('DELETE',path,json=body).status_code==422
        assert client.request('DELETE',path,json={'confirmation':'DELETE LEGACY 1','acknowledge_permanent':True}).status_code==202
        assert client.get(path).status_code==404
        assert client.get('/api/v1/legacies').json()['owned_legacies']==[]
    with factory() as db:assert db.get(User,1) is not None


def test_complete_evidence_timeline_story_graph_is_removed_in_fk_order(media_db):
    from app.database import Base
    from app.models.media_intelligence import SourceEvidence, SourceMemoryCandidate, SourceCandidateEvidence, MemorySourceLink
    from app.models.timeline import LifeEvent, LifeEventMemory, LifeEventEvidence
    from app.models.story import Story, StoryVersion, StoryChapter, StorySupportLink
    factory,storage,_=media_db
    source_id=_reserve(factory,storage)
    with factory() as db:MediaSourceService(storage=storage).receive(db,db.get(User,1),1,source_id,b'Hello')
    memory_id=add_memory(factory,1,'Synthetic complete graph')
    other_id=add_memory(factory,2,'Other Legacy must remain')
    evidence_id,candidate_id,event_id,story_id,version_id,chapter_id=[str(uuid4()) for _ in range(6)]
    with factory.begin() as db:
        job=db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id==source_id))
        artifact=db.scalar(select(MediaArtifact).where(MediaArtifact.source_id==source_id))
        db.add(SourceEvidence(id=evidence_id,legacy_id=1,source_id=source_id,generation=1,job_id=job.id,artifact_id=artifact.id,stable_key='qa',kind='text_span',text='Synthetic QA'))
        db.add(SourceMemoryCandidate(id=candidate_id,legacy_id=1,source_id=source_id,generation=1,job_id=job.id,stable_key='qa',proposal_json={},canonical_memory_id=memory_id))
        db.add(LifeEvent(id=event_id,legacy_id=1,admission_key='qa',title='Synthetic event'))
        db.add(Story(id=story_id,legacy_id=1,title='Synthetic Story',scope='full_biography',narrative_perspective='biography_third_person'))
        db.flush()
        db.add(SourceCandidateEvidence(legacy_id=1,source_id=source_id,candidate_id=candidate_id,evidence_id=evidence_id))
        db.add(MemorySourceLink(id=str(uuid4()),legacy_id=1,source_id=source_id,generation=1,memory_id=memory_id,candidate_id=candidate_id,evidence_id=evidence_id,approved_text_sha256='0'*64))
        db.add(LifeEventMemory(legacy_id=1,event_id=event_id,memory_id=memory_id))
        db.add(LifeEventEvidence(legacy_id=1,event_id=event_id,evidence_id=evidence_id))
        db.add(StoryVersion(id=version_id,legacy_id=1,story_id=story_id,version_number=1))
        db.flush()
        db.add(StoryChapter(id=chapter_id,legacy_id=1,story_version_id=version_id,title='Synthetic chapter',ordinal=1,narrative_text='Synthetic text'))
        db.flush()
        for kind,field,target in [('memory','memory_id',memory_id),('timeline_event','life_event_id',event_id),('source_evidence','evidence_id',evidence_id)]:
            db.add(StorySupportLink(id=str(uuid4()),legacy_id=1,story_version_id=version_id,chapter_id=chapter_id,support_kind=kind,**{field:target}))
    request(factory,storage)
    assert MediaWorker(factory,storage).run_once()=='purged'
    assert finalize_one(factory,storage)=='legacy_erased'
    with factory() as db:
        for table in Base.metadata.tables.values():
            if 'legacy_id' in table.c:
                assert db.scalar(select(func.count()).select_from(table).where(table.c.legacy_id==1))==0,table.name
        assert db.get(Memory,other_id).canonical_text=='Other Legacy must remain'
        assert db.get(User,1) is not None


def test_visual_profile_waits_for_actual_worker_purge(media_db):
    import io
    from PIL import Image
    from app.models.visual_companion import VisualCompanion, VisualCompanionVersion
    from app.services.visual_worker import VisualWorker
    from app.services.visual_storage import VisualStorage
    from app.services.visual_provider import FakePortraitRigProvider
    from tests.visual_l19_helpers import admission
    factory,storage,_=media_db
    data=io.BytesIO();Image.new('RGB',(256,256),(90,110,130)).save(data,'PNG');body=data.getvalue()
    source_id=_reserve(factory,storage,kind='image',mime='image/png',size=len(body),filename='synthetic.png')
    with factory() as db:
        MediaSourceService(storage=storage).receive(db,db.get(User,1),1,source_id,body)
        version_id=admission(db,source_id)
    request(factory,storage)
    assert MediaWorker(factory,storage).run_once()=='purged'
    assert finalize_one(factory,storage)=='legacy_cleanup_pending'
    worker=VisualWorker(factory,VisualStorage(storage),provider=FakePortraitRigProvider(),source_storage=storage)
    assert worker.run_once()=='purged'
    assert finalize_one(factory,storage)=='legacy_erased'
    with factory() as db:
        assert db.get(VisualCompanionVersion,version_id) is None
        assert db.scalar(select(func.count()).select_from(VisualCompanion))==0

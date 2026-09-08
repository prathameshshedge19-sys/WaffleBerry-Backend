"""Private face preparation must not create or require Legacy identity/facts."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.visual_companion import VisualCompanion
from app.services.visual_companions import VisualCompanionService
from tests.test_visual_api_l19 import api, BASE, private, routes
from tests.test_visual_companions_l19 import ready_fixture, visual
from tests.visual_l19_helpers import command, approval, factual_snapshot, source


def pending(api):
    with api.factory.begin() as db:
        legacy=db.get(Legacy,1)
        legacy.setup_status='collecting_identity'
        legacy.subject_name=legacy.relationship_to_owner=legacy.is_self=None
        db.execute(delete(Memory).where(Memory.legacy_id==1))
    with api.factory() as db:return factual_snapshot(db)


def test_unnamed_owner_can_prepare_preview_approve_and_toggle_without_factual_effects(api,monkeypatch):
    monkeypatch.setattr(routes,'preparation_service',VisualCompanionService)
    before=pending(api)
    assert api.request('GET',BASE+'/capabilities').json()['can_prepare']
    with api.factory() as db:revision=db.get(VisualCompanion,api.profile_id).revision
    response=api.request('POST',BASE+'/versions',json=command(api.source_id,revision).model_dump(mode='json'))
    private(response,202);version_id=response.json()['id']
    with api.factory() as db:
        version=ready_fixture(db,version_id,api.local)
        payload=approval(version,db.get(VisualCompanion,api.profile_id).revision).model_dump(mode='json')
    response=api.request('GET',BASE+'/versions/'+version_id+'/manifest');private(response,200)
    for asset in response.json()['assets']:private(api.request('GET',asset['content_path']),200)
    private(api.request('POST',BASE+'/activate',json=payload),200)
    for enabled in (False,True):
        revision=api.request('GET',BASE).json()['revision']
        private(api.request('PATCH',BASE,json={'enabled':enabled,'expected_revision':revision}),200)
    private(api.request('GET',BASE+'/active-manifest',actor=3),404)
    with api.factory() as db:
        assert factual_snapshot(db)==before
        assert db.get(Legacy,1).subject_name is None
        assert db.get(Legacy,1).setup_status=='collecting_identity'


@pytest.mark.parametrize('actor',[2,3,4])
def test_pending_preparation_remains_owner_only(api,actor,monkeypatch):
    monkeypatch.setattr(routes,'preparation_service',VisualCompanionService);pending(api)
    with api.factory() as db:revision=db.get(VisualCompanion,api.profile_id).revision
    private(api.request('POST',BASE+'/versions',actor=actor,json=command(api.source_id,revision).model_dump(mode='json')),404)


@pytest.mark.parametrize('state',['archived','deleting'])
def test_archived_or_deleting_legacy_cannot_prepare_or_preview(api,state):
    pending(api)
    with api.factory.begin() as db:
        legacy=db.get(Legacy,1)
        if state=='archived':legacy.setup_status='archived'
        else:legacy.deletion_requested_at=datetime.now(timezone.utc)
        revision=db.get(VisualCompanion,api.profile_id).revision
    response=api.request('POST',BASE+'/versions',json=command(api.source_id,revision).model_dump(mode='json'))
    private(response,409 if state=='archived' else 404)
    private(api.request('GET',BASE+'/versions/'+api.version_id+'/manifest'),404)


def test_preparation_off_switch_still_disables_pending_capability(api,monkeypatch):
    pending(api);monkeypatch.setattr(routes,'preparation_service',VisualCompanionService)
    monkeypatch.setenv('VISUAL_PREPARATION_ENABLED','false');get_settings.cache_clear()
    try:
        assert api.request('GET',BASE+'/capabilities').json()['can_prepare'] is False
        private(api.request('POST',BASE+'/versions',json=command(api.source_id,0).model_dump(mode='json')),503)
    finally:get_settings.cache_clear()


def test_pending_owner_cannot_use_another_legacys_photo(api):
    pending(api)
    with api.factory() as db:
        other=source(db,api.local,legacy_id=2)
        revision=db.get(VisualCompanion,api.profile_id).revision
    private(api.request('POST',BASE+'/versions',json=command(other,revision).model_dump(mode='json')),404)


def test_pending_preparation_still_requires_likeness_consent(api):
    pending(api)
    with api.factory() as db:revision=db.get(VisualCompanion,api.profile_id).revision
    payload=command(api.source_id,revision).model_dump(mode='json');payload['confirmed']=False
    private(api.request('POST',BASE+'/versions',json=payload),422)

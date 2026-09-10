from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import get_settings
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.plan_usage import PlanEntitlement, PlanTrackingState, PlanUsage
from app.models.user import User
from app.services import plan_enforcement as quota, plan_usage as usage
from tests.test_plan_shadow import shadow
from tests.test_plan_shadow_postgresql import pg
from tests.test_realtime_l15 import live, create, authenticate, connected, service, ORIGIN
from tests.test_media_sources_l16 import media_db, _reserve, MediaSourceService
from tests.test_conversation_turns_l14 import send


def enable(factory, monkeypatch):
    monkeypatch.setattr(get_settings(), 'plans_tracking_enabled', True)
    monkeypatch.setattr(get_settings(), 'plans_enforcement_enabled', True)
    with factory.begin() as db:
        for name in ['shadow', 'enforcement']:
            if not db.get(PlanTrackingState, name):
                db.add(PlanTrackingState(name=name, started_at=usage.now()-timedelta(days=1)))


def fill(factory, actor, feature, amount, day=None):
    with factory.begin() as db:
        usage._put(db, key='fixture:'+str(uuid4()), user_id=actor, feature='quota_'+feature,
                   day=day or usage.now().date(), amount=amount)


@pytest.mark.parametrize('streaming', [False, True])
def test_last_message_then_limit_preserves_replay_and_content(shadow, monkeypatch, streaming):
    client, factory, provider, cid, actor, headers, _ = shadow
    enable(factory, monkeypatch); fill(factory, actor, 'rya_text', 39)
    assert send(client, cid, headers, 'owner', 'A synthetic memory.', streaming, client_turn_id='last').status_code == (200 if streaming else 201)
    with factory() as db: before = db.scalar(select(func.count()).select_from(Message))
    blocked = send(client, cid, headers, 'owner', 'Must not reach provider.', streaming, client_turn_id='over')
    assert blocked.status_code == 429 and blocked.json()['detail']['code'] == 'plan_limit_reached'
    assert int(blocked.headers['retry-after']) > 0
    with factory() as db: assert db.scalar(select(func.count()).select_from(Message)) == before
    assert send(client, cid, headers, 'owner', 'A synthetic memory.', False, client_turn_id='last').status_code == 201
    snapshot = client.get('/api/v1/plans/usage', headers=headers).json()
    assert snapshot['enforcement_enabled'] and snapshot['daily']['rya_text']['used'] == 40
    assert snapshot['daily']['legacy_text']['remaining'] == 40
    assert client.delete(f'/api/v1/conversations/{cid}?legacy_id=1', headers=headers).status_code == 204
    with factory() as db: assert quota.totals(db, actor, 'rya_text', usage.now().date()) == (40,0)


def test_failed_reply_releases_last_slot(shadow, monkeypatch):
    client, factory, provider, cid, actor, headers, _ = shadow
    enable(factory, monkeypatch); fill(factory, actor, 'rya_text', 39)
    provider.stream_error_after = 1
    failed = send(client, cid, headers, 'owner', 'Synthetic memory.', True, client_turn_id='failed')
    assert 'event: error' in failed.text
    with factory() as db: assert quota.totals(db, actor, 'rya_text', usage.now().date()) == (39,0)
    provider.stream_error_after = None
    assert send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='retry-new').status_code == 201


def test_cutover_daily_reset_and_rollback(shadow, monkeypatch):
    client, factory, provider, cid, actor, headers, _ = shadow
    enable(factory, monkeypatch)
    # Shadow period and yesterday's enforced usage cannot exhaust today's quota.
    fill(factory, actor, 'rya_text', 40, usage.now().date()-timedelta(days=1))
    with factory.begin() as db:
        usage._put(db, key='old-shadow', user_id=actor, feature='rya_text', day=usage.now().date(), amount=999)
    assert send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='today').status_code == 201
    fill(factory, actor, 'rya_text', 39)
    monkeypatch.setattr(get_settings(), 'plans_enforcement_enabled', False)
    assert send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='rollback').status_code == 201


def test_id_bound_exemption_bypasses_all_product_caps(shadow, monkeypatch):
    client, factory, provider, cid, actor, headers, _ = shadow
    enable(factory, monkeypatch); fill(factory, actor, 'rya_text', 999)
    with factory.begin() as db: db.add(PlanEntitlement(user_id=actor, plan='free', quota_exempt=True))
    assert send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='exempt').status_code == 201
    with factory() as db:
        quota.check_capacity(db, actor, 'storage_bytes', 10**12)
        quota.check_capacity(db, actor, 'owned_legacies', 999)
        assert quota.voice_remaining(db, actor, 'rya') is None
        assert usage.snapshot(db, actor)['daily']['rya_text']['limit'] is None


def test_missing_cutover_is_safe_unavailable_not_false_quota(shadow, monkeypatch):
    client, factory, _, cid, _, headers, _ = shadow
    monkeypatch.setattr(get_settings(), 'plans_enforcement_enabled', True)
    response = send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='bad-config')
    assert response.status_code == 503 and response.json()['detail']['code'] == 'plan_check_unavailable'


def test_admission_ledger_failure_is_retriable_and_does_not_save_messages(shadow, monkeypatch):
    client,factory,_,cid,_,headers,_ = shadow
    enable(factory,monkeypatch)
    from sqlalchemy import text
    original=usage._put
    def unavailable(db,**kwargs):
        if kwargs['key'].startswith('quota:'):
            db.execute(text('SELECT * FROM intentionally_missing_quota_ledger'))
        return original(db,**kwargs)
    monkeypatch.setattr(usage,'_put',unavailable)
    with factory() as db: before=db.scalar(select(func.count()).select_from(Message))
    response=send(client,cid,headers,'owner','Synthetic memory.',False,client_turn_id='ledger-down')
    assert response.status_code==503 and response.json()['detail']['code']=='plan_check_unavailable'
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Message))==before
        assert db.scalar(select(func.count()).select_from(PlanUsage))==0


def test_owned_cap_blocks_only_creation(shadow, monkeypatch):
    client, factory, _, cid, actor, headers, _ = shadow
    enable(factory, monkeypatch)
    response = client.post('/api/v1/legacies/setup', headers=headers)
    assert response.status_code == 429 and response.json()['detail']['feature'] == 'owned_legacies'
    assert client.get('/api/v1/legacies', headers=headers).status_code == 200
    assert send(client, cid, headers, 'owner', 'Synthetic memory.', False, client_turn_id='existing').status_code == 201


def test_storage_owner_reservation_replay_and_finalization(media_db, monkeypatch):
    factory, storage, _ = media_db
    enable(factory, monkeypatch)
    monkeypatch.setitem(usage.LIMITS['free'], 'storage_bytes', 5)
    source_id = _reserve(factory, storage, user_id=2, size=5)
    with pytest.raises(HTTPException) as blocked:
        _reserve(factory, storage, size=1)
    assert blocked.value.detail['feature'] == 'storage_bytes'
    from app.models.media_source import MediaSource
    with factory() as db:
        source = db.get(MediaSource, source_id)
        repeat = MediaSourceService(storage=storage).create(db, db.get(User,2),1,kind='document',filename=source.original_filename,
            mime_type='text/plain',size_bytes=5,upload_request_key=source.upload_request_key)
        assert repeat.id == source_id
        MediaSourceService(storage=storage).receive(db, db.get(User,2),1,source_id,b'hello')
        assert usage.capacity(db,1)['storage_bytes'] == 5
        assert usage.capacity(db,2)['storage_bytes'] == 0


def test_live_warning_cutoff_and_no_text_charge(live, monkeypatch):
    client, factory, fake, settings = live
    enable(factory, monkeypatch)
    monkeypatch.setitem(usage.LIMITS['free'], 'rya_voice_ms', 1500)
    grant = create(live).json()
    with authenticate(client, grant) as ws:
        connected(ws, grant)
        events = []
        for _ in range(12):
            event = ws.receive_json(); events.append(event)
            if event['type'] == 'ended': break
        assert any(event['type'] == 'quota_warning' for event in events)
        assert any(event.get('code') == 'plan_limit_reached' for event in events)
        assert events[-1] == {'type':'ended', 'reason':'plan_limit_reached'}
    assert fake.closed and fake.cancelled
    with factory() as db:
        assert quota.totals(db,1,'rya_voice_us',usage.now().date())[0] == 1_500_000
        assert quota.totals(db,1,'rya_text',usage.now().date()) == (0,0)
    assert create(live).status_code == 429


def test_voice_setup_failure_consumes_nothing_and_text_is_independent(live, monkeypatch):
    client, factory, fake, settings = live
    enable(factory, monkeypatch); fill(factory,1,'rya_text',40)
    grant = create(live).json()
    with factory() as db:
        sid,generation,_ = service.consume(db,grant['ticket'],ORIGIN,'qa',settings)
        service.close_owned(db,sid,'qa',generation,'provider_failed',settings)
        assert quota.totals(db,1,'rya_voice_us',usage.now().date()) == (0,0)
    assert create(live).status_code == 201


def test_voice_reconnect_excludes_gap_and_splits_midnight(live, monkeypatch):
    _,factory,_,settings = live
    enable(factory,monkeypatch)
    start = datetime(2026,9,12,23,59,59,tzinfo=timezone.utc)
    with factory.begin() as db: db.get(PlanTrackingState,'enforcement').started_at = start-timedelta(days=1)
    # Ledger contract with synthetic confirmed-ready intervals.
    from app.models.plan_usage import PlanVoiceInterval
    with factory.begin() as db:
        for gen,begin,end in [(1,start,start+timedelta(seconds=2)),(2,start+timedelta(seconds=12),start+timedelta(seconds=13))]:
            interval=PlanVoiceInterval(session_id='qa-reconnect',generation=gen,user_id=1,feature='rya_voice_us',started_at=begin,observed_until=end,ended_at=end,uncertain_tail=False)
            db.add(interval); quota.voice_receipts(db,interval)
    with factory() as db:
        assert quota.totals(db,1,'rya_voice_us',start.date()) == (1_000_000,0)
        assert quota.totals(db,1,'rya_voice_us',(start+timedelta(days=1)).date()) == (2_000_000,0)


def test_postgresql_simultaneous_last_slot_admits_once(pg, monkeypatch):
    enable(pg,monkeypatch); fill(pg,1,'rya_text',39)
    with pg.begin() as db:
        db.add(Legacy(id=1,owner_user_id=1,setup_status='active')); db.flush()
        db.add_all([Conversation(id=i,user_id=1,legacy_id=1,mode='rya',title='Synthetic') for i in range(1,9)])
    from app.services.turn_lifecycle import accept_turn
    def attempt(index):
        with pg() as db:
            try:
                accept_turn(db,db.get(Conversation,index),SimpleNamespace(content='Synthetic',input_mode='text',client_turn_id=str(index)))
                return 201
            except HTTPException as error:
                db.rollback(); return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(attempt,range(1,9)))
    assert results.count(201) == 1 and results.count(429) == 7
    with pg() as db: assert quota.totals(db,1,'rya_text',usage.now().date()) == (39,1)


@pytest.mark.parametrize('feature,amount', [('owned_legacies',1),('storage_bytes',60_000_000)])
def test_postgresql_capacity_serializes_owner(pg, monkeypatch, feature, amount):
    enable(pg,monkeypatch)
    # Use real Legacy creation for the count cap; real upload reservations for storage.
    from app.services.legacy_setup import create_collecting_legacy
    if feature == 'storage_bytes':
        with pg.begin() as db:
            db.add(Legacy(id=1,owner_user_id=1,setup_status='active'))
        monkeypatch.setattr(get_settings(),'media_max_document_bytes',100_000_000)
    def attempt(index):
        with pg() as db:
            try:
                if feature == 'owned_legacies':
                    create_collecting_legacy(db,db.get(User,1)); db.commit()
                else:
                    MediaSourceService(storage=SimpleNamespace(backend_name='local',encryption_key_id=None)).create(db,db.get(User,1),1,
                        kind='document',filename='qa.txt',mime_type='text/plain',size_bytes=amount,upload_request_key=str(uuid4()))
                return 201
            except HTTPException as error:
                db.rollback(); return error.status_code
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(attempt,range(4)))
    assert results.count(201) == 1 and results.count(429) == 3

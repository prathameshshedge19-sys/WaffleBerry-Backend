from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models.legacy import Legacy
from app.models.access import LegacyAccessInvite
from app.models.collaboration import LegacyCollaborator
from app.models.memory import Memory
from app.models.viewer import LegacyViewerAccess
from tests.conftest import register_user
from tests.test_access_management_l10 import token_from
from tests.test_legacy_persona_l6 import headers, generate_legacy_code, create_visitor_chat


def draft(client, owner):
    result = client.post('/api/v1/legacies/setup', headers=headers(owner))
    assert result.status_code == 201
    legacy = result.json()['legacy']
    assert legacy['setup_status'] == 'collecting_identity'
    assert legacy['subject_name'] is None
    return legacy['id']


def assert_chat_opens(client, visitor, legacy_id):
    for path in (f'legacy-access/{legacy_id}/identity',
                 f'legacy-conversations/visitor-profile?legacy_id={legacy_id}',
                 f'legacy-conversations?legacy_id={legacy_id}'):
        assert client.get('/api/v1/' + path, headers=headers(visitor)).status_code == 200
    assert create_visitor_chat(client, visitor, legacy_id)['mode'] == 'legacy'
    assert client.get(f'/api/v1/memories?legacy_id={legacy_id}', headers=headers(visitor)).status_code == 404


def test_owner_issued_draft_code_opens_read_only_chat_without_setting_identity(test_context):
    client, sessions, codes, _ = test_context
    owner = register_user(client, codes, 'draft-owner@example.com')
    visitor = register_user(client, codes, 'draft-visitor@example.com')
    legacy_id = draft(client, owner)
    code = generate_legacy_code(client, owner, legacy_id)
    for operation in ('preview', 'join', 'join'):
        result = client.post('/api/v1/legacy-access/' + operation,
                             json={'code': code.lower().replace('-', ' ')}, headers=headers(visitor))
        assert result.status_code == 200
        assert result.json()['legacy_id'] == legacy_id
    assert_chat_opens(client, visitor, legacy_id)
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        assert legacy.setup_status == 'collecting_identity' and legacy.subject_name is None
        assert db.scalar(select(func.count(Memory.id))) == 0
        assert db.scalar(select(func.count(LegacyViewerAccess.id))) == 1


def test_accepted_viewer_invite_opens_draft_chat_and_stays_single_use(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, 'invite-draft-owner@example.com')
    visitor = register_user(client, codes, 'invite-draft-visitor@example.com')
    legacy_id = draft(client, owner)
    email = visitor['user']['email']
    assert client.post(f'/api/v1/access/legacies/{legacy_id}/invites',
                       json={'email': email, 'role': 'viewer'}, headers=headers(owner)).status_code == 201
    token = token_from(provider, email, 'viewer')
    accepted = client.post(f'/api/v1/access/invites/{token}/accept', headers=headers(visitor))
    assert accepted.status_code == 200
    assert accepted.json()['legacy_id'] == legacy_id and accepted.json()['role'] == 'viewer'
    assert_chat_opens(client, visitor, legacy_id)
    assert client.post(f'/api/v1/access/invites/{token}/accept', headers=headers(visitor)).status_code == 409
    with sessions() as db:
        assert db.get(Legacy, legacy_id).subject_name is None
        assert db.scalar(select(func.count(Memory.id))) == 0


@pytest.mark.parametrize('blocked', ['archived', 'deleting', 'disabled', 'revoked'])
def test_draft_code_does_not_bypass_access_restrictions(test_context, blocked):
    client, sessions, codes, _ = test_context
    owner = register_user(client, codes, 'blocked-owner@example.com')
    visitor = register_user(client, codes, 'blocked-visitor@example.com')
    legacy_id = draft(client, owner)
    code = generate_legacy_code(client, owner, legacy_id)
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        if blocked == 'archived': legacy.setup_status = 'archived'
        if blocked == 'deleting': legacy.deletion_requested_at = datetime.now(timezone.utc)
        if blocked == 'disabled': legacy.viewer_code_enabled = False
        if blocked == 'revoked': db.add(LegacyViewerAccess(legacy_id=legacy_id, user_id=visitor['user']['id'], status='revoked'))
        db.commit()
    result = client.post('/api/v1/legacy-access/join', json={'code': code}, headers=headers(visitor))
    assert result.status_code == (403 if blocked == 'revoked' else 404)
    assert client.get(f'/api/v1/legacy-access/{legacy_id}/identity', headers=headers(visitor)).status_code == 404


def test_malformed_unicode_viewer_code_is_invalid_not_server_error(test_context):
    client, _, codes, _ = test_context
    visitor = register_user(client, codes, 'unicode-visitor@example.com')
    result = client.post('/api/v1/legacy-access/preview', json={'code': 'LEG-éééé-éééé'}, headers=headers(visitor))
    assert result.status_code == 404


@pytest.mark.parametrize('role', ['viewer', 'collaborator'])
@pytest.mark.parametrize('restriction', [None, 'revoked', 'wrong_recipient', 'deleting', 'different_acceptor'])
def test_accepted_email_is_only_a_bookmark_for_existing_authorized_recipient(test_context, role, restriction):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, 'bookmark-owner@example.com')
    visitor = register_user(client, codes, 'bookmark-visitor@example.com')
    stranger = register_user(client, codes, 'bookmark-stranger@example.com')
    legacy_id = draft(client, owner)
    email = visitor['user']['email']
    assert client.post(f'/api/v1/access/legacies/{legacy_id}/invites',
                       json={'email': email, 'role': role}, headers=headers(owner)).status_code == 201
    token = token_from(provider, email, role)
    assert client.post(f'/api/v1/access/invites/{token}/accept', headers=headers(visitor)).status_code == 200
    model = LegacyViewerAccess if role == 'viewer' else LegacyCollaborator
    with sessions() as db:
        member = db.scalar(select(model).where(model.legacy_id == legacy_id))
        membership_id = member.id
        if restriction == 'revoked': member.status = 'revoked'
        if restriction == 'deleting': db.get(Legacy, legacy_id).deletion_requested_at = datetime.now(timezone.utc)
        if restriction == 'different_acceptor': db.scalar(select(LegacyAccessInvite)).accepted_by_user_id = stranger['user']['id']
        db.commit()
    auth = stranger if restriction == 'wrong_recipient' else visitor
    preview = client.get(f'/api/v1/access/invites/{token}', headers=headers(auth))
    assert preview.status_code == (403 if restriction else 200)
    if not restriction:
        assert preview.json()['status'] == 'accepted'
        assert preview.json()['role'] == role and preview.json()['legacy_id'] == legacy_id
    assert client.post(f'/api/v1/access/invites/{token}/accept', headers=headers(visitor)).status_code == 409
    with sessions() as db:
        members = db.scalars(select(model)).all()
        assert len(members) == 1 and members[0].id == membership_id
        assert members[0].status == ('revoked' if restriction == 'revoked' else 'active')
        assert db.scalar(select(func.count(Memory.id))) == 0

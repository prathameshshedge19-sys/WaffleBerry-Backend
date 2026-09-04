from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.models.access import LegacyAccessEvent, LegacyAccessInvite
from app.models.collaboration import LegacyCollaborator
from app.models.viewer import LegacyViewerAccess
from tests.conftest import register_user
from tests.test_collaboration_l5 import headers, owner_legacy


def token_from(provider, email, role):
    return parse_qs(urlparse(provider.invite_links[(email, role)]).query)["token"][0]


def test_owner_can_invite_each_role_and_acceptance_is_scoped_single_use(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, "l10-owner@example.com", "Owner")
    legacy_id = owner_legacy(client, sessions, owner)["legacy_id"]
    collaborator = register_user(client, codes, "l10-builder@example.com", "Builder")
    viewer = register_user(client, codes, "l10-viewer@example.com", "Viewer")
    for auth, role in ((collaborator, "collaborator"), (viewer, "viewer")):
        email = auth["user"]["email"]
        sent = client.post(f"/api/v1/access/legacies/{legacy_id}/invites", json={"email": email, "role": role}, headers=headers(owner))
        assert sent.status_code == 201, sent.text
        token = token_from(provider, email, role)
        assert client.get(f"/api/v1/access/invites/{token}", headers=headers(auth)).status_code == 200
        accepted = client.post(f"/api/v1/access/invites/{token}/accept", headers=headers(auth))
        assert accepted.status_code == 200 and accepted.json()["role"] == role
        assert client.post(f"/api/v1/access/invites/{token}/accept", headers=headers(auth)).status_code == 409
    with sessions() as db:
        assert len(db.scalars(select(LegacyCollaborator)).all()) == 1
        assert len(db.scalars(select(LegacyViewerAccess)).all()) == 1


def test_wrong_recipient_owner_boundary_revocation_and_expiry(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, "l10-sec-owner@example.com", "Owner")
    legacy_id = owner_legacy(client, sessions, owner)["legacy_id"]
    invited = register_user(client, codes, "l10-invited@example.com", "Invited")
    stranger = register_user(client, codes, "l10-stranger@example.com", "Stranger")
    assert client.get(f"/api/v1/access/legacies/{legacy_id}", headers=headers(stranger)).status_code == 404
    client.post(f"/api/v1/access/legacies/{legacy_id}/invites", json={"email": invited["user"]["email"], "role": "viewer"}, headers=headers(owner))
    token = token_from(provider, invited["user"]["email"], "viewer")
    assert client.get(f"/api/v1/access/invites/{token}", headers=headers(stranger)).status_code == 403
    panel = client.get(f"/api/v1/access/legacies/{legacy_id}", headers=headers(owner)).json()
    invite_id = panel["pending_invites"][0]["id"]
    assert client.delete(f"/api/v1/access/legacies/{legacy_id}/invites/{invite_id}", headers=headers(owner)).status_code == 204
    assert client.post(f"/api/v1/access/invites/{token}/accept", headers=headers(invited)).status_code == 409
    client.post(f"/api/v1/access/legacies/{legacy_id}/invites", json={"email": invited["user"]["email"], "role": "viewer"}, headers=headers(owner))
    expired_token = token_from(provider, invited["user"]["email"], "viewer")
    with sessions() as db:
        invite = db.scalar(select(LegacyAccessInvite).where(LegacyAccessInvite.status == "pending"))
        invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1); db.commit()
    assert client.post(f"/api/v1/access/invites/{expired_token}/accept", headers=headers(invited)).status_code == 410


def test_unified_member_and_code_controls_preserve_memberships_and_audit(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, "l10-control-owner@example.com", "Owner")
    legacy_id = owner_legacy(client, sessions, owner)["legacy_id"]
    viewer = register_user(client, codes, "l10-control-viewer@example.com", "Viewer")
    client.post(f"/api/v1/access/legacies/{legacy_id}/invites", json={"email": viewer["user"]["email"], "role": "viewer"}, headers=headers(owner))
    token = token_from(provider, viewer["user"]["email"], "viewer")
    client.post(f"/api/v1/access/invites/{token}/accept", headers=headers(viewer))
    panel = client.post(f"/api/v1/access/legacies/{legacy_id}/codes/viewer/regenerate", headers=headers(owner)).json()
    access_id = panel["viewers"][0]["access_id"]
    old_code = panel["codes"]["viewer"]["code"]
    assert client.delete(f"/api/v1/access/legacies/{legacy_id}/codes/viewer", headers=headers(owner)).status_code == 204
    panel = client.get(f"/api/v1/access/legacies/{legacy_id}", headers=headers(owner)).json()
    assert panel["counts"]["viewers"] == 1 and not panel["codes"]["viewer"]["enabled"]
    assert client.post(f"/api/v1/access/legacies/{legacy_id}/codes/viewer/enable", headers=headers(owner)).status_code == 200
    assert client.post(f"/api/v1/access/legacies/{legacy_id}/members/viewer/{access_id}/revoke", headers=headers(owner)).status_code == 200
    assert client.get(f"/api/v1/legacy-access/{legacy_id}/identity", headers=headers(viewer)).status_code == 404
    restored = client.post(f"/api/v1/access/legacies/{legacy_id}/members/viewer/{access_id}/restore", headers=headers(owner))
    assert restored.status_code == 200 and restored.json()["counts"]["viewers"] == 1
    assert old_code == restored.json()["codes"]["viewer"]["code"]
    body = restored.text
    assert "token_digest" not in body and token not in body
    with sessions() as db:
        assert len(db.scalars(select(LegacyAccessEvent)).all()) >= 6

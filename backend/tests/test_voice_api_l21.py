from uuid import uuid4

import pytest

from app.config import get_settings
from app.models.collaboration import LegacyCollaborator
from app.models.legacy import Legacy
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.services.security import create_access_token

BASE = "/api/v1/legacies/101/voice-profile"


def headers(user):
    return {"Authorization": "Bearer " + create_access_token(user.id)}


def private(response, expected):
    assert response.status_code == expected, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "object_key" not in response.text
    assert "encryption_key" not in response.text
    assert "reference_transcript" not in response.text


@pytest.fixture
def api(test_context, monkeypatch):
    client, factory, _, _ = test_context
    with factory() as db:
        users = [User(id=100 + i, full_name=f"L21 API user {i}",
            email=f"l21-api-{i}@example.invalid", password_hash="unused") for i in range(1, 5)]
        db.add_all(users)
        db.flush()
        db.add(Legacy(id=101, owner_user_id=101, subject_name="Synthetic API Legacy", setup_status="active"))
        db.flush()
        db.add(LegacyCollaborator(legacy_id=101, user_id=102, status="active"))
        db.add(LegacyViewerAccess(legacy_id=101, user_id=103, status="active"))
        db.commit()
    state = {user.id: user for user in users}
    get_settings.cache_clear()
    yield client, factory, state, monkeypatch
    get_settings.cache_clear()


def enable(monkeypatch):
    monkeypatch.setenv("VOICE_CLONING_ENABLED", "true")
    monkeypatch.setenv("VOICE_ENROLLMENT_ENABLED", "true")
    get_settings.cache_clear()


def command(key=None, **changes):
    value = {"request_key": key or str(uuid4()), "expected_revision": 0,
        "language": "mr", "consented": True,
        "consent_copy_version": "l21-voice-consent-v1",
        "policy_version": "l21-voice-policy-v1", "authority_basis": "self",
        "source_category": "self_recording", "presented_copy_digest": "a" * 64}
    value.update(changes)
    return value


def test_flags_default_off_and_existing_standard_voice_contract_unchanged(api):
    client, _, users, _ = api
    settings = get_settings()
    assert not any((settings.voice_cloning_enabled, settings.voice_enrollment_enabled,
        settings.voice_message_playback_enabled, settings.voice_live_enabled))
    private(client.get(BASE, headers=headers(users[101])), 404)
    # Existing L12 settings endpoint and its Marin/Cedar shape are untouched.
    current = client.get("/api/v1/voice/settings", headers=headers(users[101]))
    assert current.status_code == 200 and current.json()["voice"] == "marin"


def test_owner_status_enrollment_idempotency_and_safe_serialization(api):
    client, _, users, monkeypatch = api
    enable(monkeypatch)
    private(client.get(BASE), 401)
    response = client.get(BASE, headers=headers(users[101]))
    private(response, 200)
    assert response.json() == {"exists": False, "lifecycle": None, "revision": 0,
        "language": "mr", "current_available": False, "candidate_available": False,
        "failure_code": None, "capabilities": {"owner_managed": True,
            "can_enroll": True, "message_playback": False, "live": False}}
    key = str(uuid4())
    first = client.post(BASE + "/enrollments", headers=headers(users[101]), json=command(key))
    private(first, 202)
    repeated = client.post(BASE + "/enrollments", headers=headers(users[101]), json=command(key))
    private(repeated, 202)
    assert repeated.json() == first.json()
    changed = client.post(BASE + "/enrollments", headers=headers(users[101]),
        json=command(key, authority_basis="authorized_representative"))
    private(changed, 409)


def test_collaborator_visitor_outsider_and_cross_legacy_are_non_enumerating(api):
    client, _, users, monkeypatch = api
    enable(monkeypatch)
    for actor in (102, 103, 104):
        private(client.get(BASE, headers=headers(users[actor])), 404)
        private(client.post(BASE + "/enrollments", headers=headers(users[actor]), json=command()), 404)
    # A valid owner still cannot address another/nonexistent Legacy profile.
    private(client.get("/api/v1/legacies/999999/voice-profile", headers=headers(users[101])), 404)


def test_malformed_and_overlong_metadata_are_sanitized(api):
    client, _, users, monkeypatch = api
    enable(monkeypatch)
    bad = command(request_key="not-a-uuid", authority_basis="x" * 5000)
    bad["unexpected_secret"] = "must-not-be-echoed"
    response = client.post(BASE + "/enrollments", headers=headers(users[101]), json=bad)
    private(response, 422)
    assert response.json()["detail"]["code"] == "voice_request_invalid"
    assert "must-not-be-echoed" not in response.text and "not-a-uuid" not in response.text

import copy

import pytest
from sqlalchemy import select, update

from app.models.memory import Memory
from app.models.personality import LegacyPersonalityProfile as Row
from app.services.memory import CanonicalEdit
from app.services.personality_worker import PersonalityWorker
from tests.conftest import register_user
from tests.test_collaboration_l5 import generate_code, join
from tests.test_legacy_persona_l6 import generate_legacy_code, grant, headers
from tests.test_memory import _new_legacy
from tests.test_personality_style_l13 import prepared


def read(client, auth, legacy_id):
    return client.get(f"/api/v1/legacies/{legacy_id}/personality", headers=headers(auth))


def test_owner_ready_response_is_human_readable_and_read_only(test_context):
    client, sessions, _, _, owner, legacy_id, ids = prepared(test_context, [("Pallavi was warm with family.", "personality"), ("Pallavi valued education.", "value")])
    with sessions() as db:
        before = dict(db.execute(select(Row.__table__)).mappings().one())
    response = read(client, owner, legacy_id)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready" and data["legacy_id"] == legacy_id
    assert data["observations"][0]["description"] == "Warm with family."
    assert data["observations"][0]["confidence"] == "Supported"
    assert data["observations"][0]["supporting_memory_ids"] == [ids[0]]
    assert data["observations"][1]["description"] == "Valued education."
    assert set(data) == {"legacy_id", "status", "observations", "signature_expressions"}
    assert set(data["observations"][0]) == {"description", "dimension", "confidence", "context", "conflicting_accounts", "supporting_memory_ids", "supporting_memory_count"}
    with sessions() as db:
        assert dict(db.execute(select(Row.__table__)).mappings().one()) == before


def test_active_collaborator_can_read_without_new_role_system(test_context):
    client, _, codes, _, owner, legacy_id, _ = prepared(test_context, [("Pallavi was warm.", "personality")])
    collaborator = register_user(client, codes, email="dashboard-collaborator@example.com")
    join(client, collaborator, generate_code(client, owner, legacy_id))
    assert read(client, collaborator, legacy_id).status_code == 200


@pytest.mark.parametrize("role", ["visitor", "unrelated", "anonymous"])
def test_management_read_denies_nonbuilders(test_context, role):
    client, _, codes, _, owner, legacy_id, _ = prepared(test_context, [("Pallavi was warm.", "personality")])
    if role == "anonymous":
        assert client.get(f"/api/v1/legacies/{legacy_id}/personality").status_code == 401
        return
    other = register_user(client, codes, email="dashboard-nonbuilder@example.com")
    if role == "visitor":
        grant(client, other, generate_legacy_code(client, owner, legacy_id))
    assert read(client, other, legacy_id).status_code in (403, 404)


def test_cross_legacy_scope_and_invalid_ids(test_context):
    client, sessions, _, _, owner, legacy_id, _ = prepared(test_context, [("Pallavi was warm.", "personality")])
    other_id = _new_legacy(client, sessions, owner, "Madhukar")
    other = read(client, owner, other_id).json()
    assert other["legacy_id"] == other_id and other["observations"] == []
    assert read(client, owner, legacy_id).json()["observations"]
    assert read(client, owner, 0).status_code == 422
    assert read(client, owner, "invalid").status_code == 422
    assert read(client, owner, 999999).status_code in (403, 404)


@pytest.mark.parametrize("condition,expected", [("missing", "unavailable"), ("pending", "rebuilding"), ("building", "rebuilding"), ("failed", "failed"), ("stale", "stale"), ("malformed", "unavailable"), ("version", "unavailable")])
def test_freshness_states_never_expose_stale_observations(test_context, condition, expected):
    client, sessions, _, _, owner, legacy_id, _ = prepared(test_context, [("Pallavi was warm.", "personality")])
    with sessions.begin() as db:
        row = db.get(Row, legacy_id)
        if condition == "missing":
            db.delete(row)
        elif condition in ("pending", "building", "failed"):
            row.build_status = condition
        elif condition == "stale":
            row.source_generation += 1
        elif condition == "malformed":
            row.profile_json = {"bad": "data"}
        else:
            row.schema_version = 999
    response = read(client, owner, legacy_id)
    assert response.status_code == 200
    assert response.json()["status"] == expected
    assert response.json()["observations"] == response.json()["signature_expressions"] == []


def test_exact_signature_and_context_are_returned_without_internal_fields(test_context):
    phrase = "\u0905\u0917\u0902 \u092c\u093e\u0908"
    client, sessions, _, _, owner, legacy_id, ids = prepared(test_context, [(f'Pallavi often said "{phrase}" when surprised.', "habit")])
    with sessions.begin() as db:
        db.get(Memory, ids[0]).source_language = "mixed"
    assert PersonalityWorker(sessions).run_once() == "ready"
    result = read(client, owner, legacy_id).json()
    expression = result["signature_expressions"][0]
    assert expression["expression"] == phrase and expression["context"] == "Used when surprised"
    assert expression["language"] == "Mixed languages"
    assert expression["supporting_memory_count"] == 1
    serialized = str(result)
    for key in ("lease_token", "attempts", "profile_json", "builder_id", "content_fingerprint", "source_generation", "prompt"):
        assert key not in serialized


def test_forged_or_deleted_evidence_is_not_shown_as_current(test_context):
    client, sessions, _, _, owner, legacy_id, ids = prepared(test_context, [("Pallavi was warm.", "personality")])
    with sessions.begin() as db:
        row = db.get(Row, legacy_id)
        original = copy.deepcopy(row.profile_json)
        forged = copy.deepcopy(original)
        forged["observations"][0]["evidence"][0]["memory_id"] = 999999
        row.profile_json = forged
    assert read(client, owner, legacy_id).json()["observations"] == []
    with sessions.begin() as db:
        db.get(Row, legacy_id).profile_json = original
        db.execute(update(Memory).where(Memory.id == ids[0]).values(status="deleted"))
    assert read(client, owner, legacy_id).json()["status"] == "stale"


def test_memory_edit_delete_and_rebuild_update_dashboard_without_blocking(test_context):
    client, sessions, _, provider, owner, legacy_id, ids = prepared(test_context, [("Pallavi was warm with family.", "personality"), ("Pallavi valued education.", "value")])
    replacement = "Pallavi was quiet with family."
    provider.memory_provider.canonical_edits[replacement] = CanonicalEdit(canonical_text=replacement, source_language="english", entities=[])
    response = client.patch(f"/api/v1/memories/{ids[0]}?legacy_id={legacy_id}", json={"canonical_text": replacement}, headers=headers(owner))
    assert response.status_code == 200
    assert read(client, owner, legacy_id).json()["status"] == "rebuilding"
    assert PersonalityWorker(sessions).run_once() == "ready"
    assert read(client, owner, legacy_id).json()["observations"][0]["description"] == "Quiet with family."
    assert client.delete(f"/api/v1/memories/{ids[0]}?legacy_id={legacy_id}", headers=headers(owner)).status_code == 204
    assert read(client, owner, legacy_id).json()["observations"] == []
    assert PersonalityWorker(sessions).run_once() == "ready"
    assert [item["description"] for item in read(client, owner, legacy_id).json()["observations"]] == ["Valued education."]


def test_endpoint_is_read_only_and_profile_failures_do_not_break_memory_list(test_context, monkeypatch):
    client, _, _, _, owner, legacy_id, _ = prepared(test_context, [("Pallavi was warm.", "personality")])
    for method in ("post", "patch", "delete"):
        assert getattr(client, method)(f"/api/v1/legacies/{legacy_id}/personality", headers=headers(owner)).status_code == 405
    def unavailable(*args):
        raise RuntimeError("derived cache unavailable")
    monkeypatch.setattr("app.services.personality_dashboard.current_profile", unavailable)
    assert read(client, owner, legacy_id).json()["status"] == "unavailable"
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(owner)).status_code == 200

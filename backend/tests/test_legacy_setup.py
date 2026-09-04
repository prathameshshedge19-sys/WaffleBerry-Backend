import pytest
from sqlalchemy import func, select

from app.models.conversation import Conversation
from app.models.legacy import Legacy
from app.models.user import User
from app.services.legacy_setup import extract_legacy_identity
from app.services.rya import RYA_SYSTEM_PROMPT
from tests.conftest import register_user


def _headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def _conversation(client, auth, legacy_id=None):
    payload = {"title": "New chat"}
    if legacy_id is not None:
        payload["legacy_id"] = legacy_id
    response = client.post("/api/v1/conversations", json=payload, headers=_headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


def _stream(client, auth, conversation_id, content):
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content},
        headers=_headers(auth),
    )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text
    return response


def test_self_legacy_uses_explicit_profile_name_and_activates(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, name="Prathamesh")
    conversation = _conversation(client, auth)
    _stream(client, auth, conversation["id"], "I want to create my own Legacy.")

    with sessions() as db:
        legacy = db.scalar(select(Legacy))
        stored_conversation = db.get(Conversation, conversation["id"])
        assert (legacy.subject_name, legacy.relationship_to_owner, legacy.is_self) == ("Prathamesh", "self", True)
        assert legacy.setup_status == "active"
        assert stored_conversation.legacy_id == legacy.id
    context = provider.calls[-1][0].content
    assert "setup_completed_on_this_turn: yes" in context
    assert "Rya is never the Legacy subject" in context


def test_other_person_setup_persists_only_missing_name(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="mother@example.com")
    conversation = _conversation(client, auth)
    _stream(client, auth, conversation["id"], "I want to create one for my mother.")

    with sessions() as db:
        legacy = db.scalar(select(Legacy))
        assert legacy.is_self is False
        assert legacy.relationship_to_owner == "mother"
        assert legacy.subject_name is None
        assert legacy.setup_status == "collecting_identity"
    context = provider.calls[-1][0].content
    assert "relationship_to_owner: mother" in context
    assert "missing_fields: subject_name" in context


def test_all_in_one_setup_avoids_redundant_identity_state(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="all-in-one@example.com")
    conversation = _conversation(client, auth)
    _stream(client, auth, conversation["id"], "I want to create a Legacy for my mother Anjali.")

    with sessions() as db:
        legacy = db.scalar(select(Legacy))
        assert (legacy.subject_name, legacy.relationship_to_owner, legacy.is_self) == ("Anjali", "mother", False)
        assert legacy.setup_status == "active"
    context = provider.calls[-1][0].content
    assert "missing_fields: none" in context
    assert "subject_name: Anjali" in context


def test_all_in_one_grandfather_and_self_extraction_contracts():
    grandfather = extract_legacy_identity("I want to create a Legacy for my grandfather Madhukar.")
    assert (grandfather.target_type, grandfather.relationship, grandfather.subject_name) == ("other", "grandfather", "Madhukar")
    own = extract_legacy_identity("I'm Prathamesh and I'm creating this for myself.")
    assert (own.target_type, own.relationship, own.subject_name) == ("self", "self", "Prathamesh")


@pytest.mark.parametrize(
    "phrase",
    [
        "Majhya aai sathi.",
        "Meri maa ke liye.",
        "Für meine Mutter.",
    ],
)
def test_multilingual_mother_relationship_normalization(test_context, phrase):
    client, sessions, codes, _provider = test_context
    auth = register_user(client, codes, email=f"language-{abs(hash(phrase))}@example.com")
    conversation = _conversation(client, auth)
    _stream(client, auth, conversation["id"], phrase)
    with sessions() as db:
        legacy = db.scalar(select(Legacy))
        assert legacy.relationship_to_owner == "mother"
        assert legacy.is_self is False
        assert legacy.subject_name is None


def test_interrupted_setup_survives_and_new_chat_reuses_legacy(test_context):
    client, sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="resume@example.com")
    first = _conversation(client, auth)
    _stream(client, auth, first["id"], "For my mother.")

    context = client.get("/api/v1/legacies", headers=_headers(auth)).json()
    assert context["legacies"][0]["missing_fields"] == ["subject_name"]
    legacy_id = context["active_legacy_id"]

    second = _conversation(client, auth)
    assert second["legacy_id"] == legacy_id
    _stream(client, auth, second["id"], "Anjali")
    with sessions() as db:
        assert db.scalar(select(func.count(Legacy.id))) == 1
        legacy = db.get(Legacy, legacy_id)
        assert legacy.subject_name == "Anjali"
        assert legacy.setup_status == "active"


def test_first_legacy_bootstrap_is_idempotent_and_keeps_one_id_through_completion(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="bootstrap@example.com")

    first = client.post("/api/v1/legacies/setup/bootstrap", headers=_headers(auth))
    repeated = client.post("/api/v1/legacies/setup/bootstrap", headers=_headers(auth))
    assert first.status_code == repeated.status_code == 200
    legacy_id = first.json()["legacy"]["id"]
    assert repeated.json()["legacy"]["id"] == legacy_id

    conversation = _conversation(client, auth, legacy_id)
    assert conversation["legacy_id"] == legacy_id
    _stream(client, auth, conversation["id"], "Someone I love.")
    context = client.get("/api/v1/legacies", headers=_headers(auth)).json()
    assert context["legacies"][0]["missing_fields"] == ["relationship", "subject_name"]
    assert "missing_fields: relationship, subject_name" in provider.calls[-1][0].content
    assert "learn their relationship to the owner before asking their name" in provider.calls[-1][0].content
    _stream(client, auth, conversation["id"], "My mother.")
    _stream(client, auth, conversation["id"], "Pallavi.")

    with sessions() as db:
        assert db.scalar(select(func.count(Legacy.id))) == 1
        legacy = db.get(Legacy, legacy_id)
        stored_conversation = db.get(Conversation, conversation["id"])
        assert stored_conversation.legacy_id == legacy_id
        assert (legacy.subject_name, legacy.relationship_to_owner, legacy.is_self) == ("Pallavi", "mother", False)
        assert legacy.setup_status == "active"


def test_bootstrap_restores_pending_legacy_when_active_pointer_is_missing(test_context):
    client, sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="bootstrap-restore@example.com")
    created = client.post("/api/v1/legacies/setup/bootstrap", headers=_headers(auth)).json()["legacy"]
    _stream(client, auth, _conversation(client, auth, created["id"])["id"], "For my mother.")

    with sessions() as db:
        user = db.scalar(select(User).where(User.email == "bootstrap-restore@example.com"))
        user.active_legacy_id = None
        db.commit()

    restored = client.post("/api/v1/legacies/setup/bootstrap", headers=_headers(auth))
    assert restored.status_code == 200
    assert restored.json()["legacy"]["id"] == created["id"]
    context = client.get("/api/v1/legacies", headers=_headers(auth)).json()
    assert context["active_legacy_id"] == created["id"]
    assert context["legacies"][0]["missing_fields"] == ["subject_name"]
    with sessions() as db:
        assert db.scalar(select(func.count(Legacy.id))) == 1


def test_myself_answer_and_create_another_use_collecting_legacy_records(test_context):
    client, sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="bootstrap-self@example.com", name="Prathamesh")
    first = client.post("/api/v1/legacies/setup/bootstrap", headers=_headers(auth)).json()["legacy"]
    first_conversation = _conversation(client, auth, first["id"])
    _stream(client, auth, first_conversation["id"], "Myself.")

    another = client.post("/api/v1/legacies/setup", headers=_headers(auth))
    assert another.status_code == 201
    second = another.json()["legacy"]
    assert second["id"] != first["id"]
    assert second["setup_status"] == "collecting_identity"
    second_conversation = _conversation(client, auth, second["id"])
    assert second_conversation["legacy_id"] == second["id"]

    with sessions() as db:
        first_legacy = db.get(Legacy, first["id"])
        assert (first_legacy.subject_name, first_legacy.relationship_to_owner, first_legacy.is_self) == ("Prathamesh", "self", True)
        assert first_legacy.setup_status == "active"
        assert db.scalar(select(func.count(Legacy.id))) == 2


def test_multiple_legacies_switch_without_identity_contamination(test_context):
    client, sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="multiple@example.com", name="Prathamesh")
    self_chat = _conversation(client, auth)
    _stream(client, auth, self_chat["id"], "This Legacy is for myself.")
    self_legacy_id = self_chat["legacy_id"]

    setup = client.post("/api/v1/legacies/setup", headers=_headers(auth))
    assert setup.status_code == 201
    mother_legacy_id = setup.json()["legacy"]["id"]
    mother_chat = _conversation(client, auth, mother_legacy_id)
    _stream(client, auth, mother_chat["id"], "I am preserving my mother Anjali.")
    next_chat = _conversation(client, auth)

    assert next_chat["legacy_id"] == mother_legacy_id
    assert client.post(f"/api/v1/legacies/{self_legacy_id}/select", headers=_headers(auth)).status_code == 200
    selected_chat = _conversation(client, auth)
    assert selected_chat["legacy_id"] == self_legacy_id
    with sessions() as db:
        assert db.scalar(select(func.count(Legacy.id))) == 2
        self_legacy = db.get(Legacy, self_legacy_id)
        mother_legacy = db.get(Legacy, mother_legacy_id)
        assert (self_legacy.subject_name, self_legacy.relationship_to_owner) == ("Prathamesh", "self")
        assert (mother_legacy.subject_name, mother_legacy.relationship_to_owner) == ("Anjali", "mother")


def test_legacy_endpoints_and_conversation_association_enforce_ownership(test_context):
    client, _sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="legacy-owner@example.com")
    owner_chat = _conversation(client, owner)
    legacy_id = owner_chat["legacy_id"]
    other = register_user(client, codes, email="legacy-other@example.com")

    assert client.get(f"/api/v1/legacies/{legacy_id}", headers=_headers(other)).status_code == 404
    assert client.post(f"/api/v1/legacies/{legacy_id}/select", headers=_headers(other)).status_code == 404
    denied = client.post(
        "/api/v1/conversations",
        json={"title": "Not mine", "legacy_id": legacy_id},
        headers=_headers(other),
    )
    assert denied.status_code == 404


def test_conversation_workspace_is_strictly_scoped_to_selected_legacy(test_context):
    client, _sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="scoped-workspace@example.com")

    first_a = _conversation(client, auth)
    legacy_a = first_a["legacy_id"]
    second_a = _conversation(client, auth, legacy_a)

    setup = client.post("/api/v1/legacies/setup", headers=_headers(auth))
    assert setup.status_code == 201
    legacy_b = setup.json()["legacy"]["id"]
    first_b = _conversation(client, auth, legacy_b)
    second_b = _conversation(client, auth, legacy_b)

    listed_a = client.get(
        f"/api/v1/conversations?legacy_id={legacy_a}", headers=_headers(auth)
    )
    listed_b = client.get(
        f"/api/v1/conversations?legacy_id={legacy_b}", headers=_headers(auth)
    )
    assert listed_a.status_code == listed_b.status_code == 200
    assert {item["id"] for item in listed_a.json()} == {first_a["id"], second_a["id"]}
    assert {item["id"] for item in listed_b.json()} == {first_b["id"], second_b["id"]}

    created_for_a = _conversation(client, auth, legacy_a)
    assert created_for_a["legacy_id"] == legacy_a
    context_after_create = client.get("/api/v1/legacies", headers=_headers(auth)).json()
    assert context_after_create["active_legacy_id"] == legacy_b
    sent_to_a = client.post(
        f"/api/v1/conversations/{created_for_a['id']}/messages?legacy_id={legacy_a}",
        json={"content": "Keep this inside Legacy A."},
        headers=_headers(auth),
    )
    assert sent_to_a.status_code == 201
    context_after_chat = client.get("/api/v1/legacies", headers=_headers(auth)).json()
    assert context_after_chat["active_legacy_id"] == legacy_b
    mismatched_history = client.get(
        f"/api/v1/conversations/{created_for_a['id']}/messages?legacy_id={legacy_b}",
        headers=_headers(auth),
    )
    assert mismatched_history.status_code == 409
    assert mismatched_history.json()["detail"]["code"] == "legacy_mismatch"
    mismatched_rename = client.patch(
        f"/api/v1/conversations/{created_for_a['id']}?legacy_id={legacy_b}",
        json={"title": "Wrong workspace"},
        headers=_headers(auth),
    )
    assert mismatched_rename.status_code == 409
    assert mismatched_rename.json()["detail"]["code"] == "legacy_mismatch"
    mismatched_delete = client.delete(
        f"/api/v1/conversations/{created_for_a['id']}?legacy_id={legacy_b}",
        headers=_headers(auth),
    )
    assert mismatched_delete.status_code == 409
    assert mismatched_delete.json()["detail"]["code"] == "legacy_mismatch"

    scoped_stream = client.post(
        f"/api/v1/conversations/{second_b['id']}/messages/stream?legacy_id={legacy_b}",
        json={"content": "Stream inside Legacy B."},
        headers=_headers(auth),
    )
    assert scoped_stream.status_code == 200
    assert "event: done" in scoped_stream.text
    assert "event: error" not in scoped_stream.text

    mismatched_stream = client.post(
        f"/api/v1/conversations/{second_b['id']}/messages/stream?legacy_id={legacy_a}",
        json={"content": "Wrong Legacy."},
        headers=_headers(auth),
    )
    assert mismatched_stream.status_code == 409
    assert mismatched_stream.json()["detail"]["code"] == "legacy_mismatch"

    other = register_user(client, codes, email="scoped-workspace-other@example.com")
    assert client.get(
        f"/api/v1/conversations?legacy_id={legacy_a}", headers=_headers(other)
    ).status_code == 404


def test_rya_identity_explicitly_forbids_legacy_impersonation():
    assert "You remain Rya" in RYA_SYSTEM_PROMPT
    assert "never speak as, impersonate" in RYA_SYSTEM_PROMPT
    assert "relevant long-term memory context" in RYA_SYSTEM_PROMPT

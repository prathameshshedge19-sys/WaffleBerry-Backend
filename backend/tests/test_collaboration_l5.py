from sqlalchemy import func, select

from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.conversation import Conversation
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryRevision, MemoryStatus
from app.services.memory import CanonicalEdit, MemoryAnalysis, MemoryCandidate, MemoryOperation
from tests.conftest import register_user


def headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def owner_legacy(client, sessions, auth, name="Pallavi"):
    response = client.post("/api/v1/conversations", json={"title": "Owner private chat"}, headers=headers(auth))
    assert response.status_code == 201, response.text
    conversation = response.json()
    with sessions() as db:
        legacy = db.get(Legacy, conversation["legacy_id"])
        legacy.subject_name = name
        legacy.relationship_to_owner = "mother"
        legacy.is_self = False
        legacy.setup_status = "active"
        db.commit()
    return conversation


def generate_code(client, auth, legacy_id):
    response = client.post(f"/api/v1/collaborations/legacies/{legacy_id}/code", headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()["code"]


def join(client, auth, code):
    preview = client.post("/api/v1/collaborations/preview", json={"code": code.lower().replace("-", " ")}, headers=headers(auth))
    assert preview.status_code == 200, preview.text
    response = client.post("/api/v1/collaborations/join", json={"code": code}, headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()


def stream(client, auth, conversation, content):
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages/stream?legacy_id={conversation['legacy_id']}",
        json={"content": content}, headers=headers(auth),
    )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text and "event: error" not in response.text


def test_code_is_hashed_encrypted_normalized_reusable_and_regeneration_preserves_members(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l5-owner@example.com", name="Prathamesh")
    owner_chat = owner_legacy(client, sessions, owner)
    legacy_id = owner_chat["legacy_id"]
    code = generate_code(client, owner, legacy_id)
    assert code.startswith("COL-") and len(code) == 13
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        assert legacy.collaborator_code_digest and code not in legacy.collaborator_code_digest
        assert legacy.collaborator_code_ciphertext and code not in legacy.collaborator_code_ciphertext

    arya = register_user(client, codes, email="l5-arya@example.com", name="Arya")
    cousin = register_user(client, codes, email="l5-cousin@example.com", name="Cousin")
    assert join(client, arya, code)["access_role"] == "collaborator"
    assert join(client, cousin, code)["legacy_id"] == legacy_id
    assert join(client, arya, code)["access_role"] == "collaborator"
    with sessions() as db:
        assert db.scalar(select(func.count(LegacyCollaborator.id))) == 2

    owner_result = join(client, owner, code)
    assert owner_result["access_role"] == "owner"
    with sessions() as db:
        assert db.scalar(select(func.count(LegacyCollaborator.id))) == 2

    new_code = generate_code(client, owner, legacy_id)
    assert new_code != code
    assert client.post("/api/v1/collaborations/preview", json={"code": code}, headers=headers(cousin)).status_code == 404
    assert client.post("/api/v1/collaborations/preview", json={"code": new_code}, headers=headers(cousin)).status_code == 200
    with sessions() as db:
        assert db.scalar(select(func.count(LegacyCollaborator.id)).where(LegacyCollaborator.status == CollaboratorStatus.ACTIVE.value)) == 2


def test_invalid_code_is_generic_and_owner_endpoints_are_forbidden_to_collaborator(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l5-security-owner@example.com")
    legacy_id = owner_legacy(client, sessions, owner)["legacy_id"]
    code = generate_code(client, owner, legacy_id)
    arya = register_user(client, codes, email="l5-security-arya@example.com")
    invalid = client.post("/api/v1/collaborations/preview", json={"code": "COL-AAAA-BBBB"}, headers=headers(arya))
    assert invalid.status_code == 404 and invalid.json()["detail"] == "That collaborator code isn't valid."
    join(client, arya, code)
    assert client.get(f"/api/v1/collaborations/legacies/{legacy_id}", headers=headers(arya)).status_code == 404
    assert client.post(f"/api/v1/collaborations/legacies/{legacy_id}/code", headers=headers(arya)).status_code == 404
    assert client.delete(f"/api/v1/collaborations/legacies/{legacy_id}/code", headers=headers(arya)).status_code == 404


def test_collaborator_conversations_are_private_while_legacy_memory_is_shared_with_provenance(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l5-private-owner@example.com", name="Prathamesh")
    owner_chat = owner_legacy(client, sessions, owner)
    legacy_id = owner_chat["legacy_id"]
    arya = register_user(client, codes, email="l5-private-arya@example.com", name="Arya")
    join(client, arya, generate_code(client, owner, legacy_id))
    created = client.post("/api/v1/conversations", json={"title": "Cooking memories", "legacy_id": legacy_id}, headers=headers(arya))
    assert created.status_code == 201 and created.json()["mode"] == "rya"
    arya_chat = created.json()

    owner_list = client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=headers(owner)).json()
    arya_list = client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=headers(arya)).json()
    assert [item["id"] for item in owner_list] == [owner_chat["id"]]
    assert [item["id"] for item in arya_list] == [arya_chat["id"]]
    assert client.get(f"/api/v1/conversations/{owner_chat['id']}/messages?legacy_id={legacy_id}", headers=headers(arya)).status_code == 404

    contribution = "She always used to sing old Hindi songs while cooking."
    provider.memory_provider.analyses[contribution] = MemoryAnalysis(
        source_language="english", normalized_query="Pallavi singing while cooking",
        memories=[MemoryCandidate(canonical_text="Pallavi sang old Hindi songs while cooking.", category="habit", confidence=.98)],
    )
    stream(client, arya, arya_chat, contribution)
    owner_memories = client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(owner)).json()
    assert owner_memories[0]["contributor_name"] == "Arya"
    assert owner_memories[0]["contributor_user_id"] == arya["user"]["id"]
    assert owner_memories[0]["source_conversation_id"] == arya_chat["id"]
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(arya)).json()[0]["canonical_text"] == "Pallavi sang old Hindi songs while cooking."
    prompt = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system")
    assert "COLLABORATOR BUILDER CONTEXT" in prompt


def test_collaborator_correction_and_dashboard_edit_preserve_full_provenance(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l5-correction-owner@example.com", name="Prathamesh")
    owner_chat = owner_legacy(client, sessions, owner)
    legacy_id = owner_chat["legacy_id"]
    original = "Pallavi studied in Pune."
    provider.memory_provider.analyses[original] = MemoryAnalysis(source_language="english", normalized_query=original, memories=[MemoryCandidate(canonical_text=original, category="education", confidence=.95)])
    stream(client, owner, owner_chat, original)
    with sessions() as db:
        old_id = db.scalar(select(Memory.id))

    arya = register_user(client, codes, email="l5-correction-arya@example.com", name="Arya")
    join(client, arya, generate_code(client, owner, legacy_id))
    arya_chat = client.post("/api/v1/conversations", json={"title": "Correction", "legacy_id": legacy_id}, headers=headers(arya)).json()
    correction = "No, she studied in Mumbai."
    provider.memory_provider.analyses[correction] = MemoryAnalysis(source_language="english", normalized_query="Pallavi studied in Mumbai", memories=[MemoryCandidate(canonical_text="Pallavi studied in Mumbai.", category="education", confidence=.99, operation=MemoryOperation.CORRECT, related_memory_ids=[old_id])])
    stream(client, arya, arya_chat, correction)
    with sessions() as db:
        old = db.get(Memory, old_id)
        current = db.scalar(select(Memory).where(Memory.status == MemoryStatus.ACTIVE))
        revision = db.scalar(select(MemoryRevision).where(MemoryRevision.memory_id == old_id))
        assert old.contributor_user_id == owner["user"]["id"] and old.last_contributor_user_id == arya["user"]["id"]
        assert current.contributor_user_id == arya["user"]["id"]
        assert revision.changed_by_user_id == arya["user"]["id"]
        assert revision.source_conversation_id == arya_chat["id"] and revision.source_message_id is not None
        current_id = current.id

    provider.memory_provider.canonical_edits["Pallavi graduated in Mumbai."] = CanonicalEdit(canonical_text="Pallavi graduated in Mumbai.", source_language="english", entities=[])
    edited = client.patch(f"/api/v1/memories/{current_id}?legacy_id={legacy_id}", json={"canonical_text": "Pallavi graduated in Mumbai."}, headers=headers(owner))
    assert edited.status_code == 200 and edited.json()["last_contributor_name"] == "Prathamesh"
    with sessions() as db:
        revisions = db.scalars(select(MemoryRevision).where(MemoryRevision.memory_id == current_id)).all()
        assert revisions[-1].changed_by_user_id == owner["user"]["id"]


def test_collaborator_can_edit_but_not_delete_and_revocation_blocks_all_builder_access(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l5-revoke-owner@example.com", name="Owner")
    owner_chat = owner_legacy(client, sessions, owner)
    legacy_id = owner_chat["legacy_id"]
    provider.memory_provider.analyses["She loves jasmine."] = MemoryAnalysis(source_language="english", normalized_query="jasmine", memories=[MemoryCandidate(canonical_text="Pallavi loves jasmine.", category="preference", confidence=.95)])
    stream(client, owner, owner_chat, "She loves jasmine.")
    memory_id = client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(owner)).json()[0]["id"]

    arya = register_user(client, codes, email="l5-revoke-arya@example.com", name="Arya")
    code = generate_code(client, owner, legacy_id)
    join(client, arya, code)
    provider.memory_provider.canonical_edits["Pallavi loved jasmine flowers."] = CanonicalEdit(canonical_text="Pallavi loved jasmine flowers.", source_language="english", entities=[])
    assert client.patch(f"/api/v1/memories/{memory_id}?legacy_id={legacy_id}", json={"canonical_text": "Pallavi loved jasmine flowers."}, headers=headers(arya)).status_code == 200
    assert client.delete(f"/api/v1/memories/{memory_id}?legacy_id={legacy_id}", headers=headers(arya)).status_code == 403
    panel = client.get(f"/api/v1/collaborations/legacies/{legacy_id}", headers=headers(owner)).json()
    membership_id = panel["collaborators"][0]["membership_id"]
    assert client.delete(f"/api/v1/collaborations/legacies/{legacy_id}/members/{membership_id}", headers=headers(owner)).status_code == 204
    assert client.post("/api/v1/conversations", json={"title": "Blocked", "legacy_id": legacy_id}, headers=headers(arya)).status_code == 404
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(arya)).status_code == 404
    assert client.post("/api/v1/collaborations/join", json={"code": code}, headers=headers(arya)).status_code == 403
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(owner)).status_code == 200
    assert client.post(f"/api/v1/collaborations/legacies/{legacy_id}/members/{membership_id}/restore", headers=headers(owner)).status_code == 200
    assert client.post("/api/v1/conversations", json={"title": "Restored", "legacy_id": legacy_id}, headers=headers(arya)).status_code == 201


def test_collaboration_list_is_separate_unrelated_legacy_is_hidden_and_multilingual_is_canonical(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l5-list-owner@example.com", name="Prathamesh")
    owner_chat = owner_legacy(client, sessions, owner, "Pallavi")
    legacy_id = owner_chat["legacy_id"]
    arya = register_user(client, codes, email="l5-list-arya@example.com", name="Arya")
    join(client, arya, generate_code(client, owner, legacy_id))
    context = client.get("/api/v1/legacies", headers=headers(arya)).json()
    assert context["owned_legacies"] == []
    assert len(context["collaborations"]) == 1 and context["collaborations"][0]["access_role"] == "collaborator"
    stranger = register_user(client, codes, email="l5-stranger@example.com")
    assert client.get(f"/api/v1/legacies/{legacy_id}", headers=headers(stranger)).status_code == 404
    assert client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=headers(stranger)).status_code == 404

    conversation = client.post("/api/v1/conversations", json={"title": "Marathi", "legacy_id": legacy_id}, headers=headers(arya)).json()
    source = "Ti swayampak kartana juni Hindi gani gaychi."
    provider.memory_provider.analyses[source] = MemoryAnalysis(source_language="romanized marathi", normalized_query="Pallavi singing", memories=[MemoryCandidate(canonical_text="Pallavi sang old Hindi songs while cooking.", category="habit", confidence=.97)])
    stream(client, arya, conversation, source)
    memory = client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(owner)).json()[0]
    assert memory["canonical_text"] == "Pallavi sang old Hindi songs while cooking."
    assert memory["source_language"] == "romanized marathi" and memory["contributor_name"] == "Arya"

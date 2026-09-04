from sqlalchemy import func, select

from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink, MemoryStatus
from app.models.viewer import LegacyViewerAccess
from app.services.collaboration import rotate_code
from tests.conftest import register_user


def headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def active_legacy(client, sessions, auth, name="Pallavi"):
    conversation = client.post("/api/v1/conversations", json={"title": "Owner builder"}, headers=headers(auth)).json()
    with sessions() as db:
        legacy = db.get(Legacy, conversation["legacy_id"])
        legacy.subject_name = name; legacy.relationship_to_owner = "mother"; legacy.is_self = False; legacy.setup_status = "active"
        db.commit()
    return conversation


def generate_legacy_code(client, auth, legacy_id):
    response = client.post(f"/api/v1/legacy-access/legacies/{legacy_id}/code", headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()["code"]


def grant(client, auth, code):
    preview = client.post("/api/v1/legacy-access/preview", json={"code": code.lower().replace("-", " ")}, headers=headers(auth))
    assert preview.status_code == 200, preview.text
    joined = client.post("/api/v1/legacy-access/join", json={"code": code}, headers=headers(auth))
    assert joined.status_code == 200, joined.text
    return joined.json()


def add_memory(sessions, legacy_id, text, category="other", status="active", vector=None, contributor_id=None):
    with sessions() as db:
        memory = Memory(
            legacy_id=legacy_id, canonical_text=text, category=category, subject_reference="Pallavi",
            source_language="english", source_excerpt=text, confidence=.98, status=status,
            operation_type="new", explicit_save=False, normalized_fingerprint=(str(abs(hash(text))) * 64)[:64],
            embedding=vector or [1.0, 0.0, 0.0, 0.0], embedding_model="fake-multilingual-embedding",
            embedding_version="test-v1", embedding_dimensions=4, contributor_user_id=contributor_id,
            last_contributor_user_id=contributor_id,
        )
        db.add(memory); db.commit(); db.refresh(memory); return memory.id


def create_visitor_chat(client, auth, legacy_id, title="Visitor chat"):
    response = client.post("/api/v1/legacy-conversations", json={"title": title, "legacy_id": legacy_id, "mode": "legacy"}, headers=headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


def persona_stream(client, auth, conversation, content):
    response = client.post(f"/api/v1/legacy-conversations/{conversation['id']}/messages/stream?legacy_id={conversation['legacy_id']}", json={"content": content}, headers=headers(auth))
    assert response.status_code == 200, response.text
    assert "event: done" in response.text and '"memories_saved": 0' in response.text
    return response


def test_legacy_code_is_separate_secure_reusable_duplicate_safe_and_rotation_preserves_access(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l6-owner@example.com", name="Prathamesh")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    with sessions() as db:
        collaborator_code = rotate_code(db, db.get(Legacy, legacy_id))
    viewer_code = generate_legacy_code(client, owner, legacy_id)
    assert viewer_code.startswith("LEG-") and collaborator_code.startswith("COL-") and viewer_code != collaborator_code
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        assert viewer_code not in legacy.viewer_code_digest and viewer_code not in legacy.viewer_code_ciphertext

    first = register_user(client, codes, email="l6-viewer-one@example.com", name="Friend One")
    second = register_user(client, codes, email="l6-viewer-two@example.com", name="Friend Two")
    grant(client, first, viewer_code); grant(client, second, viewer_code); grant(client, first, viewer_code)
    with sessions() as db: assert db.scalar(select(func.count(LegacyViewerAccess.id))) == 2
    new_code = generate_legacy_code(client, owner, legacy_id)
    assert new_code != viewer_code
    assert client.post("/api/v1/legacy-access/preview", json={"code": viewer_code}, headers=headers(first)).status_code == 404
    assert client.post("/api/v1/legacy-access/preview", json={"code": new_code}, headers=headers(first)).status_code == 200
    assert client.get(f"/api/v1/legacy-access/{legacy_id}/identity", headers=headers(first)).status_code == 200
    assert client.delete(f"/api/v1/legacy-access/legacies/{legacy_id}/code", headers=headers(owner)).status_code == 204
    third = register_user(client, codes, email="l6-viewer-three@example.com")
    assert client.post("/api/v1/legacy-access/preview", json={"code": new_code}, headers=headers(third)).status_code == 404
    assert client.get(f"/api/v1/legacy-access/{legacy_id}/identity", headers=headers(second)).status_code == 200


def test_visitor_history_is_private_by_user_legacy_and_mode(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l6-private-owner@example.com")
    owner_chat = active_legacy(client, sessions, owner); legacy_id = owner_chat["legacy_id"]
    visitor_one = register_user(client, codes, email="l6-private-one@example.com")
    visitor_two = register_user(client, codes, email="l6-private-two@example.com")
    code = generate_legacy_code(client, owner, legacy_id); grant(client, visitor_one, code); grant(client, visitor_two, code)
    first_chat = create_visitor_chat(client, visitor_one, legacy_id, "My Pallavi chat")
    second_chat = create_visitor_chat(client, visitor_two, legacy_id, "Another Pallavi chat")
    assert first_chat["mode"] == "legacy" and second_chat["mode"] == "legacy"
    assert [item["id"] for item in client.get(f"/api/v1/legacy-conversations?legacy_id={legacy_id}", headers=headers(visitor_one)).json()] == [first_chat["id"]]
    assert client.get(f"/api/v1/legacy-conversations/{second_chat['id']}/messages?legacy_id={legacy_id}", headers=headers(visitor_one)).status_code == 404
    assert client.get(f"/api/v1/legacy-conversations/{owner_chat['id']}/messages?legacy_id={legacy_id}", headers=headers(visitor_one)).status_code == 404
    assert client.get(f"/api/v1/conversations/{first_chat['id']}/messages?legacy_id={legacy_id}", headers=headers(visitor_one)).status_code == 404
    assert [item["id"] for item in client.get(f"/api/v1/conversations?legacy_id={legacy_id}", headers=headers(owner)).json()] == [owner_chat["id"]]


def test_legacy_mode_is_strictly_read_only_even_for_remember_and_correction(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l6-readonly-owner@example.com", name="Owner")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    original_id = add_memory(sessions, legacy_id, "Pallavi loves jasmine flowers.", "preference", contributor_id=owner["user"]["id"])
    visitor = register_user(client, codes, email="l6-readonly-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    chat = create_visitor_chat(client, visitor, legacy_id)
    analysis_calls = len(provider.memory_provider.analysis_calls)
    before_embeddings = len(provider.memory_provider.embedding_calls)
    persona_stream(client, visitor, chat, "Remember this: your favorite color is blue.")
    persona_stream(client, visitor, chat, "Correction: you hate jasmine now.")
    assert len(provider.memory_provider.analysis_calls) == analysis_calls
    assert len(provider.memory_provider.embedding_calls) == before_embeddings + 2
    with sessions() as db:
        memories = db.scalars(select(Memory)).all()
        assert len(memories) == 1 and memories[0].id == original_id and memories[0].canonical_text == "Pallavi loves jasmine flowers."
        assert db.scalar(select(func.count(Message.id)).where(Message.conversation_id == chat["id"])) == 4
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(visitor)).status_code == 404
    assert client.patch(f"/api/v1/memories/{original_id}?legacy_id={legacy_id}", json={"canonical_text": "Changed"}, headers=headers(visitor)).status_code == 404
    assert client.delete(f"/api/v1/memories/{original_id}?legacy_id={legacy_id}", headers=headers(visitor)).status_code == 404


def test_persona_prompt_routes_personal_missing_general_mixed_relationship_and_language(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l6-persona-owner@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    add_memory(sessions, legacy_id, "Pallavi loves jasmine flowers.", "preference", vector=[1, 0, 0, 0])
    add_memory(sessions, legacy_id, "Rajesh is Pallavi's husband.", "relationship", vector=[0, 0, 0, 1])
    met_id = add_memory(sessions, legacy_id, "Pallavi met her future husband at KJ College.", "story", vector=[0, 0, 0, 1])
    add_memory(sessions, legacy_id, "Pallavi studied in Pune.", "education", status="superseded", vector=[0, 0, 0, 1])
    visitor = register_user(client, codes, email="l6-persona-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id)); chat = create_visitor_chat(client, visitor, legacy_id)
    responses = {
        "What flowers do you like?": "I love jasmine flowers.",
        "Where did you meet Rajesh?": "I met Rajesh at KJ College.",
        "What was your Kashmir trip like?": "I don't remember a Kashmir trip clearly, but it is known for beautiful valleys and lakes.",
        "Describe a mango.": "A mango is a sweet tropical fruit.",
        "You loved mangoes. Why are mangoes popular in India?": "I don't have a preserved mango preference, but mangoes are popular for their flavor and cultural importance.",
        "Tumhala konti phule avadtat?": "Mala jasmine chi phule avadtat.",
        "Are you ChatGPT?": "I'm Pallavi's AI Legacy here in LegaRya, built from what's been preserved about me.",
    }
    provider.persona_provider.responses.update(responses)
    for question, answer in responses.items():
        result = persona_stream(client, visitor, chat, question)
        midpoint = max(1, len(answer) // 2)
        assert answer[:midpoint] in result.text and answer[midpoint:] in result.text
        system = provider.persona_provider.calls[-1][0].content
        assert "AUTHORITATIVE READ-ONLY CONTRACT" in system and "normal general knowledge" in system
        assert "Never call yourself Rya, ChatGPT, OpenAI" in system and "STRICT READ-ONLY" in system
    relationship_prompt = next(call[0].content for call in provider.persona_provider.calls if any(turn.content == "Where did you meet Rajesh?" for turn in call))
    assert "Rajesh is Pallavi's husband" in relationship_prompt and "KJ College" in relationship_prompt
    assert "Pallavi studied in Pune" not in "\n".join(call[0].content for call in provider.persona_provider.calls)
    assert "Yes, I'm ChatGPT" not in responses["Are you ChatGPT?"]


def test_owner_edit_is_immediate_source_of_truth_and_collaborator_builder_stays_separate(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l6-edit-owner@example.com")
    owner_chat = active_legacy(client, sessions, owner); legacy_id = owner_chat["legacy_id"]
    memory_id = add_memory(sessions, legacy_id, "Pallavi loves jasmine flowers.", "preference")
    collaborator = register_user(client, codes, email="l6-edit-collab@example.com")
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id); db.add(LegacyCollaborator(legacy_id=legacy_id, user_id=collaborator["user"]["id"], added_by_user_id=owner["user"]["id"])); db.commit()
    builder = client.post("/api/v1/conversations", json={"title": "Collaborator builder", "legacy_id": legacy_id}, headers=headers(collaborator))
    assert builder.status_code == 201 and builder.json()["mode"] == "rya"

    visitor = register_user(client, codes, email="l6-edit-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id)); visitor_chat = create_visitor_chat(client, visitor, legacy_id)
    provider.memory_provider.canonical_edits["Pallavi loves marigold flowers."] = __import__("app.services.memory", fromlist=["CanonicalEdit"]).CanonicalEdit(canonical_text="Pallavi loves marigold flowers.", source_language="english", entities=[])
    edited = client.patch(f"/api/v1/memories/{memory_id}?legacy_id={legacy_id}", json={"canonical_text": "Pallavi loves marigold flowers."}, headers=headers(owner))
    assert edited.status_code == 200
    provider.persona_provider.responses["What flowers do you like now?"] = "I love marigold flowers."
    persona_stream(client, visitor, visitor_chat, "What flowers do you like now?")
    prompt = provider.persona_provider.calls[-1][0].content
    assert "Pallavi loves marigold flowers" in prompt and "Pallavi loves jasmine flowers" not in prompt

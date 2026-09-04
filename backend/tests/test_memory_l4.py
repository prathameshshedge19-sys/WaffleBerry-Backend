from sqlalchemy import func, select

from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink, MemoryRevision, MemoryStatus
from app.services.memory import CanonicalEdit, MemoryAnalysis, MemoryCandidate, MemoryEntityCandidate, MemoryOperation
from tests.conftest import register_user


def headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def named_legacy(client, sessions, auth, name="Pallavi"):
    conversation = client.post("/api/v1/conversations", json={"title": "L4 chat"}, headers=headers(auth)).json()
    with sessions() as db:
        legacy = db.get(Legacy, conversation["legacy_id"])
        legacy.subject_name = name; legacy.relationship_to_owner = "mother"; legacy.is_self = False; legacy.setup_status = "active"; db.commit()
    return conversation


def stream(client, auth, conversation, content):
    response = client.post(f"/api/v1/conversations/{conversation['id']}/messages/stream?legacy_id={conversation['legacy_id']}", json={"content": content}, headers=headers(auth))
    assert response.status_code == 200, response.text
    assert "event: done" in response.text and "event: error" not in response.text
    return response


def entity(name, entity_type="person", role="subject", aliases=()):
    return MemoryEntityCandidate(name=name, entity_type=entity_type, role=role, aliases=list(aliases))


def analysis(query, *candidates, language="english", explicit=False):
    return MemoryAnalysis(source_language=language, normalized_query=query, explicit_save=explicit, memories=list(candidates))


def candidate(text, category="other", operation=MemoryOperation.NEW, related=(), entities=(), confidence=.95, story_key=None):
    return MemoryCandidate(canonical_text=text, category=category, confidence=confidence, operation=operation, related_memory_ids=list(related), entities=list(entities), story_key=story_key)


def test_new_explicit_save_and_progressive_question_guidance(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-new@example.com")
    conversation = named_legacy(client, sessions, auth)
    habit = "My mother used to wake up at 5 every morning."
    provider.memory_provider.analyses[habit] = analysis(habit, candidate("Pallavi woke at 5 every morning.", "habit", entities=[entity("Pallavi", aliases=["my mother", "mom", "aai"])]))
    stream(client, auth, conversation, habit)
    prompt = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system")
    assert "ask exactly one specific contextual follow-up" in prompt
    assert "generic 'tell me more.'" in prompt

    explicit = "Remember this: my mother always called me Babu."
    provider.memory_provider.analyses[explicit] = analysis(
        "Pallavi called the contributor Babu.",
        candidate("Pallavi always called the contributor Babu.", "relationship", MemoryOperation.EXPLICIT_SAVE, confidence=.1),
        explicit=True,
    )
    stream(client, auth, conversation, explicit)
    memories = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(auth)).json()
    saved = next(item for item in memories if "Babu" in item["canonical_text"])
    assert saved["explicit_save"] is True and saved["operation_type"] == "explicit_save"


def test_enrich_updates_in_place_revisions_and_regenerates_embedding(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-enrich@example.com")
    conversation = named_legacy(client, sessions, auth)
    first = "She loves jasmine."
    provider.memory_provider.analyses[first] = analysis(first, candidate("Pallavi loves jasmine.", "preference"))
    stream(client, auth, conversation, first)
    with sessions() as db: memory_id = db.scalar(select(Memory.id))
    richer = "She especially loves night-blooming jasmine."
    provider.memory_provider.analyses[richer] = analysis(richer, candidate("Pallavi especially loves night-blooming jasmine.", "preference", MemoryOperation.ENRICH, [memory_id]))
    stream(client, auth, conversation, richer)
    with sessions() as db:
        active = db.scalars(select(Memory).where(Memory.status == MemoryStatus.ACTIVE)).all()
        assert len(active) == 1 and active[0].id == memory_id
        assert active[0].canonical_text == "Pallavi especially loves night-blooming jasmine."
        revision = db.scalar(select(MemoryRevision))
        assert revision.change_type == "enrich" and revision.previous_text == "Pallavi loves jasmine."
    assert ["Pallavi especially loves night-blooming jasmine."] in provider.memory_provider.embedding_calls


def test_conversational_correction_supersedes_and_source_of_truth_retrieves_only_current(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-correct@example.com")
    first_chat = named_legacy(client, sessions, auth)
    original = "Pallavi studied in Pune."
    provider.memory_provider.analyses[original] = analysis(original, candidate(original, "education"))
    stream(client, auth, first_chat, original)
    with sessions() as db: old_id = db.scalar(select(Memory.id))
    correction = "Actually, she studied in Mumbai, not Pune."
    provider.memory_provider.analyses[correction] = analysis("Pallavi studied in Mumbai.", candidate("Pallavi studied in Mumbai.", "education", MemoryOperation.CORRECT, [old_id]))
    stream(client, auth, first_chat, correction)
    with sessions() as db:
        old = db.get(Memory, old_id)
        current = db.scalar(select(Memory).where(Memory.status == MemoryStatus.ACTIVE))
        assert old.status == "superseded" and old.superseded_by_memory_id == current.id and old.embedding is None
        assert current.canonical_text == "Pallavi studied in Mumbai."
    second = client.post("/api/v1/conversations", json={"title": "Current truth", "legacy_id": first_chat["legacy_id"]}, headers=headers(auth)).json()
    question = "Where did Pallavi study?"
    provider.memory_provider.analyses[question] = analysis(question)
    stream(client, auth, second, question)
    grounding = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system" and "LEGACY MEMORIES" in turn.content)
    assert "Mumbai" in grounding and "Pune" not in grounding
    dashboard = client.get(f"/api/v1/memories?legacy_id={first_chat['legacy_id']}", headers=headers(auth)).json()
    assert [item["canonical_text"] for item in dashboard] == ["Pallavi studied in Mumbai."]


def test_dashboard_edit_regenerates_embedding_and_old_text_cannot_return(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-edit@example.com")
    conversation = named_legacy(client, sessions, auth)
    provider.memory_provider.analyses["Pallavi studied in Pune."] = analysis("Pune", candidate("Pallavi studied in Pune.", "education"))
    stream(client, auth, conversation, "Pallavi studied in Pune.")
    memory = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(auth)).json()[0]
    response = client.patch(f"/api/v1/memories/{memory['id']}?legacy_id={conversation['legacy_id']}", json={"canonical_text": "Pallavi studied in Mumbai."}, headers=headers(auth))
    assert response.status_code == 200 and response.json()["canonical_text"] == "Pallavi studied in Mumbai."
    with sessions() as db:
        updated = db.get(Memory, memory["id"])
        revision = db.scalar(select(MemoryRevision).where(MemoryRevision.memory_id == memory["id"]))
        assert updated.embedding is not None and updated.operation_type == "edit"
        assert revision.source == "dashboard" and revision.previous_text.endswith("Pune.")
    assert provider.memory_provider.embedding_calls[-1] == ["Pallavi studied in Mumbai."]


def test_dashboard_delete_soft_deletes_and_excludes_retrieval(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-delete@example.com")
    conversation = named_legacy(client, sessions, auth)
    text = "Pallavi worked at Acme."
    provider.memory_provider.analyses[text] = analysis(text, candidate(text, "career"))
    stream(client, auth, conversation, text)
    memory = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(auth)).json()[0]
    assert client.delete(f"/api/v1/memories/{memory['id']}?legacy_id={conversation['legacy_id']}", headers=headers(auth)).status_code == 204
    assert client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(auth)).json() == []
    with sessions() as db:
        deleted = db.get(Memory, memory["id"])
        assert deleted.status == "deleted" and deleted.embedding is None
        assert db.scalar(select(MemoryRevision.change_type)) == "delete"


def test_conversational_delete_operation(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-forget@example.com")
    conversation = named_legacy(client, sessions, auth)
    provider.memory_provider.analyses["Her old job was Acme."] = analysis("Acme", candidate("Pallavi worked at Acme.", "career"))
    stream(client, auth, conversation, "Her old job was Acme.")
    with sessions() as db: memory_id = db.scalar(select(Memory.id))
    command = "Forget the memory about her old job."
    provider.memory_provider.analyses[command] = analysis(command, candidate("Pallavi worked at Acme.", "career", MemoryOperation.DELETE, [memory_id]))
    stream(client, auth, conversation, command)
    with sessions() as db: assert db.get(Memory, memory_id).status == "deleted"


def test_entity_alias_linking_and_multi_memory_retrieval(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-links@example.com")
    conversation = named_legacy(client, sessions, auth)
    records = [
        ("She attended KJ College.", candidate("Pallavi attended KJ College.", "education", entities=[entity("Pallavi", aliases=["mother", "aai"]), entity("KJ College", "organization", "school")])),
        ("She met her future husband during college.", candidate("Pallavi met her future husband during college.", "relationship", entities=[entity("Pallavi"), entity("Rajesh", "person", "future_husband", ["her future husband"])])),
        ("Rajesh is her husband.", candidate("Rajesh is Pallavi's husband.", "relationship", entities=[entity("Pallavi"), entity("Rajesh", "person", "husband", ["her husband"])])),
    ]
    for source, item in records:
        provider.memory_provider.analyses[source] = analysis(source, item); stream(client, auth, conversation, source)
    with sessions() as db:
        assert db.scalar(select(func.count(MemoryEntity.id))) >= 3
        assert db.scalar(select(func.count(MemoryEntityLink.id))) >= 6
        pallavi = db.scalar(select(MemoryEntity).where(MemoryEntity.normalized_name == "pallavi"))
        assert "aai" in pallavi.aliases
    question = "Where did she meet Rajesh?"
    provider.memory_provider.analyses[question] = analysis("Where did Pallavi meet Rajesh?")
    stream(client, auth, conversation, question)
    grounding = "\n".join(turn.content for turn in provider.calls[-1] if "BEGIN_LEGACY_MEMORY_DATA" in turn.content)
    assert "KJ College" in grounding and "future husband" in grounding and "Rajesh is Pallavi's husband" in grounding


def test_story_extraction_preserves_components_and_story_link(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-story@example.com")
    conversation = named_legacy(client, sessions, auth)
    source = "My mom met my dad at KJ College during a cultural festival. She helped backstage and he performed."
    common = [entity("Pallavi"), entity("Rajesh", "person", "future_husband"), entity("KJ College", "organization", "place")]
    provider.memory_provider.analyses[source] = analysis(source,
        candidate("Pallavi met Rajesh at KJ College.", "story", entities=common, story_key="kj-cultural-festival"),
        candidate("Pallavi and Rajesh met during a cultural festival.", "story", entities=common, story_key="kj-cultural-festival"),
        candidate("Pallavi helped backstage at the cultural festival.", "story", entities=common, story_key="kj-cultural-festival"),
        candidate("Rajesh performed at the cultural festival.", "story", entities=common, story_key="kj-cultural-festival"),
    )
    stream(client, auth, conversation, source)
    with sessions() as db:
        items = db.scalars(select(Memory).where(Memory.status == MemoryStatus.ACTIVE)).all()
        assert len(items) == 4 and {item.story_key for item in items} == {"kj-cultural-festival"}


def test_owner_only_mutation_and_duplicate_edit_protection(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l4-owner@example.com")
    conversation = named_legacy(client, sessions, owner)
    for text in ("Pallavi likes tea.", "Pallavi likes coffee."):
        provider.memory_provider.analyses[text] = analysis(text, candidate(text, "preference")); stream(client, owner, conversation, text)
    items = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(owner)).json()
    other = register_user(client, codes, email="l4-other@example.com")
    assert client.patch(f"/api/v1/memories/{items[0]['id']}?legacy_id={conversation['legacy_id']}", json={"canonical_text": "Changed"}, headers=headers(other)).status_code == 404
    duplicate = client.patch(f"/api/v1/memories/{items[0]['id']}?legacy_id={conversation['legacy_id']}", json={"canonical_text": items[1]["canonical_text"]}, headers=headers(owner))
    assert duplicate.status_code == 409


def test_multilingual_dashboard_edit_normalizes_to_canonical_english(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="l4-multilingual-edit@example.com")
    conversation = named_legacy(client, sessions, auth)
    original = "Pallavi studied in Pune."
    provider.memory_provider.analyses[original] = analysis(original, candidate(original, "education")); stream(client, auth, conversation, original)
    memory_id = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(auth)).json()[0]["id"]
    marathi = "पल्लवीने मुंबईत शिक्षण घेतले."
    provider.memory_provider.canonical_edits[marathi] = CanonicalEdit(canonical_text="Pallavi studied in Mumbai.", source_language="marathi", entities=[entity("Pallavi")])
    response = client.patch(f"/api/v1/memories/{memory_id}?legacy_id={conversation['legacy_id']}", json={"canonical_text": marathi}, headers=headers(auth))
    assert response.status_code == 200
    assert response.json()["canonical_text"] == "Pallavi studied in Mumbai." and response.json()["source_language"] == "marathi"

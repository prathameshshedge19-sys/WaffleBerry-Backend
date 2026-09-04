import pytest
from sqlalchemy import func, select

from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.services.memory import MemoryAnalysis, MemoryCandidate
from tests.conftest import register_user


def _headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def _named_legacy(client, sessions, auth, name, relationship="mother"):
    conversation = client.post(
        "/api/v1/conversations", json={"title": "Memory chat"}, headers=_headers(auth)
    ).json()
    with sessions() as db:
        legacy = db.get(Legacy, conversation["legacy_id"])
        legacy.subject_name = name
        legacy.relationship_to_owner = relationship
        legacy.is_self = relationship == "self"
        legacy.setup_status = "active"
        db.commit()
    return conversation


def _new_legacy(client, sessions, auth, name, relationship="friend"):
    setup = client.post("/api/v1/legacies/setup", headers=_headers(auth)).json()
    legacy_id = setup["legacy"]["id"]
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        legacy.subject_name = name
        legacy.relationship_to_owner = relationship
        legacy.is_self = False
        legacy.setup_status = "active"
        db.commit()
    return legacy_id


def _analysis(source_language, normalized_query, canonical_text=None, category="preference", confidence=0.93):
    memories = [] if canonical_text is None else [
        MemoryCandidate(canonical_text=canonical_text, category=category, confidence=confidence)
    ]
    return MemoryAnalysis(source_language=source_language, normalized_query=normalized_query, memories=memories)


def _stream(client, auth, conversation_id, legacy_id, content):
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream?legacy_id={legacy_id}",
        json={"content": content},
        headers=_headers(auth),
    )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text
    assert "event: error" not in response.text
    return response


@pytest.mark.parametrize(
    ("source", "language", "canonical"),
    [
        ("My mother loves jasmine.", "english", "Pallavi loves jasmine flowers."),
        ("माझ्या आईला मोगऱ्याची फुले खूप आवडतात.", "marathi", "Pallavi loves jasmine flowers."),
        ("मेरी मम्मी पुणे में पढ़ी थी।", "hindi", "Pallavi studied in Pune."),
        ("Meine Mutter liebt klassische Musik.", "german", "Pallavi loves classical music."),
        ("Majhya aaila mogryachi phula khup avadtat.", "romanized_marathi", "Pallavi loves jasmine flowers."),
        ("Majhi mummy old Hindi songs khup enjoy karte.", "mixed", "Pallavi loves old Hindi songs."),
    ],
)
def test_multilingual_extraction_persists_canonical_english_with_provenance(test_context, source, language, canonical):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email=f"memory-{language}@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    provider.memory_provider.analyses[source] = _analysis(language, canonical, canonical)

    _stream(client, auth, conversation["id"], conversation["legacy_id"], source)

    with sessions() as db:
        memory = db.scalar(select(Memory))
        assert memory.canonical_text == canonical
        assert memory.subject_reference == "Pallavi"
        assert memory.source_language == language
        assert memory.source_excerpt == source
        assert memory.source_conversation_id == conversation["id"]
        assert memory.source_message_id is not None
        assert memory.embedding_model == "fake-multilingual-embedding"
        assert memory.embedding_dimensions == 4


def test_neutral_subject_perspective_and_proper_names_are_preserved(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="perspective@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    source = "My mom met my dad in Fergusson College in Pune."
    canonical = "Pallavi met her future husband at Fergusson College in Pune."
    provider.memory_provider.analyses[source] = _analysis("english", canonical, canonical, "life_event")

    _stream(client, auth, conversation["id"], conversation["legacy_id"], source)
    with sessions() as db:
        assert db.scalar(select(Memory.canonical_text)) == canonical


def test_cross_chat_cross_language_retrieval_and_silent_memory_grounding(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="cross-chat@example.com")
    first = _named_legacy(client, sessions, auth, "Pallavi")
    source = "My mother loves jasmine flowers."
    provider.memory_provider.analyses[source] = _analysis(
        "english", "Pallavi loves jasmine flowers.", "Pallavi loves jasmine flowers."
    )
    _stream(client, auth, first["id"], first["legacy_id"], source)

    second = client.post(
        "/api/v1/conversations",
        json={"title": "Second chat", "legacy_id": first["legacy_id"]},
        headers=_headers(auth),
    ).json()
    question = "Mummy la konti phula avadtat?"
    provider.memory_provider.analyses[question] = _analysis(
        "romanized_marathi", "What flowers does Pallavi like?"
    )
    _stream(client, auth, second["id"], first["legacy_id"], question)

    system_context = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system")
    assert "Pallavi loves jasmine flowers." in system_context
    assert "BEGIN_LEGACY_MEMORY_DATA" in system_context


def test_duplicate_prevention_and_non_memory_messages(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="duplicates@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    first = "My mother loves jasmine."
    repeated = "She really likes jasmine flowers."
    provider.memory_provider.analyses[first] = _analysis(
        "english", "Pallavi likes jasmine.", "Pallavi loves jasmine flowers."
    )
    provider.memory_provider.analyses[repeated] = _analysis(
        "english", "Pallavi likes jasmine flowers.", "Pallavi really likes jasmine flowers."
    )

    _stream(client, auth, conversation["id"], conversation["legacy_id"], first)
    _stream(client, auth, conversation["id"], conversation["legacy_id"], repeated)
    _stream(client, auth, conversation["id"], conversation["legacy_id"], "hi")
    _stream(client, auth, conversation["id"], conversation["legacy_id"], "What is quantum mechanics?")

    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == 1


def test_memory_retrieval_and_api_are_strictly_legacy_and_owner_scoped(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="isolation@example.com")
    first = _named_legacy(client, sessions, auth, "Prathamesh", "self")
    football = "I love football."
    provider.memory_provider.analyses[football] = _analysis(
        "english", "What sport does Prathamesh like?", "Prathamesh loves football."
    )
    _stream(client, auth, first["id"], first["legacy_id"], football)

    pallavi_id = _new_legacy(client, sessions, auth, "Pallavi", "mother")
    second = client.post(
        "/api/v1/conversations",
        json={"title": "Pallavi chat", "legacy_id": pallavi_id},
        headers=_headers(auth),
    ).json()
    music = "Meine Mutter liebt klassische Musik."
    provider.memory_provider.analyses[music] = _analysis(
        "german", "What music does Pallavi like?", "Pallavi loves classical music."
    )
    _stream(client, auth, second["id"], pallavi_id, music)

    question = "What sport do I love?"
    provider.memory_provider.analyses[question] = _analysis("english", question)
    _stream(client, auth, second["id"], pallavi_id, question)
    context = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system")
    assert "Prathamesh loves football" not in context

    pallavi_memories = client.get(
        f"/api/v1/memories?legacy_id={pallavi_id}", headers=_headers(auth)
    )
    assert pallavi_memories.status_code == 200
    assert [item["canonical_text"] for item in pallavi_memories.json()] == ["Pallavi loves classical music."]
    memory_id = pallavi_memories.json()[0]["id"]
    assert client.get(
        f"/api/v1/memories/{memory_id}?legacy_id={first['legacy_id']}", headers=_headers(auth)
    ).status_code == 404

    other = register_user(client, codes, email="memory-other@example.com")
    assert client.get(
        f"/api/v1/memories?legacy_id={pallavi_id}", headers=_headers(other)
    ).status_code == 404


def test_only_user_contributions_are_analyzed_and_assistant_persists_once(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="source-authority@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    provider.stream_chunks = ["Maybe Pallavi ", "enjoyed traveling."]
    user_text = "Tell me a story."

    _stream(client, auth, conversation["id"], conversation["legacy_id"], user_text)

    assert provider.memory_provider.analysis_calls == [(conversation["legacy_id"], user_text)]
    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == 0
        assistant_count = db.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation["id"],
                Message.role == MessageRole.ASSISTANT,
            )
        )
        assert assistant_count == 1


def test_rya_language_contract_remains_current_conversation_language():
    from app.services.rya import RYA_SYSTEM_PROMPT

    assert "current conversational language" in RYA_SYSTEM_PROMPT
    assert "relevant long-term memory context" in RYA_SYSTEM_PROMPT
    assert "do not claim unsupported memories" in RYA_SYSTEM_PROMPT

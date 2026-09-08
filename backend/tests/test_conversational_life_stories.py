"""Stories are ephemeral read-only conversation, not a required published artifact."""
import json

import pytest
from sqlalchemy import func, select

from app.models.legacy import Legacy
from app.models.memory import Memory
from app.services.legacy_intelligence import analyze_legacy_query, life_story_memories
from app.services.legacy_persona import persona_system_context
from tests.conftest import register_user
from tests.test_legacy_intelligence_l7 import memory
from tests.test_legacy_persona_l6 import active_legacy, add_memory, create_visitor_chat, generate_legacy_code, grant, persona_stream


@pytest.mark.parametrize("question", [
    "Tell me your full life story.", "Tell me your story.", "Tell me about your life.",
    "Share your biography.", "Tell me about yourself.", "Tell me Pallavi's full story.",
    "Aapki poori kahani sunao", "Tumchi sampurna goshta sanga",
    "आपकी पूरी कहानी सुनाइए", "तुमची संपूर्ण गोष्ट सांगा", "Erzähl mir deine Lebensgeschichte.",
])
def test_life_story_requests_retrieve_personal_memories_without_dates(question):
    route = analyze_legacy_query(question, "Pallavi")
    assert route.asks_life_story and route.needs_memory
    assert not route.needs_fresh_data and not route.identity_challenge


@pytest.mark.parametrize("question", ["Tell me a bedtime story", "What is a biography?", "Explain the life cycle of a butterfly", "Who are you?", "What is your favourite food?"])
def test_other_questions_do_not_trigger_full_life_coverage(question):
    assert not analyze_legacy_query(question, "Pallavi").asks_life_story


def test_biography_selection_is_bounded_diverse_and_does_not_require_embeddings_dates_or_story_keys():
    records = [memory(i, f"Career detail {i}", "career") for i in range(1, 32)]
    records += [memory(40, "Childhood detail", "childhood"), memory(41, "Family detail", "relationship"), memory(42, "Preference detail", "preference")]
    selected = life_story_memories(records)
    assert len(selected) == 20
    assert {m.category for m in selected} == {"career", "childhood", "relationship", "preference"}
    assert len({m.id for m in selected}) == 20
    assert life_story_memories(list(reversed(records))) == selected
    assert life_story_memories([]) == ()
    assert life_story_memories(records[:2]) == tuple(records[:2])


def test_storytelling_contract_is_grounded_and_all_supplementary_inputs_are_optional():
    from app.services.rya import RYA_SYSTEM_PROMPT
    assert "Dates, timelines, uploads, and complete life coverage are optional" in RYA_SYSTEM_PROMPT
    assert "never require a separate Stories section" in RYA_SYSTEM_PROMPT
    legacy = Legacy(id=1, subject_name="Pallavi")
    prompt = persona_system_context(legacy, [memory(1, "Pallavi enjoyed gardening.", "preference")], analyze_legacy_query("Tell me your life story"))
    for phrase in ("connected, warm first-person prose", "Memories alone are enough", "Timeline information is optional", "never treat database IDs or upload order as chronology", "No timeline, exact dates, source files, prepared chapters, or published Story is required", "both text and live voice", "never new canonical memory", "not invented facts"):
        assert phrase in prompt


def test_visitor_life_story_uses_broad_active_scope_without_story_or_timeline_and_writes_no_facts(test_context):
    from app.models.story import Story
    from app.models.timeline import LifeEvent
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="life-story-owner@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    expected = []
    for i, category in enumerate(("childhood", "education", "career", "relationship", "preference", "habit", "value", "other")):
        text = f"Pallavi shared undated {category} recollection number {i}."
        expected.append(add_memory(sessions, legacy_id, text, category))
    add_memory(sessions, legacy_id, "Obsolete detail must not return.", status="superseded")
    with sessions() as db:
        foreign = Legacy(owner_user_id=owner['user']['id'], subject_name="Other QA subject", setup_status="active")
        db.add(foreign); db.commit(); foreign_id = foreign.id
    add_memory(sessions, foreign_id, "Foreign personal detail must not appear.")
    visitor = register_user(client, codes, email="life-story-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    chat = create_visitor_chat(client, visitor, legacy_id)
    with sessions() as db:
        before = list(db.execute(select(Memory.__table__)))
        assert db.scalar(select(func.count()).select_from(Story)) == 0
        assert db.scalar(select(func.count()).select_from(LifeEvent)) == 0
    embedding_calls = len(provider.memory_provider.embedding_calls)
    persona_stream(client, visitor, chat, "Tell me your full life story.")
    prompt = provider.persona_provider.calls[-1][0].content
    records = json.loads(prompt.split('<BEGIN_ACTIVE_PERSONAL_MEMORY_DATA>')[1].split('<END_ACTIVE_PERSONAL_MEMORY_DATA>')[0])
    assert {record['id'] for record in records} == set(expected)
    assert 'Obsolete detail' not in prompt and 'Foreign personal detail' not in prompt
    assert len(provider.memory_provider.embedding_calls) == embedding_calls
    with sessions() as db:
        assert list(db.execute(select(Memory.__table__))) == before
        assert db.scalar(select(func.count()).select_from(Story)) == 0
        assert db.scalar(select(func.count()).select_from(LifeEvent)) == 0

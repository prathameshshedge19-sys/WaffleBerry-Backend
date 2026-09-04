from types import SimpleNamespace

import pytest

from app.models.legacy import Legacy
from app.services.legacy_intelligence import (
    QueryIntent,
    analyze_legacy_query,
    derived_persona_profile,
    detect_conflicts,
    evidence_level,
    followup_policy,
    intelligence_payload,
    relationship_representation,
    rerank_memories,
    story_representation,
    temporal_representation,
)
from app.services.legacy_persona import persona_system_context
from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryEntityCandidate, progressive_interviewing
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import active_legacy, add_memory, create_visitor_chat, generate_legacy_code, grant, headers, persona_stream


def entity(entity_id, name, aliases=()):
    return SimpleNamespace(id=entity_id, name=name, aliases=list(aliases), entity_type="person")


def link(entity_id, name, role="mentioned", aliases=()):
    value = entity(entity_id, name, aliases)
    return SimpleNamespace(entity_id=entity_id, entity=value, role=role)


def memory(memory_id, text, category="other", confidence=.95, story_key=None, links=()):
    return SimpleNamespace(id=memory_id, canonical_text=text, category=category, confidence=confidence, story_key=story_key, entity_links=list(links))


@pytest.mark.parametrize("question", [
    "What was your childhood home like?",
    "Where did you meet Rajesh?",
    "Tumhala konti phule avadtat?",
    "Aapko kaunsa phool pasand tha?",
    "Was hast du in Berlin studiert?",
])
def test_personal_memory_routing_is_multilingual(question):
    route = analyze_legacy_query(question)
    assert route.intent == QueryIntent.PERSONAL and route.needs_memory and not route.needs_fresh_data


@pytest.mark.parametrize("question", ["What is a mango?", "Explain quantum mechanics.", "Why is the sky blue?", "Was ist eine Gitarre?"])
def test_general_knowledge_does_not_require_memory(question):
    route = analyze_legacy_query(question)
    assert route.intent == QueryIntent.GENERAL and route.needs_general_knowledge and not route.needs_memory


def test_mixed_query_uses_personal_and_general_sources():
    route = analyze_legacy_query("You loved mangoes. Why are mangoes so popular in India?")
    assert route.intent == QueryIntent.MIXED and route.needs_memory and route.needs_general_knowledge


@pytest.mark.parametrize("question", ["Who is the current prime minister?", "What is the weather in Pune today?", "Was ist heute passiert?", "Aaj market mein kya hua?"])
def test_fresh_query_is_flagged_without_web_use(question):
    route = analyze_legacy_query(question)
    assert route.intent == QueryIntent.FRESH and route.needs_fresh_data and not route.needs_general_knowledge


def test_relationship_query_and_alias_resolve_to_memory_route():
    rajesh = link(2, "Rajesh", "husband", ["Dad", "Baba"])
    prathamesh = link(3, "Prathamesh", "son", ["my son"])
    memories = [memory(1, "Rajesh is Pallavi's husband.", "relationship", links=[rajesh]), memory(2, "Prathamesh is Pallavi's son.", "relationship", links=[prathamesh])]
    route = analyze_legacy_query("Who is Baba to Prathamesh?", "Pallavi", memories)
    assert route.needs_memory and route.entities == ["Rajesh", "Prathamesh"] and "relationship" in route.personal_topics


def test_temporal_order_and_safe_derived_year_are_noncanonical():
    memories = [memory(1, "Pallavi started college in 1989.", "education"), memory(2, "Pallavi married Rajesh three years later.", "relationship")]
    timeline = temporal_representation(memories)
    assert timeline[0]["year"] == 1989 and timeline[0]["derived"] is False
    assert timeline[1]["year"] == 1992 and timeline[1]["derived"] is True and timeline[1]["basis_year"] == 1989
    assert all("canonical_text" not in item for item in timeline)


def test_relationship_graph_preserves_roles_and_aliases():
    husband = link(2, "Rajesh", "husband", ["Dad", "Baba"])
    graph = relationship_representation([memory(1, "Rajesh is Pallavi's husband.", "relationship", links=[husband])])
    assert graph[0]["entities"][0] == {"name": "Rajesh", "role": "husband", "aliases": ["Dad", "Baba"]}


def test_story_reconstruction_orders_linked_fragments_only():
    memories = [memory(3, "Rajesh was performing.", "story", story_key="festival"), memory(1, "Pallavi attended KJ College.", "story", story_key="festival"), memory(2, "Pallavi helped backstage.", "story", story_key="festival")]
    story = story_representation(memories)[0]
    assert [item["memory_id"] for item in story["ordered_fragments"]] == [1, 2, 3]
    assert all("weather" not in item["text"].casefold() for item in story["ordered_fragments"])


def test_personality_values_preferences_and_style_are_separate_derived_data():
    active = [memory(1, "Pallavi was warm but direct.", "personality"), memory(2, "Pallavi believed family should eat together.", "value"), memory(3, "Pallavi loved jasmine.", "preference"), memory(4, "Pallavi often used short affectionate phrases.", "habit")]
    profile = derived_persona_profile(active)
    assert profile["personality"] == [active[0].canonical_text]
    assert profile["values_and_beliefs"] == [active[1].canonical_text]
    assert profile["preferences"] == [active[2].canonical_text]
    assert profile["communication_tendencies"] == [active[3].canonical_text]
    assert profile["source_memory_ids"] == [1, 2, 3, 4]


def test_personal_opinion_route_does_not_fabricate_when_profile_is_empty():
    route = analyze_legacy_query("What do you think about Kashmir?")
    payload = intelligence_payload(route, (), ())
    assert route.asks_personal_opinion and payload["evidence_level"] == "none"
    assert payload["derived_persona_profile"]["values_and_beliefs"] == []


def test_preserved_opinion_is_available_to_persona_profile():
    opinion = memory(1, "Pallavi believed Kashmir's natural beauty should be protected.", "opinion")
    route = analyze_legacy_query("What do you think about Kashmir?")
    payload = intelligence_payload(route, (opinion,), (opinion,))
    assert payload["derived_persona_profile"]["values_and_beliefs"] == [opinion.canonical_text]


def test_fact_inference_and_missing_evidence_levels():
    route = analyze_legacy_query("Where did you meet Rajesh?")
    direct = memory(1, "Pallavi met Rajesh at KJ College.", "story", .98)
    weak = memory(2, "Pallavi may have met Rajesh during college.", "story", .55)
    assert evidence_level(route, (direct,)) == "high_direct_or_medium_inference"
    assert evidence_level(route, (weak,)) == "low"
    assert evidence_level(route, ()) == "none"


def test_strong_inference_requires_linked_high_confidence_records():
    pallavi = link(1, "Pallavi", "subject")
    records = [memory(1, "Pallavi studied at KJ College.", "education", story_key="college", links=[pallavi]), memory(2, "Pallavi met her future husband during college.", "relationship", story_key="college", links=[pallavi])]
    assert evidence_level(analyze_legacy_query("Where did you meet your husband?"), records) == "high_or_strong_inference"


def test_reranking_combines_semantic_entity_relationship_and_story_links():
    rajesh = link(2, "Rajesh", "husband")
    memories = [memory(1, "Rajesh is Pallavi's husband.", "relationship", links=[rajesh]), memory(2, "Pallavi met Rajesh at KJ College.", "story", story_key="meeting", links=[rajesh]), memory(3, "Pallavi liked tea.", "preference")]
    route = analyze_legacy_query("Where did you meet Rajesh?", "Pallavi", memories)
    result = rerank_memories(memories, "Where did you meet Rajesh?", {1: .2, 2: .72, 3: .7}, route, 3, .28)
    assert [item.id for item in result][:2] == [2, 1] and 3 not in result


def test_conflicting_active_evidence_is_marked_for_uncertainty():
    subject = link(1, "Pallavi", "subject")
    conflicts = detect_conflicts([memory(1, "Pallavi loves jasmine.", "preference", links=[subject]), memory(2, "Pallavi hates jasmine.", "preference", links=[subject])])
    assert conflicts == [{"memory_ids": [1, 2], "instruction": "Active evidence conflicts; do not choose arbitrarily."}]


def test_progressive_followup_targets_a_specific_gap():
    analysis = MemoryAnalysis(source_language="english", normalized_query="college", memories=[MemoryCandidate(canonical_text="Pallavi met Rajesh in college.", category="relationship", confidence=.98, entities=[MemoryEntityCandidate(name="Rajesh", entity_type="person", role="husband")])])
    policy = followup_policy(analysis, ())
    prompt = progressive_interviewing(analysis, ())
    assert policy["ask"] and "Rajesh" in policy["suggestion"]
    assert "ask exactly one specific contextual follow-up" in prompt and "generic 'tell me more.'" in prompt


def test_question_fatigue_suppresses_consecutive_followup():
    analysis = MemoryAnalysis(source_language="english", normalized_query="habit", memories=[MemoryCandidate(canonical_text="Pallavi sang while cooking.", category="habit", confidence=.95)])
    previous = SimpleNamespace(role="assistant", content="What songs did she sing?")
    policy = followup_policy(analysis, (), (previous,))
    prompt = progressive_interviewing(analysis, (), (previous,))
    assert policy == {"ask": False, "reason": "question_fatigue", "suggestion": None}
    assert "do not ask a follow-up this turn" in prompt


def test_persona_contract_prevents_breaks_fresh_fabrication_and_story_embellishment():
    legacy = Legacy(id=1, owner_user_id=1, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
    route = analyze_legacy_query("What is happening today?")
    prompt = persona_system_context(legacy, (), route, ())
    assert "I'm Pallavi's AI Legacy here in LegaRya" in prompt
    assert "Fresh/current" in prompt and "do not have up-to-date information" in prompt
    assert "Never add dialogue, emotion, weather, dates, or scene details" in prompt
    assert "never print or describe routing json" in prompt.casefold()


def test_persona_profile_recomputes_after_active_memory_edit():
    old = memory(1, "Pallavi was reserved.", "personality")
    new = memory(1, "Pallavi was warm and outgoing.", "personality")
    assert derived_persona_profile((old,))["personality"] != derived_persona_profile((new,))["personality"]


def test_intelligence_payload_is_compact_and_does_not_mutate_sources():
    source = memory(1, "Pallavi started college in 1989.", "education")
    before = source.canonical_text
    payload = intelligence_payload(analyze_legacy_query("When did you start college?"), (source,), (source,))
    assert set(payload) == {"route", "evidence_level", "temporal", "relationships", "stories", "derived_persona_profile", "conflicts"}
    assert source.canonical_text == before


def test_persona_route_controls_retrieval_without_extra_model_calls(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l7-route-owner@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    add_memory(sessions, legacy_id, "Pallavi loves jasmine flowers.", "preference")
    visitor = register_user(client, codes, email="l7-route-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    chat = create_visitor_chat(client, visitor, legacy_id)
    provider.persona_provider.responses.update({
        "Explain quantum mechanics.": "Quantum mechanics describes particles, waves, and probabilities.",
        "What flowers do you like?": "I love jasmine flowers.",
        "Who is the current prime minister?": "I don't have up-to-date information on that right now.",
    })
    before = len(provider.memory_provider.embedding_calls)
    persona_stream(client, visitor, chat, "Explain quantum mechanics.")
    assert len(provider.memory_provider.embedding_calls) == before
    assert '"intent": "general"' in provider.persona_provider.calls[-1][0].content
    persona_stream(client, visitor, chat, "What flowers do you like?")
    assert len(provider.memory_provider.embedding_calls) == before + 1
    assert "Pallavi loves jasmine" in provider.persona_provider.calls[-1][0].content
    persona_stream(client, visitor, chat, "Who is the current prime minister?")
    assert len(provider.memory_provider.embedding_calls) == before + 1
    assert '"needs_fresh_data": true' in provider.persona_provider.calls[-1][0].content
    assert client.get(f"/api/v1/memories?legacy_id={legacy_id}", headers=headers(visitor)).status_code == 404

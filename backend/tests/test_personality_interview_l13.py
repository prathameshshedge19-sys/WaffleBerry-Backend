from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.memory import Memory
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity, DailyPrompt
from app.services.builder_interview import FollowupStrategy, _l13_personality_themes, plan_builder_followup
from app.services.memory import MemoryAnalysis, MemoryCandidate, progressive_interviewing
from app.services.progression import L13_PERSONALITY_QUESTION_TEMPLATES, _l13_daily_personality_text
from tests.conftest import register_user
from tests.test_builder_interview_quality import analysis, memory, messages
from tests.test_collaboration_l5 import generate_code, join
from tests.test_daily_prompt_advisor import _journey, _start
from tests.test_legacy_persona_l6 import active_legacy, create_visitor_chat, generate_legacy_code, grant, headers, persona_stream
from tests.test_memory_l4 import stream


@pytest.mark.parametrize("text,category,expected", [
    ("Pallavi was straightforward.", "personality", "everyday life"),
    ("Studies mattered a lot to Pallavi.", "value", "doing or saying about studies"),
    ("Pallavi was very strict about studies.", "personality", "doing or saying about studies"),
    ("Pallavi always made everyone laugh.", "habit", "made people laugh"),
    ("Pallavi always knew how to calm people down.", "habit", "saying or doing at those times"),
    ("Pallavi rarely got angry.", "personality", "handled frustration"),
    ("Pallavi mixed Marathi and English all the time.", "habit", "words or phrases"),
    ("Pallavi teased Prathamesh a lot.", "habit", "tease Prathamesh about"),
    ('Pallavi always said "Oh my".', "habit", "usually say that"),
    ('Pallavi said "Oh my" whenever she was surprised.', "habit", "the way Pallavi said that"),
    ("Pallavi valued independence.", "value", "doing that showed what mattered"),
])
def test_meaningful_personality_doorways_produce_one_nonleading_followup(text, category, expected):
    plan = plan_builder_followup(analysis(text, category), (), messages(text), subject_name="Pallavi")
    assert plan.should_ask_followup and expected in plan.question
    assert plan.question.count("?") == 1
    assert "and what" not in plan.question.lower()
    assert "because she cared" not in plan.question.lower()
    assert "core values" not in plan.question.lower()


def test_expression_context_already_given_is_not_requested_again_or_translated():
    phrase = "\u0905\u0917\u0902 \u092c\u093e\u0908"
    source = f'She used to say "{phrase}" whenever she was surprised.'
    item = analysis(f'Pallavi said "{phrase}" when surprised.', "habit")
    plan = plan_builder_followup(item, (), messages(source), subject_name="Pallavi")
    assert "the way Pallavi said that" in plan.question
    assert "when" not in plan.question.lower() and "translat" not in plan.question.lower()
    assert phrase in item.memories[0].canonical_text


@pytest.mark.parametrize("text", ["I miss her so much since she passed away.", "I still remember how she sat with me all night when I was sick."])
def test_emotional_contributions_are_allowed_space(text):
    plan = plan_builder_followup(analysis(text, "habit"), (), messages(text), subject_name="Pallavi")
    assert not plan.should_ask_followup and plan.question is None


def test_live_story_beats_personality_gap_and_long_story_keeps_existing_suppression():
    text = "The family sat together on the balcony each evening."
    plan = plan_builder_followup(analysis(text, "family_story", story_key="balcony"), (), messages(text), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.CONTINUE_CURRENT_STORY
    assert "evenings" in plan.question and "humor" not in plan.question
    long_story = " ".join(["She described the crowded festival, the music, the lights, and everyone backstage."] * 7)
    plan = plan_builder_followup(analysis(long_story, "story", story_key="festival"), (), messages(long_story), subject_name="Pallavi")
    assert not plan.should_ask_followup and plan.reason == "substantial_story_needs_space"


def test_existing_ambiguity_is_not_displaced_by_personality_gap():
    text = "Someone was important to Pallavi."
    plan = plan_builder_followup(analysis(text, "relationship"), (), messages(text), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.CLARIFY_AMBIGUITY
    assert plan.question == "Who is the person you mean here?"


@pytest.mark.parametrize("prior", ["What would Pallavi do or say that made people laugh?", "What kind of humor did Pallavi have?"])
def test_recent_theme_and_existing_question_history_suppress_repetition(prior):
    text = "Pallavi always made everyone laugh."
    plan = plan_builder_followup(analysis(text, "habit"), (), messages(text, (prior,)), subject_name="Pallavi")
    assert not plan.should_ask_followup


def test_existing_personality_question_is_not_repeated():
    text = "Pallavi was straightforward."
    first = plan_builder_followup(analysis(text, "personality"), (), messages(text), subject_name="Pallavi")
    second = plan_builder_followup(analysis(text, "personality"), (), messages(text, (first.question,)), subject_name="Pallavi")
    assert not second.should_ask_followup


def test_soft_gap_selection_only_runs_at_existing_natural_transition():
    active = [memory("Pallavi studied in Pune.", category="education")]
    item = MemoryAnalysis(source_language="english", normalized_query="What should we discuss next?", memories=[])
    plan = plan_builder_followup(item, active, messages("What should we discuss next?"), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN
    assert "used to say often" in plan.question
    ordinary = MemoryAnalysis(source_language="english", normalized_query="Hello", memories=[])
    plan = plan_builder_followup(ordinary, active, messages("Hello"), subject_name="Pallavi")
    assert not plan.should_ask_followup


@pytest.mark.parametrize("language,source", [
    ("english", "She spoke very directly."),
    ("marathi", "\u0924\u0940 \u0938\u0930\u0933 \u092c\u094b\u0932\u093e\u092f\u091a\u0940."),
    ("hindi", "\u0935\u0939 \u0938\u0940\u0927\u0940 \u092c\u093e\u0924 \u0915\u0930\u0924\u0940 \u0925\u0940."),
    ("german", "Sie sprach sehr direkt."),
    ("mixed", "Ti always saral bolaychi."),
])
def test_current_language_contract_and_proper_names_are_preserved(language, source):
    item = MemoryAnalysis(source_language=language, normalized_query="Meera Kulkarni was straightforward.", memories=[MemoryCandidate(canonical_text="Meera Kulkarni was straightforward.", category="personality", confidence=.97)])
    prompt = progressive_interviewing(item, (), messages(source), subject_name="Meera Kulkarni", contributor_role="owner")
    assert "Meera Kulkarni" in prompt
    assert "Respond in the language of the latest user message" in prompt
    assert "Never restart onboarding for an active Legacy" in prompt
    assert prompt.count("?") == 1
    assert item.source_language == language


def test_relationship_behavior_uses_only_the_named_person_not_an_invented_role():
    text = "Pallavi teased Prathamesh a lot."
    plan = plan_builder_followup(analysis(text, "habit", entity="Prathamesh"), (), messages(text), subject_name="Pallavi")
    assert "Prathamesh" in plan.question
    assert not any(word in plan.question.casefold() for word in ("son", "husband", "loved", "close"))


def test_daily_personality_bank_has_variety_and_single_nonclinical_questions():
    assert len(L13_PERSONALITY_QUESTION_TEMPLATES) >= 17
    assert len(set(L13_PERSONALITY_QUESTION_TEMPLATES)) == len(L13_PERSONALITY_QUESTION_TEMPLATES)
    combined = " ".join(L13_PERSONALITY_QUESTION_TEMPLATES)
    assert {"humor", "comfort", "expression", "values", "relationship_context"}.issubset(_l13_personality_themes(combined))
    for template in L13_PERSONALITY_QUESTION_TEMPLATES:
        assert template.count("?") == 1 and "{name}" in template
        assert not any(word in template.lower() for word in ("diagnos", "personality score", "core values", "signature-expression data"))


def test_daily_recent_themes_are_avoided_and_scoped_to_legacy(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l13e-daily-themes@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    with sessions.begin() as db:
        for text in ("What could make Pallavi laugh?", "What phrase did Pallavi say often?", "How did Pallavi show anger?"):
            db.add(DailyPrompt(legacy_id=legacy_id, prompt_text=text, category="personality", shown_date=date.today(), status="skipped"))
    with sessions() as db:
        result = _l13_daily_personality_text(db, legacy_id, "personality", "What kind of humor did Pallavi have?")
        assert not _l13_personality_themes(result).intersection({"humor", "expression", "anger"})
        assert "Pallavi" in result and result.count("?") == 1
        assert _l13_daily_personality_text(db, legacy_id, "education", "What kind of humor did Pallavi have?") == "What kind of humor did Pallavi have?"


def test_personality_daily_browsing_and_assistant_seed_create_no_evidence_or_activity(test_context):
    client, sessions, codes, _provider = test_context
    owner = register_user(client, codes, email="l13e-advisor@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    initial = _journey(client, owner, legacy_id)["daily_prompt"]
    with sessions.begin() as db:
        row = db.get(DailyPrompt, initial["id"])
        row.category = "personality"
        row.prompt_text = "What could make Pallavi laugh?"
    seeded = _start(client, owner, legacy_id, initial["id"])
    assert seeded.status_code == 201
    assert seeded.json()["rya_message"]["role"] == "assistant"
    assert seeded.json()["rya_message"]["content"] == "What could make Pallavi laugh?"
    skipped = client.post(f"/api/v1/progress/{legacy_id}/daily-prompt/{initial['id']}/skip?timezone=Europe/Berlin", headers=headers(owner))
    assert skipped.status_code == 200 and skipped.json()["id"] != initial["id"]
    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == 0
        assert db.scalar(select(func.count(LegacyPersonalityProfile.legacy_id))) == 0
        assert db.scalar(select(func.count(BuilderActivity.id))) == 0


def test_collaborator_uses_same_interviewer_and_contribution_pipeline(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l13e-owner@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    collaborator = register_user(client, codes, email="l13e-collaborator@example.com")
    join(client, collaborator, generate_code(client, owner, legacy_id))
    chat = client.post("/api/v1/conversations", json={"title": "Contribution", "legacy_id": legacy_id}, headers=headers(collaborator)).json()
    text = "Pallavi was straightforward."
    provider.memory_provider.analyses[text] = analysis(text, "personality")
    stream(client, collaborator, chat, text)
    prompt = "\n".join(turn.content for turn in provider.calls[-1] if turn.role == "system")
    assert "What did that look like in Pallavi's everyday life?" in prompt
    assert "never recast the contributor as the Legacy subject" in prompt
    with sessions() as db:
        assert db.scalar(select(Memory)).contributor_user_id == collaborator["user"]["id"]
        assert db.get(LegacyPersonalityProfile, legacy_id).build_status == "pending"
    assert client.get(f"/api/v1/conversations/{chat['id']}/messages?legacy_id={legacy_id}", headers=headers(owner)).status_code == 404


def test_visitor_gets_neither_builder_interview_nor_daily_advisor(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l13e-visitor-owner@example.com")
    legacy_id = active_legacy(client, sessions, owner)["legacy_id"]
    visitor = register_user(client, codes, email="l13e-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, legacy_id))
    chat = create_visitor_chat(client, visitor, legacy_id)
    persona_stream(client, visitor, chat, "You were always straightforward.")
    prompt = provider.persona_provider.calls[-1][0].content
    assert "PROGRESSIVE LEGACY INTERVIEWING" not in prompt and "personality_doorway" not in prompt
    assert client.get(f"/api/v1/progress/{legacy_id}", headers=headers(visitor)).status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count(Memory.id))) == 0
        assert db.scalar(select(func.count(LegacyPersonalityProfile.legacy_id))) == 0

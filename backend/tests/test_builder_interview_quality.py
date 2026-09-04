from types import SimpleNamespace

import pytest

from app.models.legacy import Legacy
from app.services.builder_interview import FollowupStrategy, plan_builder_followup
from app.services.legacy_setup import setup_system_context
from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryEntityCandidate, progressive_interviewing


def analysis(text, category, *, entity=None, role="mentioned", story_key=None):
    entities = [] if not entity else [MemoryEntityCandidate(name=entity, entity_type="person", role=role)]
    return MemoryAnalysis(source_language="english", normalized_query=text,
        memories=[MemoryCandidate(canonical_text=text, category=category, confidence=.97, entities=entities, story_key=story_key)])


def messages(user, assistant_questions=()):
    values = [SimpleNamespace(role="assistant", content=value) for value in assistant_questions]
    values.append(SimpleNamespace(role="user", content=user))
    return values


def memory(text, category="relationship", entities=()):
    links = [SimpleNamespace(role=role, entity=SimpleNamespace(name=name)) for name, role in entities]
    return SimpleNamespace(canonical_text=text, category=category, entity_links=links)


def test_spouse_fact_opens_relationship_story_instead_of_repeating_fact():
    item = analysis("Kiran Shedge is Pallavi's husband.", "relationship", entity="Kiran Shedge", role="husband")
    plan = plan_builder_followup(item, (), messages("and Husband is Kiran Shedge"), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.EXPLORE_RELATED_PERSON
    assert plan.question == "How did Pallavi and Kiran Shedge first meet?"


def test_creator_relationship_uses_sons_perspective_without_re_onboarding():
    item = analysis("Prathamesh is Pallavi's son.", "relationship", entity="Prathamesh", role="son")
    plan = plan_builder_followup(item, (), messages("i am her son creating for her", (
        "What was Pallavi's relationship with Prathamesh like in everyday life?",
        "How did Pallavi and Kiran first meet?",
    )), subject_name="Pallavi")
    assert "Since you're Pallavi's son" in plan.question and "like as a mother" in plan.question
    assert "who" not in plan.question.casefold() and "begin" not in plan.question.casefold()


def test_live_story_thread_beats_global_gap_and_advances_scene():
    item = analysis("The family sat together on the balcony each evening.", "family_story", story_key="evening-tea")
    plan = plan_builder_followup(item, (), messages("We all sat on the balcony."), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.CONTINUE_CURRENT_STORY
    assert plan.question == "What do you remember everyone talking about on those evenings?"


def test_known_or_recently_asked_meeting_detail_is_not_requested_again():
    item = analysis("Kiran is Pallavi's husband.", "relationship", entity="Kiran", role="husband")
    active = [memory("Pallavi met Kiran at KJ College.", entities=(("Kiran", "husband"),))]
    recent = messages("Kiran is her husband.", ("What do you know about Pallavi and Kiran's first meeting?",))
    plan = plan_builder_followup(item, active, recent, subject_name="Pallavi")
    assert not plan.should_ask_followup and plan.reason == "recent_question_fatigue"


def test_substantial_story_gets_space_without_a_question():
    text = " ".join(["She described the crowded festival, the music, the lights, and everyone backstage."] * 7)
    plan = plan_builder_followup(analysis(text, "story", story_key="festival"), (), messages(text), subject_name="Pallavi")
    assert not plan.should_ask_followup and plan.reason == "substantial_story_needs_space"


@pytest.mark.parametrize(("text", "category", "expected"), [
    ("She used to make tea every evening.", "habit", "tea evenings"),
    ("She grew up in Dombivli.", "place", "home there"),
    ("She loved old Hindi songs.", "preference", "singer or song"),
    ("She woke up at five every morning.", "habit", "first thing"),
    ("He did impressions of people from television.", "habit", "laugh the hardest"),
])
def test_fact_types_open_specific_human_details(text, category, expected):
    plan = plan_builder_followup(analysis(text, category), (), messages(text), subject_name="Pallavi")
    assert plan.should_ask_followup and expected in plan.question
    assert plan.question.count("?") == 1


def test_education_connects_to_known_spouse_when_unexplored():
    spouse = memory("Kiran is Pallavi's husband.", entities=(("Kiran", "husband"),))
    text = "She studied commerce at KJ College."
    plan = plan_builder_followup(analysis(text, "education"), (spouse,), messages(text), subject_name="Pallavi")
    assert plan.question == "Was that also where Pallavi first met Kiran?"


def test_emotional_contribution_is_not_forced_into_an_interview_question():
    text = "I miss her so much since she passed away."
    plan = plan_builder_followup(analysis(text, "relationship"), (), messages(text), subject_name="Pallavi")
    assert not plan.should_ask_followup and plan.reason == "emotional_content_needs_space"


def test_collaborator_contract_preserves_contributor_perspective():
    item = analysis("Pallavi sang while cooking.", "habit")
    recent = messages("My aunt used to sing while cooking.")
    prompt = progressive_interviewing(item, (), recent, subject_name="Pallavi", contributor_role="collaborator")
    assert "never recast the contributor as the Legacy subject" in prompt
    assert "What songs do you remember hearing her sing?" in prompt


def test_output_contract_allows_only_one_question_and_discourages_repetitive_tone():
    item = analysis("Pallavi loved jasmine.", "preference")
    prompt = progressive_interviewing(item, (), messages("She loved jasmine."), subject_name="Pallavi")
    assert prompt.count("?") == 1
    assert "Avoid emojis" in prompt and "do not habitually say" in prompt


def test_active_legacy_context_explicitly_prevents_beginner_onboarding():
    legacy = Legacy(subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
    prompt = setup_system_context(legacy)
    assert "never return to beginner onboarding" in prompt


def test_new_person_opens_relationship_exploration():
    item = analysis("Meera is Pallavi's closest friend.", "relationship", entity="Meera", role="friend")
    plan = plan_builder_followup(item, (), messages("Her closest friend was Meera."), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.EXPLORE_RELATED_PERSON and "everyday life" in plan.question


def test_ambiguous_person_gets_one_clarifying_question():
    item = analysis("Someone was important to Pallavi.", "relationship")
    plan = plan_builder_followup(item, (), messages("Someone was very important to her."), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.CLARIFY_AMBIGUITY
    assert plan.question == "Who is the person you mean here?"


def test_global_gap_is_used_only_after_natural_thread_transition():
    active = [memory("Pallavi attended KJ College.", category="education")]
    item = MemoryAnalysis(source_language="english", normalized_query="What should we discuss next?", memories=[])
    plan = plan_builder_followup(item, active, messages("What should we discuss next?"), subject_name="Pallavi")
    assert plan.followup_strategy == FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN
    assert plan.question.count("?") == 1

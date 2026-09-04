import re
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field


class FollowupStrategy(str, Enum):
    CONTINUE_CURRENT_STORY = "continue_current_story"
    DEEPEN_NEW_FACT = "deepen_new_fact"
    CLARIFY_AMBIGUITY = "clarify_ambiguity"
    EXPLORE_RELATED_PERSON = "explore_related_person"
    EXPLORE_UNDERDEVELOPED_DOMAIN = "explore_underdeveloped_domain"
    NO_QUESTION = "no_question"


class BuilderFollowupPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    should_ask_followup: bool
    followup_strategy: FollowupStrategy
    target_entities: list[str] = Field(default_factory=list, max_length=8)
    known_context: str = Field(max_length=1800)
    missing_detail: str | None = Field(default=None, max_length=240)
    question: str | None = Field(default=None, max_length=400)
    reason: str = Field(max_length=120)


_STOPWORDS = {"about", "and", "anything", "did", "does", "her", "him", "how", "the", "their", "them", "there", "they", "this", "was", "what", "when", "where", "who", "why", "with", "you", "your"}
_EMOTIONAL = re.compile(r"\b(died|death|passed away|grief|grieving|miss her|miss him|trauma|abuse|terrified|heartbroken)\b", re.I)
_CREATOR_RELATION = re.compile(r"\b(?:i am|i['’]?m)\s+(?:her|his|their)\s+(son|daughter|husband|wife|brother|sister|friend)\b", re.I)
_NARRATIVE = re.compile(r"\b(then|that day|when we|when she|when he|after that|before that|used to|would always|we all|suddenly|eventually)\b", re.I)


def _role(message: Any) -> str:
    value = getattr(message, "role", "")
    return getattr(value, "value", value)


def _question_tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.casefold()) if len(word) > 2 and word not in _STOPWORDS}


def _concept_repeated(question: str, recent_messages: Sequence[Any]) -> bool:
    proposed = _question_tokens(question)
    for message in recent_messages[-16:]:
        content = getattr(message, "content", "")
        if _role(message) != "assistant" or "?" not in content:
            continue
        prior = _question_tokens(content.rsplit("?", 1)[0])
        overlap = proposed & prior
        if len(overlap) >= 2 and len(overlap) / max(1, min(len(proposed), len(prior))) >= .42:
            return True
    return False


def _entity_pairs(candidate: Any) -> list[tuple[str, str]]:
    return [(getattr(item, "name", ""), getattr(item, "role", "")) for item in getattr(candidate, "entities", ()) if getattr(item, "name", "")]


def _memory_entity_pairs(memory: Any) -> list[tuple[str, str]]:
    pairs = []
    for link in getattr(memory, "entity_links", ()):
        entity = getattr(link, "entity", None)
        if entity and getattr(entity, "name", ""):
            pairs.append((entity.name, getattr(link, "role", "")))
    return pairs


def _result(strategy: FollowupStrategy, entities: list[str], known: str, missing: str | None,
            question: str | None, reason: str, recent_messages: Sequence[Any]) -> BuilderFollowupPlan:
    if question and _concept_repeated(question, recent_messages):
        return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION,
            target_entities=entities, known_context=known, reason="recent_question_fatigue")
    return BuilderFollowupPlan(should_ask_followup=bool(question), followup_strategy=strategy,
        target_entities=entities, known_context=known, missing_detail=missing, question=question, reason=reason)


def plan_builder_followup(analysis: Any | None, active_memories: Sequence[Any], recent_messages: Sequence[Any] = (),
                          *, subject_name: str | None = None, contributor_role: str = "owner") -> BuilderFollowupPlan:
    subject = subject_name or "the Legacy subject"
    candidates = list(getattr(analysis, "memories", ()) or ())
    known_texts = [getattr(item, "canonical_text", "") for item in active_memories if getattr(item, "canonical_text", "")]
    known = " | ".join(known_texts[-24:]) or "No earlier canonical memories yet."
    user_texts = [getattr(item, "content", "") for item in recent_messages if _role(item) == "user"]
    current = (user_texts[-1] if user_texts else getattr(analysis, "normalized_query", "") if analysis else "").strip()
    if not candidates:
        if active_memories and re.search(r"\b(what next|where should we continue|what should we discuss|another thread|something else)\b", current, re.I):
            covered = {getattr(item, "category", "") for item in active_memories}
            prompts = (("childhood", f"What is one childhood place that shaped {subject}?"),
                       ("relationship", f"Who had an important influence on {subject}'s life?"),
                       ("habit", f"What everyday habit made {subject} unmistakably themselves?"),
                       ("career", f"What part of {subject}'s working life mattered most to them?"))
            domain, question = next(((name, prompt) for name, prompt in prompts if name not in covered), prompts[0])
            return _result(FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN, [subject], known,
                f"an unexplored {domain} thread", question, "natural_thread_transition", recent_messages)
        return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="no_new_legacy_material")
    if not user_texts and any(_role(item) == "assistant" and "?" in getattr(item, "content", "") for item in recent_messages):
        return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="recent_question_fatigue")
    if _EMOTIONAL.search(current):
        return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="emotional_content_needs_space")
    if len(current.split()) >= 48 or len(re.findall(r"[.!?]+", current)) >= 4:
        return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="substantial_story_needs_space")

    primary = candidates[0]
    category = getattr(primary, "category", "other")
    pairs = _entity_pairs(primary)
    entities = list(dict.fromkeys(name for name, role in pairs if role != "subject" and name.casefold() != subject.casefold()))
    person = entities[0] if entities else None
    lowered, all_known = current.casefold(), " ".join(known_texts).casefold()
    creator = _CREATOR_RELATION.search(current)
    if category == "relationship" and not entities and re.search(r"\b(someone|that person|one of (?:her|his|their) friends)\b", current, re.I):
        return _result(FollowupStrategy.CLARIFY_AMBIGUITY, [], known, "which person the relationship refers to",
            "Who is the person you mean here?", "ambiguous_person_reference", recent_messages)
    if creator:
        relation = creator.group(1).casefold()
        perspective = "mother" if relation == "son" else "father" if relation == "daughter" else relation
        question = f"Since you're {subject}'s {relation}, what is {subject} like as a {perspective}—what is something they did that you still remember clearly?"
        return BuilderFollowupPlan(should_ask_followup=True, followup_strategy=FollowupStrategy.EXPLORE_RELATED_PERSON,
            target_entities=[subject], known_context=known, missing_detail="a lived contributor perspective",
            question=question, reason="creator_relationship")

    story_continues = bool(category in {"story", "family_story", "life_event"} or (getattr(primary, "story_key", None) and category not in {"habit", "routine", "tradition", "preference"}) or (_NARRATIVE.search(current) and category not in {"habit", "routine", "tradition", "preference"}))
    if story_continues:
        if "balcony" in lowered:
            question, missing = "What do you remember everyone talking about on those evenings?", "conversation within the family scene"
        elif re.search(r"\b(joke|joked|joking|funny)\b", lowered):
            question, missing = "What kind of jokes would he make that everyone still remembers?", "a vivid example of the humor"
        elif re.search(r"\b(met|meeting|festival|backstage)\b", lowered):
            question, missing = "What happened when they actually met that day?", "the next moment in the meeting story"
        else:
            question, missing = "What happened next in that moment?", "the next scene in the current story"
        return _result(FollowupStrategy.CONTINUE_CURRENT_STORY, entities, known, missing, question, "live_story_thread", recent_messages)

    relationship_roles = {"husband", "wife", "spouse", "son", "daughter", "mother", "father", "friend", "sibling", "mentor"}
    if category == "relationship" or (category in {"personal_detail", "other"} and any(role in relationship_roles for _, role in pairs)):
        meeting_known = person and any(marker in all_known for marker in (f"met {person.casefold()}", f"{person.casefold()} met", "first met"))
        if meeting_known:
            question, missing = f"What do you know about {subject} and {person}'s first meeting?", "the lived story behind their known meeting"
        elif person and any(role in {"husband", "wife", "spouse"} for _, role in pairs):
            question, missing = f"How did {subject} and {person} first meet?", "how the relationship began"
        elif person:
            question, missing = f"What was {subject}'s relationship with {person} like in everyday life?", "the human texture of the relationship"
        else:
            question, missing = f"What was that relationship like for {subject} in everyday life?", "the human texture of the relationship"
        return _result(FollowupStrategy.EXPLORE_RELATED_PERSON, entities, known, missing, question, "important_person_introduced", recent_messages)

    if category in {"habit", "routine", "tradition"}:
        if "tea" in lowered:
            question, missing = "What made those tea evenings feel distinctly like hers?", "sensory or social detail in the routine"
        elif re.search(r"\b(sing|sang|song)\b", lowered):
            question, missing = "What songs do you remember hearing her sing?", "the sound and specificity of the habit"
        elif re.search(r"\b(impression|imitat|television)\b", lowered):
            question, missing = "Which impression made Pallavi laugh the hardest?", "one vivid example from the current family scene"
        elif re.search(r"\b(woke|wake|morning)\b", lowered):
            question, missing = "What was the first thing she usually did after waking up?", "the next action in the routine"
        else:
            question, missing = "What did that routine look like in everyday life?", "a lived detail within the habit"
        return _result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "habit_to_lived_detail", recent_messages)

    if category in {"place", "childhood"}:
        question = "What was her home there like?" if re.search(r"\b(grew up|childhood|home|lived)\b", lowered) else "What is one scene from that place that still feels vivid?"
        return _result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, "a lived scene connected to the place", question, "place_to_story", recent_messages)

    if category == "education":
        spouse = next((name for memory in active_memories for name, role in _memory_entity_pairs(memory) if role in {"husband", "wife", "spouse"}), None)
        if spouse and "college" in lowered and not any(marker in all_known for marker in (f"met {spouse.casefold()}", "first met")):
            question, missing = f"Was that also where {subject} first met {spouse}?", "whether education connects to the known relationship"
        else:
            question, missing = f"What was {subject} like during that period of study?", "the person behind the education fact"
        return _result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "education_to_experience", recent_messages)

    if category == "career":
        return _result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, "what the work felt like or meant", f"What part of that work mattered most to {subject}?", "career_to_experience", recent_messages)

    if category in {"preference", "dislike"}:
        if re.search(r"\b(song|songs|music|hindi)\b", lowered):
            question, missing = "Was there a singer or song she played especially often?", "a specific musical association"
        elif "jasmine" in lowered:
            question, missing = "Did she keep jasmine flowers at home?", "how the preference appeared in daily life"
        else:
            question, missing = "How did that preference show up in her everyday life?", "behavior connected to the preference"
        return _result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "preference_depth_ladder", recent_messages)

    return BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION,
        target_entities=entities, known_context=known, reason="no_natural_high_value_question")

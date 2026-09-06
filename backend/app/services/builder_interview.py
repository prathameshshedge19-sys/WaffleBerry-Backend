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
            return _l13_personality_followup(_result(FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN, [subject], known,
                f"an unexplored {domain} thread", question, "natural_thread_transition", recent_messages), analysis, active_memories, recent_messages, subject_name)
        return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="no_new_legacy_material"), analysis, active_memories, recent_messages, subject_name)
    if not user_texts and any(_role(item) == "assistant" and "?" in getattr(item, "content", "") for item in recent_messages):
        return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="recent_question_fatigue"), analysis, active_memories, recent_messages, subject_name)
    if _EMOTIONAL.search(current):
        return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="emotional_content_needs_space"), analysis, active_memories, recent_messages, subject_name)
    if len(current.split()) >= 48 or len(re.findall(r"[.!?]+", current)) >= 4:
        return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION, known_context=known, reason="substantial_story_needs_space"), analysis, active_memories, recent_messages, subject_name)

    primary = candidates[0]
    category = getattr(primary, "category", "other")
    pairs = _entity_pairs(primary)
    entities = list(dict.fromkeys(name for name, role in pairs if role != "subject" and name.casefold() != subject.casefold()))
    person = entities[0] if entities else None
    lowered, all_known = current.casefold(), " ".join(known_texts).casefold()
    creator = _CREATOR_RELATION.search(current)
    if category == "relationship" and not entities and re.search(r"\b(someone|that person|one of (?:her|his|their) friends)\b", current, re.I):
        return _l13_personality_followup(_result(FollowupStrategy.CLARIFY_AMBIGUITY, [], known, "which person the relationship refers to",
            "Who is the person you mean here?", "ambiguous_person_reference", recent_messages), analysis, active_memories, recent_messages, subject_name)
    if creator:
        relation = creator.group(1).casefold()
        perspective = "mother" if relation == "son" else "father" if relation == "daughter" else relation
        question = f"Since you're {subject}'s {relation}, what is {subject} like as a {perspective}—what is something they did that you still remember clearly?"
        return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=True, followup_strategy=FollowupStrategy.EXPLORE_RELATED_PERSON,
            target_entities=[subject], known_context=known, missing_detail="a lived contributor perspective",
            question=question, reason="creator_relationship"), analysis, active_memories, recent_messages, subject_name)

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
        return _l13_personality_followup(_result(FollowupStrategy.CONTINUE_CURRENT_STORY, entities, known, missing, question, "live_story_thread", recent_messages), analysis, active_memories, recent_messages, subject_name)

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
        return _l13_personality_followup(_result(FollowupStrategy.EXPLORE_RELATED_PERSON, entities, known, missing, question, "important_person_introduced", recent_messages), analysis, active_memories, recent_messages, subject_name)

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
        return _l13_personality_followup(_result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "habit_to_lived_detail", recent_messages), analysis, active_memories, recent_messages, subject_name)

    if category in {"place", "childhood"}:
        question = "What was her home there like?" if re.search(r"\b(grew up|childhood|home|lived)\b", lowered) else "What is one scene from that place that still feels vivid?"
        return _l13_personality_followup(_result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, "a lived scene connected to the place", question, "place_to_story", recent_messages), analysis, active_memories, recent_messages, subject_name)

    if category == "education":
        spouse = next((name for memory in active_memories for name, role in _memory_entity_pairs(memory) if role in {"husband", "wife", "spouse"}), None)
        if spouse and "college" in lowered and not any(marker in all_known for marker in (f"met {spouse.casefold()}", "first met")):
            question, missing = f"Was that also where {subject} first met {spouse}?", "whether education connects to the known relationship"
        else:
            question, missing = f"What was {subject} like during that period of study?", "the person behind the education fact"
        return _l13_personality_followup(_result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "education_to_experience", recent_messages), analysis, active_memories, recent_messages, subject_name)

    if category == "career":
        return _l13_personality_followup(_result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, "what the work felt like or meant", f"What part of that work mattered most to {subject}?", "career_to_experience", recent_messages), analysis, active_memories, recent_messages, subject_name)

    if category in {"preference", "dislike"}:
        if re.search(r"\b(song|songs|music|hindi)\b", lowered):
            question, missing = "Was there a singer or song she played especially often?", "a specific musical association"
        elif "jasmine" in lowered:
            question, missing = "Did she keep jasmine flowers at home?", "how the preference appeared in daily life"
        else:
            question, missing = "How did that preference show up in her everyday life?", "behavior connected to the preference"
        return _l13_personality_followup(_result(FollowupStrategy.DEEPEN_NEW_FACT, entities, known, missing, question, "preference_depth_ladder", recent_messages), analysis, active_memories, recent_messages, subject_name)

    return _l13_personality_followup(BuilderFollowupPlan(should_ask_followup=False, followup_strategy=FollowupStrategy.NO_QUESTION,
        target_entities=entities, known_context=known, reason="no_natural_high_value_question"), analysis, active_memories, recent_messages, subject_name)


# L13 extends the existing decision, never the request/stream or memory pipeline.
_L13_THEME_PATTERNS = {
    "humor": r"laugh|humou?r|funny|jok|teas|witz|lach|hasav|hasi|\u0939\u0938|\u0939\u0901\u0938",
    "comfort": r"comfort|calm|sooth|upset|troest|tr\u00f6st|beruhig|\u0936\u093e\u0902\u0924|\u0927\u0940\u0930",
    "anger": r"angry|anger|frustrat|w\u00fct|wut|gussa|ragav|\u0930\u093e\u0917|\u0917\u0941\u0938\u094d\u0938",
    "language": r"language|marathi|hindi|english|german|sprach|\u092d\u093e\u0937|\u0907\u0902\u0917\u094d\u0930\u091c",
    "expression": r"phrase|saying|said it|say that|say all|familiar|ausdruck|spruch|\u0935\u093e\u0915\u094d\u092f|\u092e\u094d\u0939\u0923\u093e",
    "values": r"valu|mattered|important|studies|study|education|school|wert|bildung|\u0936\u093f\u0915\u094d\u0937|\u0905\u092d\u094d\u092f\u093e\u0938",
    "affection": r"affection|tender|zuneigung",
    "conflict": r"disagree|conflict|streit",
    "discipline": r"discipline|disziplin",
    "greeting": r"greet|begr\u00fc\u00df",
    "nickname": r"nickname|pet name|spitzname",
    "celebration": r"celebrat|feier",
    "worry": r"worri|worry|fear|sorg|angst",
    "decision": r"decision|decid|entscheid",
    "routine": r"routine|everyday habit|alltag",
    "relationship_context": r"family and strangers|familiar people|fremd",
    "emotional_expression": r"feelings without words|express feelings|gef\u00fchl",
}


def _l13_personality_themes(text):
    return {theme for theme, pattern in _L13_THEME_PATTERNS.items() if re.search(pattern, text, re.I)}


def _l13_personality_followup(plan, analysis, active_memories, recent_turns, subject_name):
    """Fill narrow evidence doorways after existing safeguards have decided."""
    if plan.reason not in {"no_natural_high_value_question", "no_new_legacy_material"} and not plan.should_ask_followup:
        return plan
    if plan.followup_strategy in (FollowupStrategy.CONTINUE_CURRENT_STORY, FollowupStrategy.CLARIFY_AMBIGUITY):
        return plan
    recent_questions = [turn.content for turn in recent_turns if getattr(turn, "role", None) == "assistant" and "?" in turn.content]
    user_text = next((turn.content for turn in reversed(recent_turns) if getattr(turn, "role", None) == "user"), "")
    if re.search(r"\ball night\b", user_text, re.I) and re.search(r"\bsick\b|\bhospital\b", user_text, re.I):
        return plan.model_copy(update={"should_ask_followup": False, "followup_strategy": FollowupStrategy.NO_QUESTION, "question": None, "reason": "emotional_memory_needs_space"})
    subject = subject_name or "this person"
    candidates = list(getattr(analysis, "memories", ()) or ())
    theme = None
    question = None
    if not candidates:
        if plan.followup_strategy != FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN:
            return plan
        # Existing canonical coverage is a soft signal only at a natural transition.
        covered = set().union(*(_l13_personality_themes(item.canonical_text) for item in active_memories)) if active_memories else set()
        gaps = (
            ("expression", f"Was there anything {subject} used to say often?"),
            ("humor", f"What could make {subject} laugh?"),
            ("comfort", f"What do you remember {subject} doing to comfort someone?"),
            ("values", f"What mattered to {subject} in everyday life?"),
        )
        for candidate_theme, candidate_question in gaps:
            if candidate_theme not in covered and not any(candidate_theme in _l13_personality_themes(old) for old in recent_questions):
                theme, question = candidate_theme, candidate_question
                break
    else:
        if any(item.category in {"story", "family_story", "life_event", "tradition"} or getattr(item, "story_key", None) for item in candidates):
            return plan
        text = " ".join(item.canonical_text for item in candidates)
        themes = _l13_personality_themes(text)
        has_quote = bool(re.search(r"[\"\u2018\u201c].+?[\"\u2019\u201d]", user_text + " " + text))
        if has_quote and re.search(r"\bsaid\b|\bsay\b|\bphrase\b|\bmhan\w*|\u092e\u094d\u0939\u0923", text + " " + user_text, re.I):
            if re.search(r"\bnever (?:said|used)\b", text, re.I):
                return plan
            theme = "expression"
            known_usage = bool(re.search(r"\bwhen(?:ever)?\b|\bwhile\b", text + " " + user_text, re.I))
            question = f"What do you remember about the way {subject} said that?" if known_usage else f"When would {subject} usually say that?"
        elif "language" in themes and re.search(r"mix|switch|spoke|speak|used|using", text, re.I):
            theme, question = "expression", f"Are there any words or phrases you especially remember {subject} using?"
        elif re.search(r"\bteas", text, re.I):
            match = re.search(r"\bteased\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)", text)
            target = match.group(1) if match else None
            theme = "humor"
            question = f"What kinds of things would {subject} tease {target} about?" if target else f"What kinds of things would {subject} tease about?"
        elif "humor" in themes:
            theme, question = "humor", f"What would {subject} do or say that made people laugh?"
        elif "comfort" in themes:
            theme, question = "comfort", f"What do you remember {subject} saying or doing at those times?"
        elif "anger" in themes:
            theme, question = "anger", f"What do you remember about how {subject} handled frustration?"
        elif "values" in themes:
            theme = "values"
            if re.search(r"stud|education|school", text, re.I):
                question = f"What do you remember {subject} doing or saying about studies?"
            else:
                question = f"What do you remember {subject} doing that showed what mattered?"
        elif any(item.category == "personality" for item in candidates):
            theme, question = "personality", f"What did that look like in {subject}'s everyday life?"
    if not question:
        return plan
    normalized = re.sub(r"\W+", " ", question.casefold()).strip()
    repeated = any(re.sub(r"\W+", " ", old.casefold()).strip() == normalized or (theme != "personality" and theme in _l13_personality_themes(old)) for old in recent_questions)
    if repeated:
        return plan.model_copy(update={"should_ask_followup": False, "followup_strategy": FollowupStrategy.NO_QUESTION, "question": None, "reason": "recent_personality_theme"})
    return plan.model_copy(update={
        "should_ask_followup": True,
        "followup_strategy": FollowupStrategy.EXPLORE_UNDERDEVELOPED_DOMAIN if not candidates else FollowupStrategy.DEEPEN_NEW_FACT,
        "question": question, "missing_detail": "one lived detail in the current contribution" if candidates else "an optional unexplored aspect of expression",
        "reason": "personality_doorway_" + theme,
    })

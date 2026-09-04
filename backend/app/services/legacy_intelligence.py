import re
import unicodedata
from collections import defaultdict
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field


class QueryIntent(str, Enum):
    PERSONAL = "personal"
    GENERAL = "general"
    MIXED = "mixed"
    FRESH = "fresh"


class QueryRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: QueryIntent
    personal_topics: list[str] = Field(default_factory=list, max_length=12)
    entities: list[str] = Field(default_factory=list, max_length=20)
    time_refs: list[str] = Field(default_factory=list, max_length=12)
    needs_memory: bool
    needs_general_knowledge: bool
    needs_fresh_data: bool
    asks_personal_opinion: bool = False
    identity_challenge: bool = False


FRESH_PATTERNS = (
    r"\b(today|currently|current|latest|right now|this week|this month|yesterday|last night|breaking|live|weather|market today|news|score|result)\b",
    r"\b(aaj|abhi|haal filhaal|sadhya|aajcha|aajchi|heute|aktuell|derzeit|wetter)\b",
    r"(आज|सध्या|आत्ता|आजच[ाीे]|वर्तमान|मौसम)",
)
PERSONAL_PATTERNS = (
    r"\b(your|you loved|you liked|you hated|you hate|you disliked|you prefer|you studied|you worked|you lived|you met|did you|were you|have you|do you (?:like|love|prefer|remember)|what do you think|what was .* to you|who was .* to you|what (?:have|did) i (?:already )?tell you|already told you)\b",
    r"\b(tumhala|tumhi|tujha|tujhi|aapko|aapki|aapka|aapne|dein(?:e|er|en)?|du |hast du|warst du)\b",
    r"(तुम्ह|तुझ|आपक|आपने|तुमने|तुम्हारा|तुमची|तुमचा)",
)
GENERAL_PATTERNS = (
    r"\b(what is|what are|explain|why (?:is|are|do|does)|how does|how do|describe|define|meaning of|tell me about)\b",
    r"\b(kya hai|samjhao|kaay aahe|kay ahe|erkläre|was ist|warum|wie funktioniert)\b",
    r"(क्या है|समझा|काय आहे|स्पष्ट करा)",
)
OPINION_PATTERNS = (
    r"\b(what do you think|your (?:view|opinion|belief)|did you believe|how did you feel)\b",
    r"\b(tumhala kay vat|aap kya soch|deine meinung|was denkst du)\b",
    r"(तुम्हाला काय वाट|आप क्या सोच|तुमचे मत)",
)
IDENTITY_PATTERNS = (r"\b(are you|who are you|chatgpt|artificial intelligence|an ai)\b", r"(तुम कौन|तुम्ही कोण)")

TOPIC_TERMS = {
    "childhood": ("childhood", "child", "growing up", "school days", "bachpan", "बालपण", "बचपन"),
    "education": ("school", "college", "study", "studied", "education", "university", "शिक्षण", "पढ़ाई"),
    "career": ("job", "work", "career", "office", "profession", "नोकरी", "काम"),
    "relationship": ("husband", "wife", "son", "daughter", "mother", "father", "family", "married", "meet", "met", "relationship", "आई", "वडील", "पति", "पत्नी", "बेटा", "बेटी"),
    "preference": ("like", "love", "favorite", "favourite", "prefer", "enjoy", "आवड", "पसंद"),
    "opinion": ("think", "view", "opinion", "believe", "feel", "मत", "सोच"),
    "place": ("where", "place", "home", "city", "trip", "travel", "कुठे", "कहाँ", "reise"),
    "story": ("story", "happen", "remember about", "how did", "what was", "कहानी", "गोष्ट"),
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", normalize(value), flags=re.UNICODE))


def _matches(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _contains_term(text: str, term: str) -> bool:
    term = normalize(term)
    if re.fullmatch(r"[a-z0-9]+", term):
        return bool(re.search(rf"\b{re.escape(term)}\b", text))
    return term in text


def _entity_catalog(memories: Sequence[Any]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for memory in memories:
        for link in getattr(memory, "entity_links", ()):
            entity = link.entity
            for value in (entity.name, *(entity.aliases or [])):
                key = normalize(value)
                if len(key) > 1:
                    catalog[key] = entity.name
    return catalog


def analyze_legacy_query(query: str, subject_name: str | None = None, memories: Sequence[Any] = ()) -> QueryRoute:
    text = normalize(query)
    catalog = _entity_catalog(memories)
    entities = list(dict.fromkeys(name for alias, name in catalog.items() if alias in text))
    subject_mentioned = bool(subject_name and normalize(subject_name) in text)
    topics = [topic for topic, terms in TOPIC_TERMS.items() if any(_contains_term(text, term) for term in terms)]
    time_refs = list(dict.fromkeys(re.findall(r"\b(?:18|19|20)\d{2}\b|\b\d{1,3}\s+years?\s+(?:old|later|earlier|before|after)\b", text)))
    for marker in ("childhood", "school", "college", "before", "after", "later", "marriage", "first job", "बालपण", "बचपन"):
        if marker in text and marker not in time_refs:
            time_refs.append(marker)

    fresh = _matches(FRESH_PATTERNS, text)
    personal = _matches(PERSONAL_PATTERNS, text)
    opinion = _matches(OPINION_PATTERNS, text)
    identity = _matches(IDENTITY_PATTERNS, text)
    relationship_shape = bool(re.search(r"\b(who is|what is .* to|whose|father|mother|husband|wife|son|daughter)\b", text))
    relationship_query = relationship_shape and bool(entities or subject_mentioned or "relationship" in topics)
    if relationship_query and "relationship" not in topics:
        topics.append("relationship")
    personal = personal or opinion or identity or relationship_query
    general = _matches(GENERAL_PATTERNS, text)
    if (entities or subject_mentioned) and any(topic in topics for topic in ("relationship", "story", "education", "career", "place", "preference", "opinion")):
        personal = True
        if not re.search(r"\b(explain|define|why (?:is|are)|how does|what is (?:a|an|the) )\b", text):
            general = False

    if fresh:
        intent = QueryIntent.FRESH
    elif personal and general:
        intent = QueryIntent.MIXED
    elif personal:
        intent = QueryIntent.PERSONAL
    else:
        intent = QueryIntent.GENERAL
        general = True
    return QueryRoute(
        intent=intent,
        personal_topics=topics,
        entities=entities,
        time_refs=time_refs,
        needs_memory=personal and not identity,
        needs_general_knowledge=general and not fresh,
        needs_fresh_data=fresh,
        asks_personal_opinion=opinion,
        identity_challenge=identity,
    )


def relevance_components(memory: Any, query: str, semantic: float, route: QueryRoute) -> dict[str, float]:
    query_tokens = tokens(query)
    memory_tokens = tokens(memory.canonical_text)
    lexical = len(query_tokens & memory_tokens) / max(1, len(query_tokens))
    aliases = [normalize(link.entity.name) for link in memory.entity_links]
    aliases.extend(normalize(alias) for link in memory.entity_links for alias in (link.entity.aliases or []))
    entity = 1.0 if any(alias in normalize(query) for alias in aliases if len(alias) > 1) else 0.0
    topic = 1.0 if memory.category in route.personal_topics else 0.0
    temporal = 1.0 if route.time_refs and any(ref in normalize(memory.canonical_text) for ref in route.time_refs) else 0.0
    relationship = 1.0 if "relationship" in route.personal_topics and (memory.category == "relationship" or any("husband" in link.role or "wife" in link.role or "father" in link.role or "mother" in link.role or "son" in link.role or "daughter" in link.role for link in memory.entity_links)) else 0.0
    confidence = max(0.0, min(1.0, float(memory.confidence or 0)))
    score = semantic * .55 + lexical * .22 + entity * .16 + topic * .10 + temporal * .08 + relationship * .12 + confidence * .03
    return {"score": score, "semantic": semantic, "lexical": lexical, "entity": entity, "topic": topic, "temporal": temporal, "relationship": relationship}


def rerank_memories(memories: Sequence[Any], query: str, semantic_scores: Mapping[int, float], route: QueryRoute, top_k: int, threshold: float) -> tuple[Any, ...]:
    scored = [(memory, relevance_components(memory, query, semantic_scores.get(memory.id, 0.0), route)) for memory in memories]
    scored.sort(key=lambda item: (item[1]["score"], item[0].confidence, item[0].id), reverse=True)
    floor = max(.08, threshold * .58)
    selected = [memory for memory, parts in scored if parts["score"] >= floor]
    if not selected and scored and scored[0][1]["score"] >= floor * .62:
        selected.append(scored[0][0])
    selected_ids = {memory.id for memory in selected}
    entity_ids = {link.entity_id for memory in selected for link in memory.entity_links}
    story_keys = {memory.story_key for memory in selected if memory.story_key}
    expanded = list(selected)
    for memory, _parts in scored:
        if memory.id in selected_ids:
            continue
        graph_linked = bool(entity_ids and any(link.entity_id in entity_ids for link in memory.entity_links))
        story_linked = bool(memory.story_key and memory.story_key in story_keys)
        if graph_linked or story_linked:
            expanded.append(memory)
            selected_ids.add(memory.id)
    rank = {memory.id: index for index, (memory, _parts) in enumerate(scored)}
    expanded.sort(key=lambda memory: (0 if memory in selected else 1, rank[memory.id]))
    return tuple(expanded[:top_k])


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def temporal_representation(memories: Sequence[Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    most_recent_year: int | None = None
    for memory in sorted(memories, key=lambda item: item.id):
        text = memory.canonical_text
        years = [int(year) for year in re.findall(r"\b(?:18|19|20)\d{2}\b", text)]
        age_pairs = re.findall(r"\b(?:aged?|age)\s+(\d{1,3})\b|\b(\d{1,3})\s+years? old\b", text, re.IGNORECASE)
        ages = [int(value) for pair in age_pairs for value in pair if value]
        for year in years:
            timeline.append({"memory_id": memory.id, "kind": "explicit_year", "year": year, "text": text, "derived": False})
            most_recent_year = year
        relative = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\s+later\b", text, re.IGNORECASE)
        if relative and most_recent_year is not None:
            delta = int(relative.group(1)) if relative.group(1).isdigit() else NUMBER_WORDS[relative.group(1).casefold()]
            timeline.append({"memory_id": memory.id, "kind": "derived_year", "year": most_recent_year + delta, "basis_year": most_recent_year, "delta_years": delta, "text": text, "derived": True})
        for age in ages:
            timeline.append({"memory_id": memory.id, "kind": "explicit_age", "age": age, "text": text, "derived": False})
    return timeline


def derived_persona_profile(memories: Sequence[Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {"personality": [], "values_and_beliefs": [], "communication_tendencies": [], "preferences": [], "source_memory_ids": []}
    category_map = {
        "personality": "personality",
        "value": "values_and_beliefs",
        "belief": "values_and_beliefs",
        "opinion": "values_and_beliefs",
        "preference": "preferences",
        "habit": "communication_tendencies",
    }
    for memory in memories:
        target = category_map.get(memory.category)
        if target and len(profile[target]) < 8:
            profile[target].append(memory.canonical_text)
            profile["source_memory_ids"].append(memory.id)
    profile["source_memory_ids"] = list(dict.fromkeys(profile["source_memory_ids"]))
    return profile


def relationship_representation(memories: Sequence[Any]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for memory in memories:
        if memory.category != "relationship" and not any(link.role not in {"subject", "mentioned"} for link in memory.entity_links):
            continue
        relationships.append({"memory_id": memory.id, "fact": memory.canonical_text, "entities": [{"name": link.entity.name, "role": link.role, "aliases": link.entity.aliases or []} for link in memory.entity_links]})
    return relationships


def story_representation(memories: Sequence[Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for memory in memories:
        if memory.story_key:
            groups[memory.story_key].append(memory)
    return [{"story_key": key, "ordered_fragments": [{"memory_id": memory.id, "text": memory.canonical_text} for memory in sorted(items, key=lambda item: item.id)]} for key, items in groups.items()]


def evidence_level(route: QueryRoute, memories: Sequence[Any]) -> str:
    if not route.needs_memory or route.identity_challenge:
        return "not_applicable"
    if not memories:
        return "none"
    confident = [memory for memory in memories if float(memory.confidence or 0) >= .85]
    if len(confident) >= 2 and (len({memory.story_key for memory in confident if memory.story_key}) == 1 or bool(set.intersection(*(set(link.entity_id for link in memory.entity_links) for memory in confident)))):
        return "high_or_strong_inference"
    if confident:
        return "high_direct_or_medium_inference"
    return "low"


def detect_conflicts(memories: Sequence[Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    opposites = ((" loves ", " hates "), (" liked ", " disliked "), (" is ", " is not "), (" always ", " never "))
    for index, left in enumerate(memories):
        for right in memories[index + 1:]:
            if left.category != right.category:
                continue
            left_text = f" {normalize(left.canonical_text)} "; right_text = f" {normalize(right.canonical_text)} "
            shared_entities = {link.entity_id for link in left.entity_links} & {link.entity_id for link in right.entity_links}
            if shared_entities and any((a in left_text and b in right_text) or (b in left_text and a in right_text) for a, b in opposites):
                conflicts.append({"memory_ids": [left.id, right.id], "instruction": "Active evidence conflicts; do not choose arbitrarily."})
    return conflicts


def intelligence_payload(route: QueryRoute, selected_memories: Sequence[Any], active_memories: Sequence[Any]) -> dict[str, Any]:
    return {
        "route": route.model_dump(mode="json"),
        "evidence_level": evidence_level(route, selected_memories),
        "temporal": temporal_representation(selected_memories),
        "relationships": relationship_representation(selected_memories),
        "stories": story_representation(selected_memories),
        "derived_persona_profile": derived_persona_profile(active_memories),
        "conflicts": detect_conflicts(selected_memories),
    }


def followup_policy(analysis: Any | None, active_memories: Sequence[Any], recent_messages: Sequence[Any] = ()) -> dict[str, Any]:
    candidates = list(getattr(analysis, "memories", ()) or ())
    if not candidates:
        return {"ask": False, "reason": "no_new_legacy_material", "suggestion": None}
    recent_assistant_questions = 0
    for message in list(recent_messages)[-6:]:
        role = getattr(getattr(message, "role", None), "value", getattr(message, "role", ""))
        if role == "assistant" and "?" in getattr(message, "content", ""):
            recent_assistant_questions += 1
    if recent_assistant_questions >= 1:
        return {"ask": False, "reason": "question_fatigue", "suggestion": None}
    primary = candidates[0]
    entities = [entity.name for entity in getattr(primary, "entities", ()) if getattr(entity, "role", "") != "subject"]
    subject = entities[0] if entities else "that part of the story"
    suggestions = {
        "relationship": f"Ask about the first meaningful interaction involving {subject}.",
        "story": f"Ask for one still-missing moment or turning point involving {subject}.",
        "education": "Ask about one vivid person, event, or turning point from that period of education.",
        "career": "Ask what led to that work or what the experience meant to the Legacy subject.",
        "habit": "Ask how that routine began or why it mattered.",
        "value": "Ask for one real example that shows how that value shaped a choice.",
        "tradition": "Ask who participated and what made the tradition meaningful.",
    }
    suggestion = suggestions.get(primary.category)
    return {"ask": bool(suggestion), "reason": "specific_gap" if suggestion else "no_high_value_gap", "suggestion": suggestion}

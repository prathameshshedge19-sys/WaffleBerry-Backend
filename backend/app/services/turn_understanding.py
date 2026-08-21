"""One semantic interpretation shared by Live Call routing and grounding."""

from dataclasses import dataclass
import re


_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_LEADING_DISCOURSE = re.compile(
    r"^(?:(?:okay|ok|well|so|thanks|thank you|please)[,!\s]+)+", re.IGNORECASE,
)
_ATTRIBUTES = {
    "name": "name", "names": "name", "breed": "breed", "breeds": "breed",
    "size": "size", "large": "size", "count": "count", "many": "count",
    "brand": "brand", "model": "model", "where": "place", "when": "time",
    "nickname": "nickname", "nicknames": "nickname",
    "birthday": "birthday", "birthdays": "birthday",
}
_ALIASES = {
    "dog": "dogs", "pets": "dogs", "pet": "dogs", "television": "tv",
    "wife": "spouse",
}
_KNOWN_SUBJECTS = (
    "husband", "wife", "spouse", "brother", "sister", "family", "dogs", "dog",
    "pets", "pet", "tv", "television", "garden", "car", "cars", "trip", "trips",
    "siblings", "children", "friends", "schools", "school", "jobs", "colleagues",
    "hobbies", "places", "houses",
)
_REFERENTIAL = frozenset({
    "he", "her", "him", "it", "she", "that", "their", "them", "they", "this",
    "those", "what", "who", "else", "more", "detail", "details", "name", "names",
    "size", "brand", "model",
})


@dataclass(frozen=True)
class TurnUnderstanding:
    speech_act: str
    top_level_class: str
    explicit_subjects: tuple[str, ...]
    requested_attributes: tuple[str, ...]
    intent: str
    referential_followup: bool
    replaces_topic: bool
    resolved_topic: str | None
    subject_type: str = "memory"
    corrective_kind: str | None = None
    uses_previous_answer_anchor: bool = False


def interpret_turn(query: str, *, active_topic: str | None = None) -> TurnUnderstanding:
    normalized = " ".join(query.casefold().split())
    semantic = _LEADING_DISCOURSE.sub("", normalized)
    words = _WORDS.findall(semantic)
    word_set = set(words)
    attributes = list(dict.fromkeys(_ATTRIBUTES[word] for word in words if word in _ATTRIBUTES))
    if re.search(r"\bwhat did\b.+\bdo\b|\bused to do\b", semantic):
        attributes.append("activity")
    if re.search(r"\b(?:tease|teased|teasing)\b", semantic):
        attributes.append("teasing")
    if re.search(r"\b(?:play|played|playing)\b", semantic):
        attributes.append("activity")
    if re.search(r"\bchildhood\b", semantic):
        attributes.append("childhood_narrative")
    if re.search(r"\b(?:evenings?|mornings?|afternoons?|nights?)\b", semantic):
        attributes.append("time_context")
    if re.search(
        r"\b(?:their [\w'-]+|who are (?:they|your)|which (?:ones?|[\w'-]+)|"
        r"how many|where have you (?:travelled|traveled|been)|both|all of them|"
        r"the other one|the second one)\b",
        semantic,
    ):
        attributes.append("collection")
    if re.search(r"\b(?:do|did) (?:i|we|you) (?:have|own|go|visit)|\b(?:is|are) there\b", semantic):
        attributes.insert(0, "existence")

    explicit = []
    for candidate in _KNOWN_SUBJECTS:
        if re.search(rf"\b{re.escape(candidate)}\b", semantic):
            canonical = _ALIASES.get(candidate, candidate)
            if canonical not in explicit:
                explicit.append(canonical)
    terse_correction_target = re.match(r"^no[, ]+([\w'-]+)\b", semantic)
    if terse_correction_target and terse_correction_target.group(1).casefold() not in {"i", "we"}:
        candidate = terse_correction_target.group(1).casefold()
        if candidate not in explicit:
            explicit.append(candidate)
    possessive = re.search(r"\b(?:your|my|our)\s+([\w'-]+)", semantic)
    if possessive and not re.match(r"^(?:i|we)\b", semantic):
        candidate = re.sub(r"['’]s$", "", possessive.group(1))
        canonical = _ALIASES.get(candidate, candidate)
        if canonical not in explicit and candidate not in {"full"}:
            explicit.append(canonical)

    all_words = set(_WORDS.findall(normalized))
    social_vocabulary = {
        "hello", "hi", "hey", "thanks", "thank", "you", "okay", "ok", "perfect",
        "goodbye", "bye", "good", "night", "how", "are",
    }
    clarification = bool(re.search(
        r"\b(?:i (?:was )?(?:asking|asked) (?:you )?(?:about )?|i meant|meant the)\b",
        semantic,
    ))
    objection = bool(re.search(
        r"\b(?:did i ask you that|that(?:'s| is) not what i asked|"
        r"i (?:did not|didn't) ask (?:you )?(?:that|about)|why are you telling me)\b",
        semantic,
    ))
    correction = bool(
        not clarification and not objection and re.search(
            r"^(?:no\b|actually\b|that(?:'s| is)\b.*\bnot\b|say\b.*\bnot\b)", semantic,
        )
    )
    corrective_kind = "clarification" if clarification else "objection" if objection else "correction" if correction else None
    social = bool(
        (all_words and all_words <= social_vocabulary)
        or
        re.fullmatch(r"(?:hello|hi|hey|thanks|thank you|okay|ok|perfect|goodbye|bye|good night)[.! ]*", semantic)
        or re.fullmatch(r"how are you[?.!]*", semantic)
    )
    personal_proposition = bool(
        (re.search(r"\b(?:my|our|your)\s+[\w'-]+", semantic)
         and not re.match(r"^(?:i|we)\b", semantic))
        or re.search(r"\b(?:do|did) (?:i|we|you) (?:have|own)\b", semantic)
        or re.match(r"^(?:have i|has my|did i|was i|were we|who am i)\b", semantic)
        or re.search(r"\bi told you (?:before|earlier|that)\b", semantic)
        or re.search(r"\b(?:do|did) you remember(?: anything)?(?: about| of)?\b", semantic)
        or re.search(r"\b(?:tumhare|tumhara|tumhari|aapke|aapka|aapki|mere|mera|meri|hamare|hamara|hamari)\b", semantic)
        or re.match(r"^you (?:are|'re)\s+[^?!.]+", semantic)
    )
    general_proposition = bool(re.match(
        r"^(?:what (?:is|are|does|do|can|could|should)|define|describe|explain|how (?:does|do|is|are|big)|why (?:does|do|is|are))\b",
        semantic,
    ) or re.match(r"^what\b.*\b(?:can|could|should|does|do)\b", semantic)) and not personal_proposition
    reference_only = bool(words) and not explicit and (
        bool(_REFERENTIAL.intersection(word_set)) or len(words) <= 3
    )
    referential = bool(active_topic and reference_only and not general_proposition and not social)

    if corrective_kind and (explicit or active_topic):
        top_level = "personal"
    elif social:
        top_level = "social"
    elif personal_proposition or re.match(r"^(?:and\s+)?what about\b", semantic) or re.match(r"^tell me about the\b", semantic):
        top_level = "personal"
    elif general_proposition:
        top_level = "general"
    elif referential:
        top_level = "personal"
    elif explicit or (0 < len(words) <= 3):
        top_level = "personal"
    else:
        top_level = "general"

    if re.search(r"\b(?:your|my|our)\s+nickname\b", semantic):
        subject, subject_type = "represented person", "self"
        if "nickname" not in attributes:
            attributes.append("nickname")
    elif re.search(r"\b(?:your|my|our)\s+(?:full\s+)?name\b", semantic) or re.fullmatch(r"who are you[?.!]*", semantic):
        subject, subject_type = "represented person", "self"
        if "name" not in attributes:
            attributes.append("name")
    elif len(explicit) > 1 and " and " in semantic:
        subject, subject_type = "+".join(explicit), "multi"
    elif explicit:
        subject = explicit[-1]
        subject_type = "relationship" if subject in {"husband", "spouse", "brother", "sister", "parent", "child"} else "memory"
    elif (referential or corrective_kind) and active_topic:
        subject, subject_type = active_topic, "memory"
    else:
        candidates = [
            re.sub(r"['’]s$", "", word) for word in words
            if word not in {"and", "about", "tell", "me", "the", "what", "who", "is", "are", "was", "were", "their", "it"}
            and word not in _ATTRIBUTES
        ]
        subject, subject_type = (candidates[-1] if candidates else "personal subject"), "memory"

    attributes = list(dict.fromkeys(attributes))
    intent = "existence_query" if "existence" in attributes else "attribute_query" if attributes else "summary"
    speech_act = corrective_kind or ("question" if "?" in query or re.match(r"^(?:what|who|why|how|do|did|have|is|are)\b", semantic) else "statement")
    resolved = subject if top_level == "personal" else None
    replaces = bool(explicit and active_topic and subject != active_topic)
    return TurnUnderstanding(
        speech_act, top_level, tuple(explicit), tuple(attributes), intent,
        referential, replaces, resolved, subject_type, corrective_kind,
        bool(corrective_kind and not explicit and active_topic),
    )

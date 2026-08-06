"""Conservative query-time proper-name resolution for one Legacy."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD
from app.models.memory import (
    IdentityFactStatus,
    IdentityFactType,
    LegacyIdentityFact,
    Memory,
    MemoryParticipant,
    MemoryReviewStatus,
)


_DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "क़": "q",
    "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "dh", "फ़": "f",
}
_DEVANAGARI_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u",
    "ऊ": "uu", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
}
_DEVANAGARI_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
}
_NAME_FACT_TYPES = {
    IdentityFactType.FULL_NAME,
    IdentityFactType.PREFERRED_NAME,
    IdentityFactType.SPOUSE_NAME,
    IdentityFactType.CHILD_NAME,
    IdentityFactType.PARENT_NAME,
    IdentityFactType.SIBLING_NAME,
}
_RELATION_TERMS = {
    IdentityFactType.SPOUSE_NAME: {
        "spouse", "husband", "wife", "navra", "navryacha", "pati", "patni",
        "नवरा", "नवऱ्याचं", "पति", "पत्नी",
    },
    IdentityFactType.SIBLING_NAME: {
        "sibling", "brother", "sister", "bhau", "bahin", "भाऊ", "बहीण",
        "भाई", "बहन",
    },
    IdentityFactType.PARENT_NAME: {
        "parent", "mother", "father", "aai", "baba", "आई", "वडील", "माँ", "पिता",
    },
    IdentityFactType.CHILD_NAME: {
        "child", "son", "daughter", "mulga", "mulgi", "मुलगा", "मुलगी", "बेटा", "बेटी",
    },
}
def transliterate_devanagari(value: str) -> str:
    """Return a deterministic Latin comparison form without changing source data."""
    output: list[str] = []
    for char in unicodedata.normalize("NFKC", value):
        if char in _DEVANAGARI_CONSONANTS:
            output.append(_DEVANAGARI_CONSONANTS[char] + "a")
        elif char in _DEVANAGARI_MATRAS:
            if output and output[-1].endswith("a"):
                output[-1] = output[-1][:-1]
            output.append(_DEVANAGARI_MATRAS[char])
        elif char == "्":
            if output and output[-1].endswith("a"):
                output[-1] = output[-1][:-1]
        elif char in _DEVANAGARI_VOWELS:
            output.append(_DEVANAGARI_VOWELS[char])
        elif char in {"ं", "ँ"}:
            output.append("n")
        elif char == "ः":
            output.append("h")
        else:
            output.append(char)
    source_words = unicodedata.normalize("NFKC", value).split()
    transliterated_words = "".join(output).split()
    for index, token in enumerate(transliterated_words):
        source = source_words[index] if index < len(source_words) else ""
        if (
            token.endswith("a")
            and any(char in _DEVANAGARI_CONSONANTS for char in source)
        ):
            transliterated_words[index] = token[:-1]
    return " ".join(transliterated_words)


def comparable_name(value: str) -> str:
    """Build a script-neutral, conservative phonetic comparison key."""
    latin = transliterate_devanagari(value).casefold()
    latin = "".join(
        char for char in unicodedata.normalize("NFKD", latin)
        if not unicodedata.combining(char)
    )
    latin = re.sub(r"[^a-z0-9]+", " ", latin).strip()
    latin = re.sub(r"(?:ee|ii)", "i", latin)
    latin = re.sub(r"(?:oo|uu)", "u", latin)
    latin = re.sub(r"aa", "a", latin)
    return " ".join(latin.split())


def _query_keys(query: str) -> set[str]:
    words: list[str] = []
    current: list[str] = []
    for char in unicodedata.normalize("NFKC", query):
        if unicodedata.category(char)[0] in {"L", "M", "N"}:
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    tokens = [comparable_name(token) for token in words]
    tokens = [token for token in tokens if token]
    keys = set(tokens)
    for width in (2, 3):
        keys.update(
            " ".join(tokens[index:index + width])
            for index in range(len(tokens) - width + 1)
        )
    return keys


def _similarity(candidate: str, query_keys: set[str]) -> float:
    candidate_tokens = candidate.split()
    best = 0.0
    for query_key in query_keys:
        if query_key == candidate:
            best = max(best, 1.0)
            continue
        query_tokens = query_key.split()
        if query_key in candidate_tokens or candidate in query_tokens:
            best = max(best, 0.94)
            continue
        compact_candidate = candidate.replace(" ", "")
        compact_query = query_key.replace(" ", "")
        if min(len(compact_candidate), len(compact_query)) < 6:
            continue
        if compact_candidate.startswith(compact_query) or compact_query.startswith(compact_candidate):
            continue
        ratio = SequenceMatcher(None, compact_candidate, compact_query).ratio()
        if ratio >= 0.90:
            best = max(best, ratio)
    return best


def _relationship_intent(query: str) -> IdentityFactType | None:
    keys = _query_keys(query)
    for fact_type, terms in _RELATION_TERMS.items():
        if keys & {comparable_name(term) for term in terms}:
            return fact_type
    return None


@dataclass(frozen=True)
class NameResolution:
    canonical_value: str | None = None
    fact_type: IdentityFactType | None = None
    relationship: str | None = None
    candidate_count: int = 0
    confidence: float = 0.0
    ambiguous: bool = False
    relationship_context_used: bool = False

    def expand_query(self, query: str) -> str:
        if not self.canonical_value:
            return query
        if unicodedata.normalize("NFKC", self.canonical_value).casefold() in unicodedata.normalize("NFKC", query).casefold():
            return query
        return f"{query} {self.canonical_value}"


@dataclass(frozen=True)
class _Candidate:
    value: str
    fact_type: IdentityFactType | None
    relationship: str
    sort_id: tuple[int, int]
    conflicting: bool = False


def _relationship_type(value: str) -> IdentityFactType | None:
    keys = _query_keys(value)
    for fact_type, terms in _RELATION_TERMS.items():
        if keys & {comparable_name(term) for term in terms}:
            return fact_type
    return None


class ProperNameResolver:
    """Resolve strong name variants from approved facts for one owned Legacy."""

    def resolve(self, db: Session, *, user_id: int, legacy_id: int, query: str) -> NameResolution:
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            return NameResolution()
        query_keys = _query_keys(query)
        relationship_intent = _relationship_intent(query)
        facts = db.query(LegacyIdentityFact).join(
            Memory, Memory.memory_id == LegacyIdentityFact.source_memory_id
        ).filter(
            LegacyIdentityFact.legacy_id == legacy_id,
            LegacyIdentityFact.fact_type.in_(_NAME_FACT_TYPES),
            LegacyIdentityFact.status.in_((IdentityFactStatus.ACTIVE, IdentityFactStatus.CONFLICTING)),
            Memory.review_status == MemoryReviewStatus.APPROVED,
            Memory.superseded_by_memory_id.is_(None),
        ).order_by(LegacyIdentityFact.identity_fact_id).all()
        participants = db.query(MemoryParticipant).join(
            Memory, Memory.memory_id == MemoryParticipant.memory_id
        ).filter(
            Memory.legacy_id == legacy_id,
            Memory.review_status == MemoryReviewStatus.APPROVED,
            Memory.superseded_by_memory_id.is_(None),
            MemoryParticipant.relationship.is_not(None),
        ).order_by(MemoryParticipant.memory_participant_id).all()
        candidates = [
            _Candidate(
                fact.value,
                fact.fact_type,
                fact.relationship,
                (0, fact.identity_fact_id),
                fact.status == IdentityFactStatus.CONFLICTING,
            )
            for fact in facts
        ]
        candidates.extend(
            _Candidate(
                participant.name,
                _relationship_type(participant.relationship or ""),
                participant.relationship or "",
                (1, participant.memory_participant_id),
            )
            for participant in participants
        )
        scored = []
        for candidate in candidates:
            if relationship_intent is not None and candidate.fact_type != relationship_intent:
                continue
            score = _similarity(comparable_name(candidate.value), query_keys)
            if score >= 0.90:
                scored.append((score, candidate))
        if not scored:
            return NameResolution(candidate_count=0)
        scored.sort(key=lambda item: (-item[0], item[1].sort_id))
        top_score = scored[0][0]
        plausible = [item for item in scored if top_score - item[0] <= 0.03]
        distinct_contexts = {
            (
                candidate.fact_type,
                comparable_name(candidate.value),
                comparable_name(candidate.relationship),
            )
            for _, candidate in plausible
        }
        conflict = any(candidate.conflicting for _, candidate in plausible)
        if len(distinct_contexts) > 1 or conflict:
            return NameResolution(
                candidate_count=len(plausible),
                confidence=top_score,
                ambiguous=True,
                relationship_context_used=relationship_intent is not None,
            )
        candidate = plausible[0][1]
        return NameResolution(
            canonical_value=candidate.value,
            fact_type=candidate.fact_type,
            relationship=candidate.relationship or None,
            candidate_count=len(plausible),
            confidence=top_score,
            relationship_context_used=(
                relationship_intent is not None or bool(candidate.relationship)
            ),
        )

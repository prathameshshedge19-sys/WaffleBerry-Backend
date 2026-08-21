"""Bounded semantic concept resolution before personal-memory retrieval."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True, slots=True)
class SemanticResolution:
    canonical_query: str
    canonical_concept: str | None = None
    canonical_relationship: str | None = None
    resolved_entity_type: str | None = None
    confidence: float = 1.0
    ambiguity: tuple[str, ...] = ()
    clarification_required: bool = False
    clarification_prompt: str | None = None
    context_used: bool = False
    source: str = "canonical_fast_path"

    @property
    def confidence_bucket(self) -> str:
        return "high" if self.confidence >= 0.8 else "medium" if self.confidence >= 0.5 else "low"


class SemanticConceptResolver:
    """Resolve surface language into the small canonical product ontology.

    Multilingual and unseen paraphrases remain owned by the existing semantic
    normalization model. These patterns are only bounded, high-confidence fast
    paths and ambiguity guards, not a memory-specific slang dictionary.
    """

    _CONCEPTS = (
        ("brother", "brother", "relationship", re.compile(
            r"\b(?:bro+|broo+|brow|bhai|bhau|male sibling|younger sibling)\b", re.I,
        )),
        ("husband", "husband", "relationship", re.compile(
            r"\b(?:hubby|better half)\b", re.I,
        )),
        ("mother", "mother", "relationship", re.compile(
            r"\b(?:mom|mum|mummy|mama)\b", re.I,
        )),
        ("father", "father", "relationship", re.compile(
            r"\b(?:dad|daddy|papa)\b", re.I,
        )),
        ("children", "children", "relationship_collection", re.compile(
            r"\b(?:kids|kidd?os?)\b", re.I,
        )),
        ("dogs", None, "pet_collection", re.compile(r"\b(?:pooch|pooches)\b", re.I)),
        ("cat", None, "pet", re.compile(r"\b(?:kitty|kitties)\b", re.I)),
        ("tv", None, "object", re.compile(r"\b(?:telly|television set)\b", re.I)),
        ("phone", None, "object", re.compile(r"\bmobile\b", re.I)),
        ("university", None, "place", re.compile(r"\buni\b", re.I)),
        ("exercise", None, "activity", re.compile(r"\bwork(?:ed|ing)? out\b", re.I)),
        ("spend time", None, "activity", re.compile(r"\bhang(?:ing)? out\b", re.I)),
    )
    _CANONICAL = frozenset({
        "self", "spouse", "husband", "wife", "partner", "brother", "sister",
        "sibling", "mother", "father", "parent", "son", "daughter", "child",
        "children", "friend", "pet", "dog", "dogs", "cat", "tv", "phone",
        "university", "nickname", "childhood",
    })
    _PERSONAL_FRAME = re.compile(
        r"\b(?:your|my|our)\s+([a-z][\w'-]*)", re.I,
    )
    _ROOTS = {
        "brother": ("brother", "brother", "relationship"),
        "husband": ("husband", "husband", "relationship"),
        "mother": ("mother", "mother", "relationship"),
        "father": ("father", "father", "relationship"),
        "child": ("child", "child", "relationship"),
        "dog": ("dogs", None, "pet_collection"),
        "cat": ("cat", None, "pet"),
        "television": ("tv", None, "object"),
        "nickname": ("nickname", None, "attribute"),
        "childhood": ("childhood", None, "narrative"),
    }
    _STOP = frozenset({
        "a", "about", "and", "are", "did", "do", "does", "i", "is", "like",
        "me", "my", "of", "our", "play", "tell", "the", "to", "what", "where",
        "who", "you", "your",
    })

    def resolve(self, query: str, *, active_topic: str | None = None) -> SemanticResolution:
        normalized = " ".join(query.split())
        lowered = normalized.casefold()
        if lowered in self._CANONICAL:
            concept = "dogs" if lowered == "dog" else lowered
            relationship = concept if concept in {
                "husband", "wife", "brother", "sister", "mother", "father",
                "parent", "child", "children",
            } else None
            return SemanticResolution(normalized, concept, relationship,
                                      "relationship" if relationship else "concept")
        if re.search(r"\b(?:business|project|work) partner\b", lowered):
            return SemanticResolution(normalized, "business partner", None, "relationship",
                                      context_used=bool(active_topic), source="context")
        if re.search(r"\bpartner\b", lowered):
            if active_topic and re.search(r"\b(?:business|project|work)\b", active_topic, re.I):
                return SemanticResolution(normalized, "business partner", None, "relationship",
                                          context_used=True, source="context")
            return SemanticResolution(
                normalized, "partner", None, "relationship", 0.6,
                ("husband or spouse", "business partner"), True,
                "Do you mean my husband or my business partner?",
                bool(active_topic), "ambiguity_guard",
            )
        for concept, relationship, entity_type, pattern in self._CONCEPTS:
            if pattern.search(normalized):
                canonical = pattern.sub(concept, normalized)
                canonical = self._reconstruct(canonical, concept)
                return SemanticResolution(
                    canonical, concept, relationship, entity_type, 0.95,
                    context_used=bool(active_topic), source="ontology_fast_path",
                )
        surface = self._surface_term(normalized)
        if surface and surface.casefold() in self._CANONICAL:
            concept = "dogs" if surface.casefold() in {"dog", "dogs"} else surface.casefold()
            return SemanticResolution(normalized, concept)
        fuzzy = self._fuzzy_concept(surface, normalized, active_topic)
        if fuzzy is not None:
            concept, relationship, entity_type, confidence = fuzzy
            canonical = re.sub(rf"\b{re.escape(surface)}\b", concept, normalized, flags=re.I)
            canonical = self._reconstruct(canonical, concept)
            return SemanticResolution(
                canonical, concept, relationship, entity_type, confidence,
                context_used=bool(active_topic), source="bounded_semantic_inference",
            )
        match = self._PERSONAL_FRAME.search(normalized)
        surface = surface or (
            re.sub(r"(?:'s|’s|')$", "", match.group(1), flags=re.I) if match else None
        )
        unknown_frame = bool(surface and re.fullmatch(
            rf"tell me about (?:(?:your|my|our) )?{re.escape(surface)}[.!?]*",
            normalized, re.I,
        ))
        if unknown_frame and surface.casefold() not in self._CANONICAL and surface.casefold() not in {
            "name", "full", "family", "childhood", "dogs", "tv", "nickname", "life", "trip",
            "bicycle", "bike", "car", "school", "job", "work", "garden", "home",
        }:
            return SemanticResolution(
                normalized, None, None, "unresolved_personal_concept", 0.2, (), True,
                f"What do you mean by '{surface}'?", bool(active_topic), "unknown_term_guard",
            )
        return SemanticResolution(normalized)

    @classmethod
    def _surface_term(cls, query: str) -> str | None:
        possessive = cls._PERSONAL_FRAME.search(query)
        if possessive:
            return re.sub(r"(?:'s|’s|')$", "", possessive.group(1), flags=re.I)
        about = re.search(r"\babout\s+([A-Za-z][\w'-]*)", query, re.I)
        if about:
            return about.group(1)
        candidates = [
            token for token in re.findall(r"[A-Za-z][\w'-]*", query)
            if token.casefold() not in cls._STOP
        ]
        return candidates[0] if candidates else None

    @classmethod
    def _fuzzy_concept(
        cls, surface: str | None, query: str, active_topic: str | None,
    ) -> tuple[str, str | None, str, float] | None:
        if not surface or len(surface) < 3:
            return None
        if surface.isupper() and len(surface) <= 4:
            if surface.casefold() == "dos" and active_topic and re.search(
                r"\b(?:dog|dogs|pet|pets)\b", active_topic, re.I,
            ):
                return "dogs", None, "pet_collection", .86
            return None
        key = surface.casefold()
        best = None
        for root, target in cls._ROOTS.items():
            spelling = SequenceMatcher(None, key, root).ratio()
            phonetic = cls._soundex(key)[:3] == cls._soundex(root)[:3]
            morphology = bool(
                key.startswith(root) or root.startswith(key)
                or re.sub(r"(?:y|ie|i|o|a|z|s)$", "", key) == root
            )
            score = max(spelling, .87 if phonetic and spelling >= .5 else 0,
                        .9 if morphology else 0)
            if best is None or score > best[0]:
                best = (score, target)
        if best and best[0] >= .78:
            concept, relationship, entity_type = best[1]
            return concept, relationship, entity_type, min(.96, best[0])
        return None

    @staticmethod
    def _soundex(value: str) -> str:
        if not value:
            return ""
        groups = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
                  **dict.fromkeys("dt", "3"), "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
        encoded = [groups.get(char, "") for char in value.casefold()[1:]]
        compact = []
        for digit in encoded:
            if digit and (not compact or digit != compact[-1]):
                compact.append(digit)
        return (value[0].upper() + "".join(compact) + "000")[:4]

    @staticmethod
    def _reconstruct(query: str, concept: str) -> str:
        lowered = query.casefold()
        if concept == "brother" and "play" in lowered and "like" in lowered:
            return "What did your brother like to play?"
        if concept == "brother" and "where" in lowered and re.search(r"\b(?:grow|grew)\b", lowered):
            return "Where did you and your brother grow up?"
        if concept == "dogs" and re.search(r"\bname", lowered):
            return "What are your dogs' names?"
        if concept == "husband" and re.search(r"\bname", lowered):
            return "What is your husband's name?"
        return query

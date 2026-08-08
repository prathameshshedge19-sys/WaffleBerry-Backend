"""Deterministic, non-persisted speaking-style evidence extraction."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryReviewStatus


_QUOTED_PHRASE = re.compile(
    r'["“](?P<double>[^"”\r\n]{1,80})["”]|'
    r"‘(?P<single>[^’\r\n]{1,80})’",
    re.UNICODE,
)
_GREETING_CUES = ("greet", "welcomed with", "said hi", "say hi")
_NICKNAME_PATTERN = re.compile(
    r"\b(?:nickname|pet name)\b|"
    r"\b(?:called|addressed)\s+"
    r"(?:him|her|me|us|the user|her son|her daughter|his son|his daughter)\b"
)
_EXPRESSION_CUES = (
    "expression",
    "phrase",
    "saying",
    "often said",
    "used to say",
    "would say",
)
_TONE_PATTERNS = (
    ("warm", re.compile(r"\b(?:spoke|speaks|tone was)\s+warm(?:ly)?\b")),
    ("formal", re.compile(r"\b(?:spoke|speaks|tone was)\s+formal(?:ly)?\b")),
    ("casual", re.compile(r"\b(?:spoke|speaks|tone was)\s+casual(?:ly)?\b")),
    ("direct", re.compile(r"\b(?:spoke|speaks|tone was)\s+direct(?:ly)?\b")),
    ("encouraging", re.compile(r"\b(?:spoke|speaks|tone was)\s+encouraging(?:ly)?\b")),
    ("optimistic", re.compile(r"\b(?:spoke|speaks|tone was)\s+optimistic(?:ally)?\b")),
    ("dry humour", re.compile(r"\b(?:dry sense of humo(?:u)?r|humo(?:u)?r was dry)\b")),
    ("playful humour", re.compile(r"\b(?:playful sense of humo(?:u)?r|humo(?:u)?r was playful)\b")),
    ("storytelling", re.compile(r"\b(?:loved|enjoyed) telling stories\b")),
)


@dataclass(frozen=True)
class PersonaProfile:
    """Bounded, explicit style evidence safe for prompt construction."""

    greetings: tuple[str, ...] = ()
    nicknames: tuple[str, ...] = ()
    recurring_expressions: tuple[str, ...] = ()
    tone_markers: tuple[str, ...] = ()

    @property
    def has_evidence(self) -> bool:
        return any(
            (
                self.greetings,
                self.nicknames,
                self.recurring_expressions,
                self.tone_markers,
            )
        )

    def prompt_data(self) -> dict[str, list[str]]:
        return {
            "greetings": list(self.greetings),
            "nicknames": list(self.nicknames),
            "recurring_expressions": list(self.recurring_expressions),
            "tone_markers": list(self.tone_markers),
        }


class PersonaProfileBuilder:
    """Extract only explicitly evidenced style from approved Memory text."""

    MAX_ITEMS_PER_FIELD = 5

    def build(self, memories: Iterable[object]) -> PersonaProfile:
        greetings: list[str] = []
        nicknames: list[str] = []
        expressions: list[str] = []
        tones: list[str] = []
        ordered = sorted(
            memories,
            key=lambda item: getattr(item, "memory_id", 0),
        )
        for memory in ordered:
            text = " ".join(
                value.strip()
                for value in (
                    getattr(memory, "title", ""),
                    getattr(memory, "summary", ""),
                )
                if isinstance(value, str) and value.strip()
            )
            normalized = unicodedata.normalize("NFKC", text).casefold()
            phrases = [
                (match.group("double") or match.group("single")).strip()
                for match in _QUOTED_PHRASE.finditer(text)
            ]
            if phrases and any(cue in normalized for cue in _GREETING_CUES):
                self._extend_unique(greetings, phrases)
            if phrases and _NICKNAME_PATTERN.search(normalized):
                self._extend_unique(nicknames, phrases)
            if phrases and any(cue in normalized for cue in _EXPRESSION_CUES):
                self._extend_unique(expressions, phrases)
            for label, pattern in _TONE_PATTERNS:
                if pattern.search(normalized):
                    self._extend_unique(tones, [label])
        return PersonaProfile(
            greetings=tuple(greetings[: self.MAX_ITEMS_PER_FIELD]),
            nicknames=tuple(nicknames[: self.MAX_ITEMS_PER_FIELD]),
            recurring_expressions=tuple(
                expressions[: self.MAX_ITEMS_PER_FIELD]
            ),
            tone_markers=tuple(tones[: self.MAX_ITEMS_PER_FIELD]),
        )

    @staticmethod
    def _extend_unique(target: list[str], values: Iterable[str]) -> None:
        seen = {item.casefold() for item in target}
        for value in values:
            if not value or value.casefold() in seen:
                continue
            target.append(value)
            seen.add(value.casefold())


class PersonaProfileService:
    """Regenerate one Legacy profile from all approved Memories."""

    def build(self, db: Session, *, legacy_id: int) -> PersonaProfile:
        memories = (
            db.query(
                Memory.memory_id,
                Memory.title,
                Memory.summary,
            )
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.review_status == MemoryReviewStatus.APPROVED,
            )
            .order_by(Memory.memory_id.asc())
            .all()
        )
        return PersonaProfileBuilder().build(memories)

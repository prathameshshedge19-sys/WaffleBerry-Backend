"""Semantic first-person normalization for future canonical memory writes."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.models.memory import IdentityFactType, Legacy, LegacyIdentityFact, Memory


def _key(value: str | None) -> str:
    return " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKC", value or "").casefold()
            if unicodedata.category(char)[0] in {"L", "M", "N"} or char.isspace()
        ).split()
    )


@dataclass(frozen=True)
class _Perspective:
    self_names: tuple[str, ...]
    other_names: tuple[str, ...]
    entity_aliases: tuple[tuple[str, str], ...]
    relationships: tuple[tuple[str, str], ...]
    shared: bool
    household: bool


class CanonicalMemoryPerspectiveService:
    """Render extracted roles from the selected Legacy's perspective."""

    _NARRATOR_PREFIX = re.compile(
        r"^(?:the user|the conversation|the memory|according to [^,]+|"
        r"[^,.]+ (?:stated|reported|mentioned))\s+(?:that\s+)?",
        re.IGNORECASE,
    )

    def normalize(
        self,
        candidate,
        *,
        legacy: Legacy,
        identity_facts: Sequence[LegacyIdentityFact],
        existing_memories: Sequence[Memory],
    ):
        aliases = self._self_aliases(legacy, identity_facts)
        canonical_entities = self._canonical_entities(identity_facts, existing_memories)
        participants = []
        self_names: list[str] = []
        other_names: list[str] = []
        entity_aliases: list[tuple[str, str]] = []
        relationships: list[tuple[str, str]] = []
        for participant in candidate.participants:
            name_key = _key(participant.name)
            canonical_name = canonical_entities.get(name_key, participant.name)
            is_self = name_key in aliases
            if is_self:
                self_names.append(participant.name)
            else:
                other_names.append(canonical_name)
                entity_aliases.append((participant.name, canonical_name))
                if participant.relationship:
                    relationships.append((canonical_name, participant.relationship))
            participants.append(participant.model_copy(update={"name": canonical_name}))

        tags = {_key(tag) for tag in candidate.tags}
        household = bool(self_names) and bool(
            tags & {"household", "home", "family", "pet", "pets"}
            or candidate.category in {"home", "family", "personal_detail"}
        )
        perspective = _Perspective(
            tuple(dict.fromkeys(self_names)),
            tuple(dict.fromkeys(other_names)),
            tuple(dict.fromkeys(entity_aliases)),
            tuple(dict.fromkeys(relationships)),
            bool(self_names and other_names),
            household,
        )
        summary = self._render(candidate.summary, perspective)
        title = self._render(candidate.title, perspective, title=True)
        details = self._remove_duplicate_identity_claims(
            candidate.details, identity_facts
        )
        return candidate.model_copy(update={
            "title": title,
            "summary": summary,
            "details": details,
            "participants": participants,
        })

    @staticmethod
    def _self_aliases(
        legacy: Legacy, identity_facts: Sequence[LegacyIdentityFact],
    ) -> set[str]:
        values = [legacy.display_name]
        values.extend(
            fact.value for fact in identity_facts
            if fact.fact_type in {
                IdentityFactType.FULL_NAME, IdentityFactType.PREFERRED_NAME,
            }
        )
        aliases = {_key(value) for value in values if _key(value)}
        aliases.update(
            value.split()[0] for value in tuple(aliases) if len(value.split()) > 1
        )
        return aliases

    @staticmethod
    def _canonical_entities(
        identity_facts: Sequence[LegacyIdentityFact],
        memories: Sequence[Memory],
    ) -> dict[str, str]:
        values = [fact.value for fact in identity_facts]
        values.extend(
            participant.name
            for memory in memories
            for participant in memory.participants
        )
        return {_key(value): value for value in values if _key(value)}

    @classmethod
    def _render(
        cls, text: str, perspective: _Perspective, *, title: bool = False,
    ) -> str:
        rendered = cls._NARRATOR_PREFIX.sub("", text.strip())
        for alias, canonical in perspective.entity_aliases:
            if alias != canonical:
                rendered = re.sub(
                    rf"\b{re.escape(alias)}\b", canonical, rendered,
                    flags=re.IGNORECASE,
                )
        if not perspective.self_names:
            return rendered

        self_pattern = "|".join(
            re.escape(name) for name in sorted(perspective.self_names, key=len, reverse=True)
        )
        # These rewrites are enabled only after structured participant resolution
        # identifies SELF; names alone never trigger perspective conversion.
        rendered = re.sub(
            rf"\b(?:{self_pattern})['’]s\s+(?:family|household|home)\s+has\b",
            "We have", rendered, flags=re.IGNORECASE,
        )
        rendered = re.sub(
            rf"\b(?:{self_pattern})['’]s\s+home\s+has\b",
            "We have", rendered, flags=re.IGNORECASE,
        )
        rendered = re.sub(
            rf"\b(?:{self_pattern})['’]s\b", "My", rendered,
            flags=re.IGNORECASE,
        )
        if perspective.other_names:
            for name, relationship in perspective.relationships:
                rendered = re.sub(
                    rf"\b(?:{self_pattern})\s+and\s+(?:her|his|their)\s+"
                    rf"{re.escape(relationship)}\s+{re.escape(name)}\b",
                    f"My {relationship} {name} and I", rendered,
                    flags=re.IGNORECASE,
                )
            other_pattern = "|".join(
                re.escape(name) for name in sorted(perspective.other_names, key=len, reverse=True)
            )
            rendered = re.sub(
                rf"\b(?:{self_pattern})\s+and\s+({other_pattern})\b",
                lambda match: f"{match.group(1)} and I", rendered,
                flags=re.IGNORECASE,
            )
            rendered = re.sub(
                rf"\b({other_pattern})\s+and\s+(?:{self_pattern})\b",
                lambda match: f"{match.group(1)} and I", rendered,
                flags=re.IGNORECASE,
            )
        rendered = re.sub(
            rf"\b(?:{self_pattern})\b", "I", rendered, flags=re.IGNORECASE,
        )
        if perspective.shared:
            rendered = re.sub(r"\bthey\b", "we", rendered, flags=re.IGNORECASE)
            rendered = re.sub(r"\btheir\b", "our", rendered, flags=re.IGNORECASE)
            rendered = re.sub(r"\bthem\b", "us", rendered, flags=re.IGNORECASE)
        if perspective.household:
            rendered = re.sub(r"\bOur home has\b", "We have", rendered, flags=re.IGNORECASE)
            named = re.search(
                r"\bWe have\s+([A-Za-z][A-Za-z -]*s)\s+named\s+"
                r"([^,.]+(?:\s+and\s+[^,.]+))([.]?)$",
                rendered, flags=re.IGNORECASE,
            )
            if named and len(perspective.other_names) >= 2:
                rendered = (
                    f"We have {len(perspective.other_names)} {named.group(1)}, "
                    f"{named.group(2)}{named.group(3)}"
                )
        if title:
            return rendered
        return rendered[0].upper() + rendered[1:] if rendered else rendered

    @staticmethod
    def _remove_duplicate_identity_claims(details, existing_facts):
        if details is None or not details.identity_facts:
            return details
        existing = {
            (
                str(getattr(fact.fact_type, "value", fact.fact_type)),
                _key(fact.value),
                _key(fact.relationship),
            )
            for fact in existing_facts
        }
        claims = [
            claim for claim in details.identity_facts
            if (
                str(getattr(claim.fact_type, "value", claim.fact_type)),
                _key(claim.value),
                _key(claim.relationship),
            ) not in existing
        ]
        return details.model_copy(update={"identity_facts": claims})

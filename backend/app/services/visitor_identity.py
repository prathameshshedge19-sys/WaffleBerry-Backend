import re
import unicodedata
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryEntity, MemoryStatus
from app.models.visitor import LegacyVisitorProfile, VisitorRelationshipStatus


RELATIONSHIPS = {
    "son": ("son", "beta", "mulga", "sohn", "बेटा", "मुलगा"),
    "daughter": ("daughter", "beti", "mulgi", "tochter", "बेटी", "मुलगी"),
    "brother": ("brother", "bhai", "bhau", "bruder", "भाई", "भाऊ"),
    "sister": ("sister", "behen", "bahin", "schwester", "बहन", "बहीण"),
    "mother": ("mother", "mom", "mum", "maa", "aai", "mutter", "माँ", "आई"),
    "father": ("father", "dad", "papa", "baba", "vater", "पिता", "बाबा"),
    "wife": ("wife", "patni", "bayko", "ehefrau", "पत्नी", "बायको"),
    "husband": ("husband", "pati", "navra", "ehemann", "पति", "नवरा"),
    "friend": ("friend", "dost", "mitra", "freund", "freundin", "दोस्त", "मित्र"),
    "neighbor": ("neighbor", "neighbour", "padosi", "shejari", "nachbar", "nachbarin", "पड़ोसी", "शेजारी"),
    "niece": ("niece", "bhatiji", "bhanji", "putani", "nichte", "भतीजी", "भांजी"),
    "nephew": ("nephew", "bhatija", "bhanja", "putanya", "neffe", "भतीजा", "भांजा"),
    "granddaughter": ("granddaughter", "poti", "natin", "enkelin", "पोती", "नात"),
    "grandson": ("grandson", "pota", "nati", "enkel", "पोता", "नातू"),
    "cousin": ("cousin", "chulat", "cousine", "चचेरा", "चुलत"),
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def current_language(text: str) -> str:
    value = normalize(text)
    if re.search(r"[\u0900-\u097f]", text):
        return "the same Devanagari language and script as the latest visitor message"
    if re.search(r"\b(ich|mein|meine|dein|deine|was|wie|warum|hallo|bin)\b", value):
        return "German"
    if re.search(r"\b(mi|tuza|tujha|mulga|mulgi|aai|aahe|mala|kay|kasa|kashi)\b", value):
        return "Romanized Marathi"
    if re.search(r"\b(main|mai|aapka|aapki|beta|beti|bhai|behen|haan|kya|kaise)\b", value):
        return "Romanized Hindi"
    return "English"


def extract_relationship(text: str) -> str | None:
    normalized = normalize(text)
    for canonical, aliases in RELATIONSHIPS.items():
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized) for alias in aliases):
            return canonical
    return None


def extract_name(text: str, allow_name_only: bool = False) -> str | None:
    compact = " ".join(text.split()).strip()
    patterns = (
        r"^(?:i am|i'm|im|my name is|this is)\s+([\p{L}][\p{L}'-]*(?:\s+[\p{L}][\p{L}'-]*){0,2})",
        r"^(?:mi|main|mai|mein)\s+([\p{L}][\p{L}'-]*)",
        r"^ich bin\s+([\p{L}][\p{L}'-]*)",
    )
    # Python's re has no \p{L}; the Unicode-aware negative separator form keeps this dependency-free.
    patterns = tuple(item.replace(r"[\p{L}]", r"[^\W\d_]").replace(r"[\p{L}'-]", r"[^\W\d_'-]") for item in patterns)
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE | re.UNICODE)
        if match:
            words = match.group(1).strip(" ,.-").split()
            relationship_words = {alias for aliases in RELATIONSHIPS.values() for alias in aliases}
            words = [word for word in words if normalize(word) not in relationship_words and normalize(word) not in {"your", "dein", "deine", "tuza", "tujha", "aapka", "aapki"}]
            return " ".join(word[:1].upper() + word[1:] for word in words) or None
    name_only = compact.strip(" .")
    if allow_name_only and re.fullmatch(r"[^\W\d_][^\d,!?;:.]{0,79}", name_only, re.UNICODE):
        words = name_only.split()
        if 1 <= len(words) <= 3 and not extract_relationship(compact):
            return " ".join(word[:1].upper() + word[1:] for word in words)
    return None


def active_memories(db: Session, legacy_id: int) -> list[Memory]:
    return list(db.scalars(select(Memory).where(Memory.legacy_id == legacy_id, Memory.status == MemoryStatus.ACTIVE.value).order_by(Memory.id)))


def _entity_for_name(db: Session, legacy_id: int, name: str) -> MemoryEntity | None:
    normalized_name = normalize(name)
    entities = db.scalars(select(MemoryEntity).where(MemoryEntity.legacy_id == legacy_id)).all()
    matches = [entity for entity in entities if normalized_name in {normalize(entity.name), *(normalize(alias) for alias in (entity.aliases or []))}]
    return matches[0] if len(matches) == 1 else None


def _supported_relationship(memories: Sequence[Memory], name: str, relationship: str) -> bool:
    name_key = normalize(name)
    aliases = RELATIONSHIPS.get(relationship, (relationship,))
    for memory in memories:
        text = normalize(memory.canonical_text)
        linked_names = {normalize(link.entity.name) for link in memory.entity_links}
        linked_names.update(normalize(alias) for link in memory.entity_links for alias in (link.entity.aliases or []))
        roles = {normalize(link.role) for link in memory.entity_links}
        name_matches = name_key in linked_names or re.search(rf"(?<!\w){re.escape(name_key)}(?!\w)", text)
        relationship_matches = relationship in roles or any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) for alias in aliases)
        if name_matches and relationship_matches:
            return True
    return False


def known_relationships(memories: Sequence[Memory], name: str) -> list[str]:
    return [relationship for relationship in RELATIONSHIPS if _supported_relationship(memories, name, relationship)]


def upsert_profile(db: Session, legacy_id: int, viewer_user_id: int, name: str, relationship: str | None) -> LegacyVisitorProfile:
    profile = db.scalar(select(LegacyVisitorProfile).where(LegacyVisitorProfile.legacy_id == legacy_id, LegacyVisitorProfile.viewer_user_id == viewer_user_id))
    if profile is None:
        profile = LegacyVisitorProfile(legacy_id=legacy_id, viewer_user_id=viewer_user_id)
        db.add(profile)
    memories = active_memories(db, legacy_id)
    entity = _entity_for_name(db, legacy_id, name)
    profile.preferred_name = name
    profile.matched_entity_id = entity.id if entity else None
    profile.claimed_relationship = relationship
    if relationship:
        profile.relationship_status = VisitorRelationshipStatus.VERIFIED_FROM_MEMORY.value if _supported_relationship(memories, name, relationship) else VisitorRelationshipStatus.UNVERIFIED.value
    else:
        supported = known_relationships(memories, name)
        if len(supported) == 1:
            profile.claimed_relationship = supported[0]
            profile.relationship_status = VisitorRelationshipStatus.CLAIMED.value
        else:
            profile.relationship_status = VisitorRelationshipStatus.CLAIMED.value
    profile.last_seen_at = datetime.now(timezone.utc)
    db.flush()
    return profile


def capture_from_message(db: Session, profile: LegacyVisitorProfile | None, legacy_id: int, viewer_user_id: int, content: str) -> LegacyVisitorProfile | None:
    yes = normalize(content).strip(" .!?") in {"yes", "yes i am", "yeah", "yep", "haan", "ha", "ho", "hoy", "ja", "जी हाँ", "हो"}
    if profile and yes and profile.preferred_name and profile.claimed_relationship and profile.relationship_status == VisitorRelationshipStatus.CLAIMED.value:
        memories = active_memories(db, legacy_id)
        profile.relationship_status = VisitorRelationshipStatus.VERIFIED_FROM_MEMORY.value if _supported_relationship(memories, profile.preferred_name, profile.claimed_relationship) else VisitorRelationshipStatus.UNVERIFIED.value
        profile.last_seen_at = datetime.now(timezone.utc); db.flush(); return profile
    relationship = extract_relationship(content)
    name = extract_name(content, allow_name_only=not profile or not profile.preferred_name)
    if not name and profile:
        name = profile.preferred_name
    if not name:
        return profile
    if relationship is None and profile and profile.claimed_relationship and profile.relationship_status != VisitorRelationshipStatus.CLAIMED.value:
        relationship = profile.claimed_relationship
    return upsert_profile(db, legacy_id, viewer_user_id, name, relationship)


def visitor_evidence(memories: Sequence[Memory], profile: LegacyVisitorProfile | None) -> dict:
    if not profile or not profile.preferred_name:
        return {"identified": False, "instruction": "Ask naturally who the visitor is, one question at a time."}
    name_key = normalize(profile.preferred_name)
    relevant = [memory for memory in memories if name_key in normalize(memory.canonical_text) or any(name_key in {normalize(link.entity.name), *(normalize(alias) for alias in (link.entity.aliases or []))} for link in memory.entity_links)]
    nicknames: list[str] = []
    for memory in relevant:
        text = memory.canonical_text
        for match in re.finditer(rf"(?:calls?|called)\s+{re.escape(profile.preferred_name)}\s+[\"'“”]?([A-Z][\w'-]{{1,30}})", text, re.IGNORECASE):
            nicknames.append(match.group(1).strip("\"'“”"))
    supported = known_relationships(memories, profile.preferred_name)
    conflict = bool(profile.claimed_relationship and supported and profile.claimed_relationship not in supported)
    safe_relevant = relevant if profile.relationship_status == VisitorRelationshipStatus.VERIFIED_FROM_MEMORY.value else [memory for memory in relevant if not re.search(r"\b(calls?|called|nickname|pet name)\b", normalize(memory.canonical_text))]
    return {
        "identified": True,
        "preferred_name": profile.preferred_name,
        "claimed_relationship": profile.claimed_relationship,
        "relationship_status": profile.relationship_status,
        "matched_entity_id": profile.matched_entity_id,
        "supported_relationships": supported,
        "claim_conflicts_with_memory": conflict,
        "visitor_specific_nicknames": list(dict.fromkeys(nicknames)) if profile.relationship_status == VisitorRelationshipStatus.VERIFIED_FROM_MEMORY.value else [],
        "visitor_specific_evidence": [{"id": memory.id, "text": memory.canonical_text} for memory in safe_relevant[:12]],
    }


def greeting(subject_name: str, profile: LegacyVisitorProfile | None) -> str:
    if not profile or not profile.preferred_name:
        return "Hi… who am I talking to?"
    relationship = f" · {profile.claimed_relationship.title()}" if profile.claimed_relationship else ""
    return f"Hi {profile.preferred_name}. It’s good to see you again."

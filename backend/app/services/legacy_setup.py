import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.legacy import Legacy, LegacySetupStatus
from app.models.user import User


RELATIONSHIP_ALIASES: dict[str, tuple[str, ...]] = {
    "mother": ("mother", "mom", "mum", "mummy", "mamma", "mama", "aai", "आई", "maa", "माँ", "मां", "mata", "माता", "mutter"),
    "father": ("father", "dad", "daddy", "papa", "baba", "बाबा", "pitaji", "पिताजी", "vater"),
    "grandmother": ("grandmother", "grandma", "granny", "aaji", "आजी", "dadi", "दादी", "nani", "नानी", "grossmutter", "großmutter"),
    "grandfather": ("grandfather", "grandpa", "granddad", "ajoba", "आजोबा", "dadaji", "दादाजी", "nana", "नाना", "grossvater", "großvater"),
    "wife": ("wife", "patni", "पत्नी", "ehefrau"),
    "husband": ("husband", "pati", "पति", "ehemann"),
    "spouse": ("spouse",),
    "partner": ("partner", "partnerin"),
    "sister": ("sister", "behen", "बहन", "bahin", "बहिण", "schwester"),
    "brother": ("brother", "bhai", "भाई", "bhau", "भाऊ", "bruder"),
    "daughter": ("daughter", "beti", "बेटी", "mulgi", "मुलगी", "tochter"),
    "son": ("son", "beta", "बेटा", "mulga", "मुलगा", "sohn"),
    "friend": ("friend", "best friend", "dost", "दोस्त", "mitra", "मित्र", "freund", "freundin"),
}

SELF_PHRASES = (
    "myself", "my own legacy", "own legacy", "for me", "for myself", "about me",
    "majhyasathi", "majhya sathi", "mazyasathi", "mere liye", "khud ke liye",
    "खुद के लिए", "मेरे लिए", "für mich", "fuer mich",
)
OTHER_PHRASES = (
    "someone i love", "someone else", "somebody else", "someone i care about",
    "kisi aur", "दूसरे के लिए", "jemand anderen", "jemanden anderen",
)
CONNECTORS = {
    "named", "called", "name", "is", "whose", "naam", "nav", "नाव", "नाम",
    "ke", "ki", "ka", "liye", "sathi", "साठी", "für", "fuer", "meine", "mein",
}
NON_NAMES = {
    "legacy", "one", "someone", "somebody", "person", "love", "care", "create", "make",
    "build", "preserve", "this", "them", "him", "her", "it", "please", "yes", "yeah",
    "for", "my", "our", "your", "meri", "mera", "mere", "majhya", "majhi", "majha",
    "meine", "mein", "für", "fuer",
}


@dataclass(frozen=True)
class LegacyExtraction:
    target_type: str | None = None
    subject_name: str | None = None
    relationship: str | None = None


@dataclass(frozen=True)
class SetupUpdate:
    changed: bool
    activated: bool
    extraction: LegacyExtraction


def _words(value: str) -> list[str]:
    return re.findall(r"[^\W\d_][\w'’-]*", value, flags=re.UNICODE)


def _normalized(value: str) -> str:
    return " ".join(word.casefold() for word in _words(value))


def _valid_name(words: list[str]) -> str | None:
    cleaned = [word.strip("'’-") for word in words if word.strip("'’-")]
    if not cleaned or len(cleaned) > 4:
        return None
    lowered = {word.casefold() for word in cleaned}
    if lowered & (CONNECTORS | NON_NAMES):
        return None
    if any(not all(character.isalpha() or character in "-'’" for character in word) for word in cleaned):
        return None
    value = " ".join(cleaned).strip()
    return value[:255] if len(value) >= 2 else None


def _relationship_match(words: list[str]) -> tuple[str | None, int, int]:
    lowered = [word.casefold() for word in words]
    best: tuple[str | None, int, int] = (None, -1, -1)
    for canonical, aliases in RELATIONSHIP_ALIASES.items():
        for alias in aliases:
            alias_words = _words(alias.casefold())
            width = len(alias_words)
            for index in range(len(lowered) - width + 1):
                if lowered[index:index + width] == alias_words and width > best[2] - best[1]:
                    best = (canonical, index, index + width)
    return best


def _name_near_relationship(words: list[str], start: int, end: int) -> str | None:
    following = words[end:]
    while following and following[0].casefold() in CONNECTORS:
        following = following[1:]
    if following:
        candidate = _valid_name([following[0]])
        if candidate:
            return candidate
    preceding = words[:start]
    while preceding and preceding[-1].casefold() in NON_NAMES | CONNECTORS:
        preceding = preceding[:-1]
    if preceding:
        candidate = _valid_name([preceding[-1]])
        if candidate and preceding[-1][:1].isupper():
            return candidate
    return None


def extract_legacy_identity(text: str, legacy: Legacy | None = None) -> LegacyExtraction:
    words = _words(text)
    normalized = _normalized(text)
    target_type = None
    relationship, relation_start, relation_end = _relationship_match(words)
    padded = f" {normalized} "
    if any(f" {phrase} " in padded for phrase in SELF_PHRASES):
        target_type = "self"
        relationship = "self"
    elif relationship or any(f" {phrase} " in padded for phrase in OTHER_PHRASES):
        target_type = "other"

    subject_name = None
    self_name_patterns = (
        r"\b(?:i am|i'm|my name is)\s+([^,.!?]+)",
        r"\b(?:mera naam|majha nav|majhe nav|mein name ist|ich bin)\s+([^,.!?]+)",
    )
    if target_type == "self":
        for pattern in self_name_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = re.split(r"\b(?:and|ani|aur|und)\b", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
                subject_name = _valid_name(_words(candidate)[:4])
                if subject_name:
                    break
    elif relationship:
        subject_name = _name_near_relationship(words, relation_start, relation_end)

    needs_name = legacy is not None and legacy.setup_status == LegacySetupStatus.COLLECTING_IDENTITY.value and legacy.subject_name is None and legacy.is_self is not None
    if not subject_name and needs_name and not relationship and target_type is None:
        subject_name = _valid_name(words)

    return LegacyExtraction(target_type=target_type, subject_name=subject_name, relationship=relationship)


def missing_setup_fields(legacy: Legacy) -> list[str]:
    missing = []
    if legacy.is_self is None:
        missing.append("target_type")
    if legacy.subject_name is None:
        missing.append("subject_name")
    if legacy.is_self is False and legacy.relationship_to_owner is None:
        missing.append("relationship")
    return missing


def apply_setup_message(legacy: Legacy, content: str, default_self_name: str | None = None) -> SetupUpdate:
    if legacy.setup_status != LegacySetupStatus.COLLECTING_IDENTITY.value:
        return SetupUpdate(False, False, LegacyExtraction())
    extraction = extract_legacy_identity(content, legacy)
    changed = False
    if legacy.is_self is None and extraction.target_type:
        legacy.is_self = extraction.target_type == "self"
        changed = True
    if legacy.is_self is True and legacy.relationship_to_owner != "self":
        legacy.relationship_to_owner = "self"
        changed = True
    if legacy.is_self is False and legacy.relationship_to_owner is None and extraction.relationship not in {None, "self"}:
        legacy.relationship_to_owner = extraction.relationship
        changed = True
    if legacy.subject_name is None and extraction.subject_name:
        legacy.subject_name = extraction.subject_name
        changed = True
    if legacy.is_self is True and legacy.subject_name is None and default_self_name:
        profile_name = _valid_name(_words(default_self_name))
        if profile_name:
            legacy.subject_name = profile_name
            changed = True
    activated = not missing_setup_fields(legacy)
    if activated:
        legacy.setup_status = LegacySetupStatus.ACTIVE.value
        changed = True
    return SetupUpdate(changed, activated, extraction)


def create_collecting_legacy(db: Session, user: User) -> Legacy:
    legacy = Legacy(owner_user_id=user.id, setup_status=LegacySetupStatus.COLLECTING_IDENTITY.value)
    db.add(legacy)
    db.flush()
    user.active_legacy_id = legacy.id
    return legacy


def owned_legacy(db: Session, user_id: int, legacy_id: int) -> Legacy | None:
    return db.scalar(select(Legacy).where(Legacy.id == legacy_id, Legacy.owner_user_id == user_id))


def active_or_new_legacy(db: Session, user: User) -> Legacy:
    legacy = owned_legacy(db, user.id, user.active_legacy_id) if user.active_legacy_id else None
    if legacy is None or legacy.setup_status == LegacySetupStatus.ARCHIVED.value:
        legacy = create_collecting_legacy(db, user)
    return legacy


def setup_system_context(legacy: Legacy, activated_now: bool = False) -> str:
    missing = missing_setup_fields(legacy)
    target = "self" if legacy.is_self is True else "other" if legacy.is_self is False else "unknown"
    return f"""Authoritative Legacy identity context for this conversation:
- Rya is the AI companion. Rya is never the Legacy subject and must never impersonate the subject.
- setup_status: {legacy.setup_status}
- target_type: {target}
- subject_name: {legacy.subject_name or 'unknown'}
- relationship_to_owner: {legacy.relationship_to_owner or 'unknown'}
- missing_fields: {', '.join(missing) if missing else 'none'}
- setup_completed_on_this_turn: {'yes' if activated_now else 'no'}

Follow these rules:
- Treat this state as authoritative and do not ask again for fields already known.
- If setup is incomplete, ask one warm, natural question for the next missing field.
- If setup just completed, briefly acknowledge whose Legacy you are building and invite the user to continue naturally.
- If setup_status is active, never return to beginner onboarding or ask who the Legacy is for. Continue from the current thread and established memories.
- Continue in the user's current language or mixed-language style.
- Do not claim long-term memory extraction, a memory graph, or the subject's identity or memories."""

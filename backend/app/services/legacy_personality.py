"""Conservative, deterministic evidence derivation. No LLM or chat integration.

Coverage intentionally favors omission over invented personality. All output is
untrusted structured data; a future consumer must not interpolate it as policy.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from sqlalchemy import exists, or_, select

from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink
from app.models.personality import LegacyPersonalityProfile
from app.schemas.personality import (
    EvidenceContext, EvidenceRef, PersonalityObservation, PersonalityProfile,
    SignatureExpression, SourceSpan,
)

SCHEMA_VERSION = 1
POLICY_VERSION = "l13-conservative-v1"
BUILDER_ID = "deterministic-evidence-v1"
MAX_MEMORIES = 2048
_QUOTES = re.compile(r'"([^"\n]{1,240})"|\u201c([^\u201d\n]{1,240})\u201d|\u2018([^\u2019\n]{1,240})\u2019')
_INSTRUCTION = re.compile(r"ignore\b.{0,80}\binstructions|system\s*prompt|developer\s*message|<\|(?:system|assistant)|\b(?:execute|run)\s+(?:this|the)\s+(?:code|command)", re.I)
_TRAITS = {
    "straightforward": ("directness", "straightforward"), "direct": ("directness", "straightforward"),
    "blunt": ("directness", "blunt"), "indirect": ("directness", "indirect"),
    "quiet": ("sociability", "quiet"), "reserved": ("sociability", "quiet"),
    "talkative": ("sociability", "talkative"), "outgoing": ("sociability", "outgoing"),
    "warm": ("warmth", "warm"), "affectionate": ("warmth", "affectionate"),
    "gentle": ("warmth", "gentle"), "distant": ("warmth", "distant"),
    "playful": ("humor", "playful"), "witty": ("humor", "witty"),
    "serious": ("humor", "serious"), "thoughtful": ("deliberateness", "thoughtful"),
    "careful": ("deliberateness", "careful"),
}
_VALUES = ("family", "education", "kindness", "discipline", "creativity", "independence", "honesty", "tradition", "ambition")
_OPPOSITES = ({"quiet", "talkative"}, {"quiet", "outgoing"}, {"straightforward", "indirect"}, {"blunt", "indirect"}, {"warm", "distant"}, {"affectionate", "distant"}, {"playful", "serious"}, {"witty", "serious"})


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class MemoryEvidence:
    id: int
    legacy_id: int
    canonical_text: str
    category: str
    subject_name: str
    subject_reference: str
    source_excerpt: str = ""
    source_language: str = "unknown"
    story_key: str | None = None
    source_conversation_id: int | None = None
    source_message_id: int | None = None
    operation_type: str = "new"
    entities: tuple[tuple[int, str, str], ...] = ()

    @property
    def fingerprint(self):
        return _digest(asdict(self))

    @property
    def independence_key(self):
        # Unknown provenance is NOT treated as independent corroboration.
        group = ("story", self.story_key) if self.story_key else (
            ("conversation", self.source_conversation_id) if self.source_conversation_id else ("unprovenanced", None)
        )
        return _digest((self.legacy_id, group))


def load_evidence(db, legacy_id):
    legacy = db.get(Legacy, legacy_id)
    if legacy is None:
        return ()
    from app.models.media_intelligence import MemorySourceLink, SupportState
    source_link = exists(select(MemorySourceLink.id).where(MemorySourceLink.memory_id == Memory.id, MemorySourceLink.legacy_id == legacy_id))
    approved_source_link = exists(select(MemorySourceLink.id).where(MemorySourceLink.memory_id == Memory.id, MemorySourceLink.legacy_id == legacy_id, MemorySourceLink.support_state == SupportState.APPROVED.value, MemorySourceLink.removed_at.is_(None)))
    usable = or_(~source_link, approved_source_link)
    memories = db.scalars(select(Memory).where(Memory.legacy_id == legacy_id, Memory.status == "active", usable).order_by(Memory.id).limit(MAX_MEMORIES + 1)).all()
    if len(memories) > MAX_MEMORIES:
        raise ValueError("Personality evidence budget exceeded")
    entity_rows = db.execute(
        select(MemoryEntityLink.memory_id, MemoryEntity.id, MemoryEntity.name, MemoryEntityLink.role)
        .join(MemoryEntity, MemoryEntity.id == MemoryEntityLink.entity_id)
        .join(Memory, Memory.id == MemoryEntityLink.memory_id)
        .where(Memory.legacy_id == legacy_id, MemoryEntity.legacy_id == legacy_id, Memory.status == "active", usable)
        .order_by(MemoryEntity.id)
    ).all()
    entities = {}
    for memory_id, entity_id, name, role in entity_rows:
        entities.setdefault(memory_id, []).append((entity_id, name, role))
    return tuple(MemoryEvidence(
        id=m.id, legacy_id=legacy_id, canonical_text=m.canonical_text,
        category=m.category, subject_name=legacy.subject_name or "",
        subject_reference=m.subject_reference or "", source_excerpt=m.source_excerpt or "",
        source_language=m.source_language or "unknown", story_key=m.story_key,
        source_conversation_id=m.source_conversation_id, source_message_id=m.source_message_id,
        operation_type=m.operation_type, entities=tuple(entities.get(m.id, ())),
    ) for m in memories)


def _subject_pattern(memory):
    if not memory.subject_name.strip():
        return r"(?!)"
    names = [re.escape(memory.subject_name.strip())]
    if memory.subject_reference.casefold().strip() == memory.subject_name.casefold().strip():
        names += ["she", "he", "they"]
    return "(?:" + "|".join(names) + ")"


def _context(tail, memory):
    qualification = tail.strip(" .,;:") or None
    if qualification and len(qualification) > 600:
        raise ValueError("Context exceeds evidence budget")
    def extract(pattern):
        match = re.search(pattern, tail, re.I)
        return match.group(1).strip(" .,;")[:160] if match else None
    entity_ids = tuple(dict.fromkeys(entity_id for entity_id, name, _role in memory.entities if re.search(r"\b" + re.escape(name) + r"\b", tail, re.I)))[:16]
    return EvidenceContext(
        qualification=qualification,
        relationship=extract(r"\b(?:with|around|to)\s+(?:her |his |their )?(family|strangers|children|friends|son|daughter|husband|wife|colleagues)\b"),
        entity_ids=entity_ids,
        setting=extract(r"\b(?:at|in)\s+([^,;.]+)"),
        topic=extract(r"\b(?:about|regarding)\s+([^,;.]+)"),
        time=extract(r"\b((?:in|during|before|after)\s+(?:\d{4}|childhood|adulthood|retirement))\b"),
    )


def _reference(memory, text, field="canonical_text", start=None):
    source = getattr(memory, field)
    start = source.index(text) if start is None else start
    return EvidenceRef(memory_id=memory.id, content_fingerprint=memory.fingerprint,
        independence_key=memory.independence_key,
        span=SourceSpan(field=field, start=start, end=start + len(text), text=text))


def _observation(memory, sentence, dimension, trait, evidence_type, tail):
    explicit = evidence_type == "explicit_description"
    return PersonalityObservation(
        dimension=dimension, trait=trait, description=sentence,
        evidence_type=evidence_type, evidence=(_reference(memory, sentence),), context=_context(tail, memory),
        confidence="supported" if explicit or evidence_type == "habitual_account" else "tentative",
        confidence_reason="Explicit preserved description; not a calibrated probability." if explicit else "Reported behavior is contextual, not a universal personality claim.",
        response_style_eligible=explicit and not _INSTRUCTION.search(sentence),
    )


def _observations(memory):
    subject = _subject_pattern(memory)
    adjective = "|".join(sorted(_TRAITS, key=len, reverse=True))
    for part in re.finditer(r"[^.!?;\n]+[.!?;]?", memory.canonical_text):
        sentence = part.group().strip()
        if not sentence or len(sentence) > 600 or _INSTRUCTION.search(sentence):
            continue
        match = re.fullmatch(subject + r"\s+(?:was|is|were|are)\s+(?:(?:very|extremely|quite|usually|always)\s+)?(" + adjective + r")\b(.*)", sentence, re.I)
        if match:
            dimension, trait = _TRAITS[match.group(1).lower()]
            yield _observation(memory, sentence, dimension, trait, "explicit_description", match.group(2))
            continue
        match = re.fullmatch(subject + r"\s+(?:valued|values|prioritized|prioritizes|believed in)\s+(" + "|".join(_VALUES) + r")\b(.*)", sentence, re.I)
        if match:
            yield _observation(memory, sentence, "value", match.group(1).lower(), "explicit_description", match.group(2))
            continue
        match = re.fullmatch(subject + r"\s+(?:(always|often|sometimes|once)\s+)?teased\s+(.+)", sentence, re.I)
        if match:
            kind = "habitual_account" if match.group(1) in ("always", "often") else "behavioral_inference"
            yield _observation(memory, sentence, "humor", "contextual_teasing", kind, "with " + match.group(2))
            continue
        # Values from a reported act remain tentative, never response policy.
        match = re.fullmatch(subject + r"\s+(?:paid|helped pay)\s+(.+school fees.*)", sentence, re.I)
        if match:
            yield _observation(memory, sentence, "value", "education", "behavioral_inference", match.group(1))


def _script(text):
    devanagari = bool(re.search(r"[\u0900-\u097f]", text))
    latin = bool(re.search(r"[A-Za-z]", text))
    return "Mixed" if devanagari and latin else "Devanagari" if devanagari else "Latin" if latin else "Other"


def _expressions(memory):
    # Edits/enrichments retain old source excerpts in L12: never reuse those as
    # authenticated wording. A new, explicitly sourced memory can restore it.
    if memory.operation_type in ("edit", "enrich") or not memory.source_excerpt:
        return
    attribution = re.match(_subject_pattern(memory) + r"\s+(?:(always|often|sometimes|once)\s+)?(?:said|says|used to say|called)\b", memory.canonical_text, re.I)
    if not attribution:
        return
    source_quotes = {next(value for value in match.groups() if value is not None) for match in _QUOTES.finditer(memory.source_excerpt)}
    for match in _QUOTES.finditer(memory.canonical_text):
        expression = next(value for value in match.groups() if value is not None)
        if expression not in source_quotes:
            continue
        tail = memory.canonical_text[attribution.end():match.start()] + memory.canonical_text[match.end():]
        yield SignatureExpression(
            expression=expression, original_language=memory.source_language[:64], original_script=_script(expression),
            context=_context(tail, memory), reported_frequency=(attribution.group(1) or "unspecified").lower(),
            confidence="supported", evidence=(_reference(memory, expression, "source_excerpt"),),
            # Nothing in Phase B is injected. Instruction-like quotes are also
            # explicitly ineligible for any later response-style selector.
            response_style_eligible=not bool(_INSTRUCTION.search(expression)),
        )


def derive_profile(legacy_id, generation, memories):
    memories = tuple(memories)
    if len(memories) > MAX_MEMORIES or len({m.id for m in memories}) != len(memories):
        raise ValueError("Invalid evidence set")
    observations, expressions = [], []
    for memory in memories:
        if memory.legacy_id != legacy_id or len(memory.canonical_text) > 12000 or len(memory.source_excerpt) > 12000:
            raise ValueError("Invalid evidence scope or length")
        observations.extend(_observations(memory))
        expressions.extend(_expressions(memory))
    # Merge only genuinely matching contextual observations. Same-story and
    # same-conversation fragments cannot inflate independence/confidence.
    merged = {}
    for observation in observations:
        key = (observation.dimension, observation.trait, observation.evidence_type, observation.context.model_dump_json())
        if key not in merged:
            merged[key] = observation
            continue
        previous = merged[key]
        refs = tuple({ref.memory_id: ref for ref in (*previous.evidence, *observation.evidence)}.values())[:8]
        independent = len({ref.independence_key for ref in refs}) >= 2
        explicit = previous.evidence_type == "explicit_description"
        merged[key] = previous.model_copy(update={
            "evidence": refs,
            "confidence": "corroborated" if independent and explicit else previous.confidence,
            "confidence_reason": "Matching explicit descriptions in independent preserved accounts." if independent and explicit else previous.confidence_reason,
        })
    observations = list(merged.values())
    for index, observation in enumerate(observations):
        conflicts = set()
        for other in observations:
            if other.context == observation.context and other.dimension == observation.dimension and {other.trait, observation.trait} in _OPPOSITES:
                conflicts.update(ref.memory_id for ref in other.evidence)
        if conflicts:
            observations[index] = observation.model_copy(update={"conflict_memory_ids": tuple(sorted(conflicts))[:64], "response_style_eligible": False, "confidence": "tentative", "confidence_reason": "Unresolved opposing descriptions in the same recorded context."})
    profile = PersonalityProfile(
        legacy_id=legacy_id, source_generation=generation,
        observations=tuple(observations[:48]), signature_expressions=tuple(expressions[:16]),
        evidence_manifest={m.id: m.fingerprint for m in memories},
    )
    validate_profile(profile, legacy_id, generation, memories)
    return profile


def validate_profile(profile, legacy_id, generation, memories):
    profile = PersonalityProfile.model_validate(profile.model_dump() if isinstance(profile, PersonalityProfile) else profile)
    sources = {memory.id: memory for memory in memories}
    if profile.legacy_id != legacy_id or profile.source_generation != generation or any(memory.legacy_id != legacy_id for memory in memories):
        raise ValueError("Profile scope/generation mismatch")
    if profile.evidence_manifest != {key: value.fingerprint for key, value in sources.items()}:
        raise ValueError("Profile evidence manifest mismatch")
    for item in (*profile.observations, *profile.signature_expressions):
        linked_entities = set()
        for reference in item.evidence:
            memory = sources.get(reference.memory_id)
            if memory is None or memory.fingerprint != reference.content_fingerprint or memory.independence_key != reference.independence_key:
                raise ValueError("Unverified personality evidence")
            span = reference.span
            if getattr(memory, span.field)[span.start:span.end] != span.text:
                raise ValueError("Unverified source span")
            linked_entities.update(entity[0] for entity in memory.entities)
            if isinstance(item, SignatureExpression):
                verified = {expression.expression for expression in _expressions(memory)}
                if item.expression not in verified or span.field != "source_excerpt" or span.text != item.expression:
                    raise ValueError("Unverified original expression")
        if not set(item.context.entity_ids).issubset(linked_entities):
            raise ValueError("Unverified entity context")
        if isinstance(item, PersonalityObservation) and not set(item.conflict_memory_ids).issubset(sources):
            raise ValueError("Unverified conflict reference")
    if len(profile.model_dump_json().encode()) > 131072:
        raise ValueError("Profile output budget exceeded")
    return profile


def current_profile(db, legacy_id):
    """Read-only freshness gate. No rebuild, queue write, or provider invocation.

    Not connected to persona routes in Phase B. Fresh SELECT avoids identity-map
    reuse of a projection invalidated on the transaction's underlying connection.
    """
    row = db.scalar(select(LegacyPersonalityProfile).where(LegacyPersonalityProfile.legacy_id == legacy_id).execution_options(populate_existing=True))
    if row is None or row.build_status != "ready" or row.built_generation != row.source_generation or row.schema_version != SCHEMA_VERSION or row.policy_version != POLICY_VERSION or row.builder_id != BUILDER_ID or row.profile_json is None:
        return None
    try:
        profile = PersonalityProfile.model_validate(row.profile_json)
        if profile.legacy_id != legacy_id or profile.source_generation != row.source_generation:
            return None
        return profile
    except ValueError:
        return None

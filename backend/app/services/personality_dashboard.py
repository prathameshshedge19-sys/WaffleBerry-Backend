"""Authenticated callers get only current, evidence-validated display summaries."""

import re

from sqlalchemy import select

from app.models.personality import LegacyPersonalityProfile
from app.schemas.personality_dashboard import ExpressionSummary, ObservationSummary, PersonalityDashboard
from app.services.legacy_personality import _INSTRUCTION, current_profile, load_evidence, validate_profile
from app.services.personality_style import _validated_observations

_DIMENSIONS = {"directness": "Communication", "sociability": "Social presence", "warmth": "Warmth", "humor": "Humor & expression", "deliberateness": "Thoughtfulness", "value": "Values"}
_CONFIDENCE = {"corroborated": "Well supported", "supported": "Supported", "tentative": "Tentative"}
_LANGUAGES = {"english": "English", "marathi": "Marathi", "hindi": "Hindi", "german": "German", "mixed": "Mixed languages", "romanized_marathi": "Marathi in Latin script", "romanized_hindi": "Hindi in Latin script"}


def _description(text, subject):
    # Remove only a redundant subject/copula, never strengthen the evidence.
    names = [re.escape(subject)] if subject else []
    names.extend(["she", "he", "they"])
    text = re.sub(r"^(?:" + "|".join(names) + r")\s+(?:(?:was|were|is|are)\s+)?", "", text, count=1, flags=re.I)
    return text[:1].upper() + text[1:]


def read_personality_dashboard(db, legacy):
    def state(status):
        return PersonalityDashboard(legacy_id=legacy.id, status=status)
    try:
        with db.no_autoflush:
            with db.connection().begin_nested():
                row = db.scalar(select(LegacyPersonalityProfile).where(LegacyPersonalityProfile.legacy_id == legacy.id).execution_options(populate_existing=True))
                if row is None:
                    return state("unavailable")
                if row.build_status == "failed":
                    return state("failed")
                if row.build_status in ("pending", "building"):
                    return state("rebuilding")
                if row.built_generation != row.source_generation:
                    return state("stale")
                profile = current_profile(db, legacy.id)
                if profile is None:
                    return state("unavailable")
                memories = load_evidence(db, legacy.id)
                try:
                    profile = validate_profile(profile, legacy.id, profile.source_generation, memories)
                    _validated_observations(profile, {memory.id: memory for memory in memories})
                except ValueError:
                    return state("stale")
                observations = []
                for item in profile.observations:
                    ids = sorted({reference.memory_id for reference in item.evidence})
                    observations.append(ObservationSummary(
                        description=_description(item.description, legacy.subject_name),
                        dimension=_DIMENSIONS[item.dimension], confidence=_CONFIDENCE[item.confidence],
                        context=item.context.qualification, conflicting_accounts=bool(item.conflict_memory_ids),
                        supporting_memory_ids=ids, supporting_memory_count=len(ids),
                    ))
                expressions = []
                for item in profile.signature_expressions:
                    if not item.response_style_eligible or _INSTRUCTION.search(item.expression):
                        continue
                    ids = sorted({reference.memory_id for reference in item.evidence})
                    expressions.append(ExpressionSummary(
                        expression=item.expression,
                        context=f"Used {item.context.qualification}" if item.context.qualification else "Preserved original wording",
                        language=_LANGUAGES.get(item.original_language, "Original language"),
                        supporting_memory_ids=ids, supporting_memory_count=len(ids),
                    ))
                latest = current_profile(db, legacy.id)
                if latest is None or latest.source_generation != profile.source_generation:
                    return state("stale")
                return PersonalityDashboard(legacy_id=legacy.id, status="ready", observations=observations, signature_expressions=expressions)
    except Exception:
        # Only derived-data failures are contained here; authorization happens
        # outside this function and must still reject unauthorized requests.
        return state("unavailable")

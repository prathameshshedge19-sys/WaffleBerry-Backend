"""Companion chat orchestration and approved-memory grounding."""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
import re
from uuid import uuid4
from types import SimpleNamespace

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.user import Conversation, Message
from app.crud.memory import LegacyCRUD
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder, ConversationMessage
from app.services.ai.provider import AIMessage
from app.services.ai.exceptions import AIInvalidResponseError, MemoryGroundingError
from app.services.ai.exceptions import AIProviderError
from app.services.ai.external_knowledge import (
    attach_external_context,
    attach_web_failure_context,
    ExternalKnowledgeClassifier,
    QueryKnowledgeMode,
)
from app.services.ai.provider import ExternalKnowledgeMode
from app.services.conversation_continuity import ConversationContinuity
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.identity_retrieval import (
    detect_identity_intent,
    IdentityFactRetrievalService,
    IdentityGroundingResult,
)
from app.services.memory.fidelity import (
    MemoryFidelityAnalyzer,
    MemoryFidelityService,
)
from app.services.memory.multilingual_retrieval import detect_query_language_mode
from app.services.language_normalization import LanguageNormalizationService
from app.services.memory.name_resolution import (
    comparable_name,
    NameResolution,
    ProperNameResolver,
)
from app.services.memory.retrieval import (
    MemoryRetrievalArchivedError,
    MemoryRetrievalNotFoundError,
    MemoryRetrievalService,
)
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker
from app.services.persona_profile import PersonaProfile, PersonaProfileService
from app.services.grounded_answer import GroundedAnswerService
from app.services.memory.legacy_context import LegacyMemoryEngine


logger = logging.getLogger(__name__)


def _safe_log(level: int, event: str, **metadata) -> None:
    """Emit journal-visible structured metadata with no story or prompt text."""
    logger.log(
        level,
        json.dumps(
            {"event": event, **metadata},
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


@dataclass(frozen=True)
class EvidenceGroup:
    kind: str
    evidence_id: int | None
    epistemic_status: str
    payload: dict


@dataclass(frozen=True)
class GroundedTurnContext:
    selected_identity_fact_ids: tuple[int, ...] = ()
    selected_memory_ids: tuple[int, ...] = ()
    evidence_groups: tuple[EvidenceGroup, ...] = ()
    resolved_entities: tuple[str, ...] = ()
    topic_anchor: str = ""
    fact_confidence: str = "unsupported"
    coverage: str = "none"
    conflict_count: int = 0
    uncertain: bool = False
    supported_relevant_evidence_count: int = 0
    fallback_search_attempted: bool = False
    retrieval_status: str = "ok"


@dataclass(frozen=True)
class PreparedCompanionInput:
    messages: list[AIMessage]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""
    query_mode: QueryKnowledgeMode = "autobiographical_memory"
    external_knowledge_mode: ExternalKnowledgeMode | None = None
    external_lookup_messages: tuple[AIMessage, ...] = ()
    grounding_chars: int = 0
    identity_context_chars: int = 0
    identity_direct: bool = False
    identity_count: int = 0
    conflict_count: int = 0
    has_uncertainty: bool = False
    resolved_entities: tuple[str, ...] = ()
    memory_evidence: tuple[dict, ...] = ()
    identity_evidence: tuple[dict, ...] = ()
    approved_candidate_count: int = 0
    matched_candidate_count: int = 0
    query_intent: str = "unknown"
    query_language_mode: str = "unknown"
    query_broad: bool = False
    fact_confidence: str = "unsupported"
    coverage: str = "none"
    grounded_turn: GroundedTurnContext = GroundedTurnContext()
    fallback_search_attempted: bool = False
    retrieval_status: str = "ok"
    profile_engine_invoked: bool = False
    profile_fact_count: int = 0
    detailed_memory_count: int = 0


@dataclass(frozen=True)
class CompanionGeneration:
    content: str
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""


@dataclass(frozen=True)
class CompanionStreamPlan:
    stream: AsyncIterator[str]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""


class ChatService:
    @staticmethod
    def _memory_perspective(memory, legacy_name: str) -> dict:
        """Classify grammatical perspective from source-grounded participant roles."""
        subjects = [
            name for name, role in zip(
                memory.participant_names, memory.participant_roles, strict=False,
            )
            if role == "subject"
        ]
        if not subjects:
            return {"subject": "uncertain", "subjects": []}
        legacy_key = comparable_name(legacy_name)
        perspectives = [
            "self" if comparable_name(name) == legacy_key else name
            for name in subjects
        ]
        perspectives = list(dict.fromkeys(perspectives))
        if len(perspectives) == 1:
            return {"subject": perspectives[0], "subjects": perspectives}
        return {"subject": "multiple", "subjects": perspectives}

    @staticmethod
    def _memory_entities(memory, legacy_name: str) -> list[str]:
        """Expose grounded named entities without treating the Persona as an answer entity."""
        legacy_key = comparable_name(legacy_name)
        names = [
            name for name in memory.participant_names
            if comparable_name(name) != legacy_key
        ]
        details = getattr(memory, "details", None)
        extra = getattr(details, "model_extra", None) or {}
        for value in extra.values():
            if isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        for source in (memory.title, memory.summary):
            names.extend(
                match.strip(" .,'\"")
                for match in re.findall(
                    r"\bnamed\s+([^,.;]+?)(?=\s+(?:who|that|which|and)\b|$)",
                    source,
                    flags=re.IGNORECASE,
                )
                if match.strip(" .,'\"")
            )
        return list(dict.fromkeys(names))

    @staticmethod
    def _unique_names(names) -> tuple[str, ...]:
        result = []
        seen = set()
        for name in names:
            key = comparable_name(name)
            if key and key not in seen:
                seen.add(key)
                result.append(name)
        return tuple(result)

    """Load conversation context and prepare provider-neutral AI input."""

    def __init__(
        self,
        ai_service: AIService,
        context_builder: ContextBuilder,
        memory_retrieval: MemoryRetrievalService | None = None,
        memory_grounding: CompanionMemoryGrounding | None = None,
        persona_profiles: PersonaProfileService | None = None,
        memory_fidelity: MemoryFidelityService | None = None,
        conversation_continuity: ConversationContinuity | None = None,
        identity_retrieval: IdentityFactRetrievalService | None = None,
        name_resolver: ProperNameResolver | None = None,
        legacy_memory_engine: LegacyMemoryEngine | None = None,
    ) -> None:
        self._ai_service = ai_service
        self._context_builder = context_builder
        self._memory_retrieval = memory_retrieval or MemoryRetrievalService()
        self._memory_grounding = memory_grounding or CompanionMemoryGrounding()
        self._persona_profiles = persona_profiles or PersonaProfileService()
        self._memory_fidelity = memory_fidelity or MemoryFidelityService()
        self._conversation_continuity = (
            conversation_continuity or ConversationContinuity()
        )
        self._identity_retrieval = (
            identity_retrieval or IdentityFactRetrievalService()
        )
        self._name_resolver = name_resolver or ProperNameResolver()
        self._legacy_memory_engine = legacy_memory_engine or LegacyMemoryEngine(
            retrieval=self._memory_retrieval,
        )

    def prepare_ai_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
    ) -> list[AIMessage]:
        """Return provider messages while retaining the established contract."""
        return self.prepare_grounded_personal_turn(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
        ).messages

    def bounded_conversation_history(
        self, db: Session, conversation: Conversation,
    ) -> tuple[dict[str, str], ...]:
        """Return the canonical Chat-sized history window for another modality."""
        history = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.conversation_id)
            .order_by(Message.created_at.desc(), Message.message_id.desc())
            .limit(self._context_builder.history_query_limit)
            .all()
        )
        history.reverse()
        items = []
        for message in history:
            role = self._context_builder._normalize_role(message.role)
            content = message.content.strip()
            if role in {"user", "assistant"} and content:
                items.append({"role": role, "content": content})
        return tuple(items)

    def prepare_grounded_personal_turn(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *,
        history_override: Iterable[ConversationMessage] | None = None,
        live_call: bool = False,
        conversation_style: str = "natural",
        response_length: str = "balanced",
        semantic_message_override: str | None = None,
    ) -> PreparedCompanionInput:
        """Run the one bounded canonical identity-and-memory grounding preparation."""
        if not isinstance(user_message, str) or not user_message.strip():
            raise AIInvalidResponseError("A substantive user message is required.")
        if history_override is None:
            history = (
                db.query(Message)
                .filter(Message.conversation_id == conversation.conversation_id)
                .order_by(
                    Message.created_at.desc(),
                    Message.message_id.desc(),
                )
                .limit(self._context_builder.history_query_limit)
                .all()
            )
            history.reverse()
        else:
            history = list(history_override)[-self._context_builder.history_query_limit:]
        grounding_context = None
        memory_grounding_context = None
        identity_context = None
        request_id = uuid4().hex
        memory_ids: tuple[int, ...] = ()
        memory_evidence: tuple[dict, ...] = ()
        identity_evidence: tuple[dict, ...] = ()
        approved_candidate_count = 0
        matched_candidate_count = 0
        retrieved_at = None
        persona_display_name = None
        persona_relationship = None
        retrieval_available = True
        fallback_search_attempted = False
        persona_profile = PersonaProfile()
        fidelity_plan = MemoryFidelityAnalyzer().analyze([])
        normalized_turn = LanguageNormalizationService().normalize_user_turn(user_message)
        semantic_message = semantic_message_override or normalized_turn.normalized_english_text
        query_classification = MemoryRelevanceRanker.classify_query(semantic_message)
        response_query_classification = query_classification
        query_intent = query_classification.intent
        query_language_mode = detect_query_language_mode(user_message)
        knowledge_plan = ExternalKnowledgeClassifier.classify(semantic_message)
        name_resolution = NameResolution()
        identity_result = IdentityGroundingResult(None, None)
        legacy_id = getattr(conversation, "legacy_id", None)
        personal_turn = GroundedAnswerService.is_personal(
            semantic_message,
            active_topic=any(
                getattr(item, "role", None) in {"user", "assistant"}
                for item in history
            ),
        )
        if (
            not personal_turn
            and knowledge_plan.query_mode == "autobiographical_memory"
            and GroundedAnswerService.classify_turn(semantic_message) != "social"
        ):
            personal_turn = True
        # Older injected retrieval doubles predate the profile API. Keep their
        # established orchestration contract while production uses strict
        # PERSONAL/GENERAL/SOCIAL routing through the shared engine.
        if (
            not hasattr(self._memory_retrieval, "retrieve_approved")
            and GroundedAnswerService.classify_turn(semantic_message) != "social"
        ):
            personal_turn = True
        if legacy_id is not None and not personal_turn:
            legacy = getattr(conversation, "legacy", None)
            if legacy is None or legacy.owner_user_id != conversation.user_id:
                legacy = LegacyCRUD.get_user_legacy(
                    db, legacy_id, conversation.user_id,
                )
            if legacy is None:
                raise MemoryGroundingError("Legacy identity could not be prepared.")
            persona_display_name = legacy.display_name
            persona_relationship = legacy.relationship
            try:
                persona_profile = self._persona_profiles.build(db, legacy_id=legacy_id)
            except SQLAlchemyError:
                db.rollback()
                persona_profile = PersonaProfile()
        if (
            legacy_id is not None
            and isinstance(user_message, str)
            and user_message.strip()
            and personal_turn
        ):
            try:
                legacy = getattr(conversation, "legacy", None)
                if (
                    legacy is None
                    or legacy.owner_user_id != conversation.user_id
                ):
                    legacy = LegacyCRUD.get_user_legacy(
                        db,
                        legacy_id,
                        conversation.user_id,
                    )
            except SQLAlchemyError as exc:
                db.rollback()
                raise MemoryGroundingError(
                    "Legacy identity could not be prepared."
                ) from exc
            if legacy is None:
                raise MemoryGroundingError(
                    "Legacy identity could not be prepared."
                )
            persona_display_name = legacy.display_name
            persona_relationship = legacy.relationship
            try:
                persona_profile = self._persona_profiles.build(
                    db,
                    legacy_id=legacy_id,
                )
            except SQLAlchemyError:
                db.rollback()
                persona_profile = PersonaProfile()
            try:
                name_resolution = self._name_resolver.resolve(
                    db,
                    user_id=conversation.user_id,
                    legacy_id=legacy_id,
                    query=semantic_message,
                )
            except SQLAlchemyError:
                db.rollback()
                name_resolution = NameResolution()
            try:
                identity_arguments = {
                    "user_id": conversation.user_id,
                    "legacy_id": legacy_id,
                    "query": semantic_message,
                }
                if name_resolution.fact_type is not None:
                    identity_arguments["fact_type_override"] = (
                        name_resolution.fact_type
                    )
                    identity_arguments["canonical_value_override"] = (
                        name_resolution.canonical_value
                    )
                identity_result = self._identity_retrieval.retrieve(
                    db,
                    **identity_arguments,
                )
                if (
                    not identity_result.records
                    and (
                        response_query_classification.intent == "family"
                        or bool(re.search(r"\b(?:family|household)\b", semantic_message, re.I))
                    )
                ):
                    identity_result = self._identity_retrieval.retrieve_family_projection(
                        db, user_id=conversation.user_id, legacy_id=legacy_id,
                        query=semantic_message,
                    )
            except SQLAlchemyError:
                db.rollback()
                identity_result = IdentityGroundingResult(
                    detect_identity_intent(semantic_message),
                    None,
                )
            try:
                retrieval_query = self._conversation_continuity.build_retrieval_query(
                    history,
                    semantic_message,
                )
                retrieval_query = name_resolution.expand_query(retrieval_query)
                query_classification = MemoryRelevanceRanker.classify_query(
                    retrieval_query
                )
                query_intent = query_classification.intent
                query_language_mode = detect_query_language_mode(retrieval_query)
                ranked = self._memory_retrieval.search_approved(
                    db,
                    user_id=conversation.user_id,
                    legacy_id=legacy_id,
                    query=retrieval_query,
                )
                if not ranked.memories and not identity_result.records:
                    fallback_search_attempted = True
                    fallback_query = MemoryRelevanceRanker.build_fallback_query(
                        retrieval_query
                    )
                    ranked = self._memory_retrieval.search_approved(
                        db,
                        user_id=conversation.user_id,
                        legacy_id=legacy_id,
                        query=fallback_query,
                    )
            except SQLAlchemyError:
                db.rollback()
                retrieval_available = False
                _safe_log(
                    logging.WARNING,
                    "companion_memory_retrieval_failed",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    retrieval_failure_category="database_error",
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route="semantic_unavailable_lexical_fallback",
                    query_scope=("broad" if query_classification.broad else "specific"),
                    approved_memory_count=None,
                    matched_memory_count=0,
                    selected_memory_ids=[],
                    top_relevance_scores=[],
                )
            except (
                MemoryRetrievalNotFoundError,
                MemoryRetrievalArchivedError,
            ) as exc:
                db.rollback()
                _safe_log(
                    logging.WARNING,
                    "companion_memory_retrieval_failed",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    retrieval_failure_category=type(exc).__name__,
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route="semantic_unavailable_lexical_fallback",
                    query_scope=("broad" if query_classification.broad else "specific"),
                    approved_memory_count=None,
                    matched_memory_count=0,
                    selected_memory_ids=[],
                    top_relevance_scores=[],
                )
                raise MemoryGroundingError(
                    "Approved Legacy memories could not be prepared."
                ) from exc
            else:
                approved_candidate_count = ranked.approved_memory_count
                matched_candidate_count = ranked.matched_memory_count
                # Evidence selection is modality-neutral. Rendering may differ,
                # but Chat and Live Call receive the same canonical fact set.
                selection = self._memory_grounding.select(
                    [] if identity_result.fact_type is not None and identity_result.records
                    else ranked.memories,
                    compact=True,
                )
                memory_grounding_context = selection.context
                grounding_context = (
                    selection.context if live_call
                    else self._memory_grounding.build_context(list(selection.memories))
                )
                identity_context = (
                    identity_result.compact_context
                    if live_call and identity_result.compact_context is not None
                    else identity_result.context
                )
                if identity_context is not None:
                    grounding_context = (
                        identity_context
                        if grounding_context is None
                        else f"{identity_context}\n\n{grounding_context}"
                    )
                identity_evidence = identity_result.records
                if selection.memories:
                    memory_ids = tuple(
                        memory.memory_id for memory in selection.memories
                    )
                    retrieved_at = datetime.now(timezone.utc)
                    memory_evidence = tuple({
                        "memory_id": memory.memory_id,
                        "title": memory.title,
                        "summary": memory.summary,
                        "entities": self._memory_entities(
                            memory, conversation.legacy.display_name,
                        ),
                        "uncertainty": memory.uncertainty_note,
                        "conflict": memory.contradiction_group_id is not None,
                        "epistemic_status": (
                            "conflicted" if memory.contradiction_group_id is not None
                            else "uncertain" if memory.uncertainty_note
                            else "supported"
                        ),
                        **self._memory_perspective(
                            memory, conversation.legacy.display_name,
                        ),
                    } for memory in selection.memories)
                _safe_log(
                    logging.INFO,
                    "companion_memory_retrieval",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    approved_candidate_count=ranked.approved_memory_count,
                    approved_memory_count=ranked.approved_memory_count,
                    retrieved_memory_count=ranked.matched_memory_count,
                    matched_memory_count=ranked.matched_memory_count,
                    selected_memory_ids=list(memory_ids),
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route=(
                        "multilingual_semantic_lexical_hybrid"
                        if ranked.semantic_route_used
                        else "semantic_unavailable_lexical_fallback"
                    ),
                    semantic_candidate_count=ranked.semantic_candidate_count,
                    semantic_top_score=max(
                        (
                            memory.semantic_score
                            for memory in selection.memories
                            if memory.semantic_score is not None
                        ),
                        default=None,
                    ),
                    embedding_versions=list(dict.fromkeys(
                        memory.embedding_version
                        for memory in selection.memories
                        if memory.embedding_version
                    )),
                    query_scope=("broad" if query_classification.broad else "specific"),
                    positive_scoring_memory_count=ranked.matched_memory_count,
                    selected_topic_buckets=list(dict.fromkeys(
                        bucket for memory in selection.memories
                        for bucket in memory.topic_buckets
                    )),
                    selected_relevance_scores=[
                        memory.relevance_score for memory in selection.memories
                    ],
                    top_relevance_scores=[
                        memory.relevance_score
                        for memory in ranked.memories[:8]
                    ],
                    grounding_context_created=grounding_context is not None,
                    provider_call_attempted=False,
                )
                _safe_log(
                    logging.INFO,
                    "companion_identity_retrieval",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    identity_intent_detected=(
                        identity_result.fact_type is not None
                    ),
                    identity_fact_type=(
                        identity_result.fact_type.value
                        if identity_result.fact_type is not None
                        else None
                    ),
                    identity_candidate_count=identity_result.candidate_count,
                    identity_conflict_present=identity_result.conflict_present,
                    identity_fallback_to_memory=(
                        identity_result.fact_type is not None
                        and identity_result.context is None
                    ),
                )
                _safe_log(
                    logging.INFO,
                    "companion_name_resolution",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    name_resolution_attempted=True,
                    candidate_count=name_resolution.candidate_count,
                    resolution_method=(
                        "deterministic_transliteration_context"
                        if name_resolution.canonical_value is not None
                        else None
                    ),
                    resolution_confidence_bucket=(
                        "high" if name_resolution.confidence >= 0.90 else "none"
                    ),
                    relationship_context_used=(
                        name_resolution.relationship_context_used
                    ),
                    ambiguous_resolution=name_resolution.ambiguous,
                    fallback_used=(name_resolution.canonical_value is None),
                )
                try:
                    fidelity_plan = self._memory_fidelity.analyze_selected(
                        db,
                        legacy_id=legacy_id,
                        memories=list(selection.memories),
                        retrieval_available=True,
                    )
                except SQLAlchemyError:
                    db.rollback()
                    fidelity_plan = MemoryFidelityAnalyzer().analyze(
                        list(selection.memories),
                        has_uncertainty=True,
                    )
            if not retrieval_available:
                fidelity_plan = MemoryFidelityAnalyzer().analyze(
                    [],
                    retrieval_available=False,
                )
        else:
            _safe_log(
                logging.INFO,
                "companion_memory_retrieval",
                request_id=request_id,
                user_id=conversation.user_id,
                conversation_id=conversation.conversation_id,
                legacy_id=legacy_id,
                approved_candidate_count=0,
                approved_memory_count=0,
                retrieved_memory_count=0,
                matched_memory_count=0,
                selected_memory_ids=[],
                query_intent=query_intent,
                query_language_mode=query_language_mode,
                retrieval_route="not_applicable",
                query_scope=("broad" if query_classification.broad else "specific"),
                top_relevance_scores=[],
                grounding_context_created=False,
                provider_call_attempted=False,
            )
        # The shared engine is the final factual selector for every personal
        # modality.  The established retrieval above remains temporarily as
        # diagnostic compatibility while consumers migrate, but cannot alter
        # the evidence supplied to Chat or Live Call.
        shared_context = None
        if (
            personal_turn and legacy_id is not None
            and hasattr(self._memory_retrieval, "retrieve_approved")
        ):
            shared_context = self._legacy_memory_engine.prepare_legacy_context(
                db,
                user_id=conversation.user_id,
                legacy_id=legacy_id,
                conversation_id=getattr(conversation, "conversation_id", None),
                user_message=semantic_message,
                recent_history=history,
            )
            grounding_context = shared_context.prompt_context()
            profile_memory_facts = tuple(
                fact for fact in shared_context.profile_facts
                if fact.source_kind == "memory"
            )
            memory_ids = tuple(dict.fromkeys((
                *(fact.source_id for fact in profile_memory_facts),
                *(item.memory_id for item in shared_context.detailed_memories),
            )))
            memory_evidence = tuple([
                *({
                    "memory_id": fact.source_id,
                    "title": fact.key,
                    "summary": fact.value,
                    "entities": list(fact.entities),
                    "uncertainty": fact.uncertainty,
                    "conflict": fact.conflicting,
                    "epistemic_status": (
                        "conflicted" if fact.conflicting
                        else "uncertain" if fact.uncertainty else "supported"
                    ),
                    "subject": "self",
                    "subjects": ["self"],
                    "relevance_level": level,
                } for fact, level in zip(
                    shared_context.profile_facts,
                    shared_context.relevance_levels,
                    strict=False,
                ) if fact.source_kind == "memory"),
                *({
                    "memory_id": item.memory_id,
                    "title": item.title,
                    "summary": item.summary,
                    "entities": self._memory_entities(
                        item, shared_context.profile.display_name,
                    ),
                    "uncertainty": item.uncertainty_note,
                    "conflict": item.contradiction_group_id is not None,
                    "epistemic_status": (
                        "conflicted" if item.contradiction_group_id is not None
                        else "uncertain" if item.uncertainty_note else "supported"
                    ),
                    "relevance_level": 2,
                    **self._memory_perspective(
                        item, shared_context.profile.display_name,
                    ),
                } for item in shared_context.detailed_memories),
            ])
            identity_evidence = shared_context.identity_facts
            retrieved_at = datetime.now(timezone.utc) if memory_ids else None
            identity_context = grounding_context
            memory_grounding_context = grounding_context
            fallback_search_attempted = fallback_search_attempted or (
                shared_context.retrieval_status == "true_unknown"
            )

        resolved_entities = self._unique_names([
            *(shared_context.resolved_entities if shared_context else ()),
            *([name_resolution.canonical_value]
              if name_resolution.canonical_value else []),
            *(
                entity
                for memory in memory_evidence
                for entity in memory.get("entities", ())
            ),
        ])
        conflict_count = (int(identity_result.conflict_present)
                          + int(fidelity_plan.has_conflict))
        fact_confidence = (
            "conflicted" if conflict_count
            else "uncertain" if fidelity_plan.has_uncertainty
            else "supported" if (memory_ids or identity_evidence)
            else "unsupported"
        )
        is_broad = (
            response_query_classification.broad
            or ConversationContinuity.has_explicit_subject(semantic_message)
        )
        coverage = (
            "none" if not (memory_ids or identity_evidence)
            else "partial" if is_broad else "focused"
        )
        evidence_groups = tuple([
            *(EvidenceGroup(
                "identity", record.get("identity_fact_id"),
                "conflicted" if record.get("conflicting")
                else "uncertain" if record.get("uncertainty_note")
                else "supported", dict(record),
            ) for record in identity_evidence),
            *(EvidenceGroup(
                "memory", record.get("memory_id"),
                record.get("epistemic_status", "supported"), dict(record),
            ) for record in memory_evidence),
        ])
        grounded_turn = GroundedTurnContext(
            selected_identity_fact_ids=tuple(
                group.evidence_id for group in evidence_groups
                if group.kind == "identity" and group.evidence_id is not None
            ),
            selected_memory_ids=memory_ids,
            evidence_groups=evidence_groups,
            resolved_entities=resolved_entities,
            topic_anchor=semantic_message.strip(),
            fact_confidence=fact_confidence,
            coverage=coverage,
            conflict_count=conflict_count,
            uncertain=fidelity_plan.has_uncertainty,
            supported_relevant_evidence_count=sum(
                group.epistemic_status == "supported" for group in evidence_groups
            ),
            fallback_search_attempted=fallback_search_attempted,
            retrieval_status=(
                "error" if not retrieval_available
                else "true_unknown" if not evidence_groups
                else "ok"
            ),
        )
        return PreparedCompanionInput(
            messages=self._apply_presentation_preferences(self._context_builder.build_chat_messages(
                history,
                user_message,
                grounding_context=grounding_context,
                persona_display_name=persona_display_name,
                persona_relationship=persona_relationship,
                retrieval_available=retrieval_available,
                persona_style_profile=persona_profile.prompt_data(),
                persona_fidelity_guidance=fidelity_plan.prompt_guidance(),
                external_knowledge_enabled=(
                    knowledge_plan.query_mode != "autobiographical_memory"
                ),
                live_call=live_call,
            ), conversation_style, response_length, live_call),
            memory_ids=memory_ids,
            retrieved_at=retrieved_at,
            request_id=request_id,
            query_mode=knowledge_plan.query_mode,
            external_knowledge_mode=knowledge_plan.external_knowledge_mode,
            external_lookup_messages=(
                ExternalKnowledgeClassifier.build_public_lookup_messages(
                    user_message
                )
                if knowledge_plan.web_search_requested
                else ()
            ),
            grounding_chars=len(memory_grounding_context or ""),
            identity_context_chars=len(identity_context or "") if legacy_id is not None else 0,
            identity_direct=(
                legacy_id is not None
                and identity_result.compact_context is not None
            ),
            identity_count=identity_result.candidate_count,
            conflict_count=conflict_count,
            has_uncertainty=fidelity_plan.has_uncertainty,
            resolved_entities=resolved_entities,
            memory_evidence=memory_evidence,
            identity_evidence=identity_evidence,
            approved_candidate_count=approved_candidate_count,
            matched_candidate_count=matched_candidate_count,
            query_intent=getattr(query_intent, "value", str(query_intent)),
            query_language_mode=getattr(
                query_language_mode, "value", str(query_language_mode)
            ),
            query_broad=(
                response_query_classification.broad
                or ConversationContinuity.has_explicit_subject(semantic_message)
            ),
            fact_confidence=fact_confidence,
            coverage=coverage,
            grounded_turn=grounded_turn,
            fallback_search_attempted=fallback_search_attempted,
            retrieval_status=grounded_turn.retrieval_status,
            profile_engine_invoked=shared_context is not None,
            profile_fact_count=(
                shared_context.profile_relevant_fact_count if shared_context else 0
            ),
            detailed_memory_count=(
                shared_context.detailed_relevant_memory_count if shared_context else 0
            ),
        )

    async def generate_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> str:
        """Generate assistant text without changing persistence state."""
        result = await self.generate_response_with_provenance(
            db,
            conversation,
            user_message,
        )
        return result.content

    async def generate_response_with_provenance(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
    ) -> CompanionGeneration:
        """Generate text and return internal supplied-memory provenance."""
        normalized_turn = await LanguageNormalizationService(
            self._ai_service
        ).normalize_semantically(user_message)
        prepared = self.prepare_grounded_personal_turn(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
            semantic_message_override=normalized_turn.normalized_english_text,
        )
        db.rollback()
        self._log_provider_attempt(prepared, conversation)
        try:
            synthesis_messages = prepared.messages
            if prepared.external_knowledge_mode == "web_search":
                external_facts = await self._ai_service.generate_response(
                    prepared.external_lookup_messages,
                    external_knowledge_mode="web_search",
                )
                synthesis_messages = attach_external_context(
                    prepared.messages,
                    external_facts,
                )
                _safe_log(
                    logging.INFO,
                    "companion_external_knowledge_completed",
                    request_id=prepared.request_id,
                    query_mode=prepared.query_mode,
                    web_search_requested=True,
                    web_search_completed=True,
                    memory_count_supplied=len(prepared.memory_ids),
                    external_source_count=self._source_link_count(
                        external_facts
                    ),
                    fallback_reason=None,
                    provider_tool_exception_type=None,
                )
            content = await self._ai_service.generate_response(
                synthesis_messages
            )
        except AIProviderError as exc:
            if prepared.external_knowledge_mode != "web_search":
                raise
            _safe_log(
                logging.WARNING,
                "companion_external_knowledge_fallback",
                request_id=prepared.request_id,
                query_mode=prepared.query_mode,
                web_search_requested=True,
                web_search_completed=False,
                memory_count_supplied=len(prepared.memory_ids),
                external_source_count=0,
                fallback_reason="provider_tool_failure",
                provider_tool_exception_type=type(exc).__name__,
            )
            content = await self._ai_service.generate_response(
                attach_web_failure_context(prepared.messages)
            )
        return CompanionGeneration(
            content=content,
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
            request_id=prepared.request_id,
        )

    def stream_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> AsyncIterator[str]:
        """Prepare ordered context and return a provider-neutral text stream."""
        return self.stream_response_with_provenance(
            db,
            conversation,
            user_message,
        ).stream

    def stream_response_with_provenance(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
        semantic_message_override: str | None = None,
    ) -> CompanionStreamPlan:
        """Prepare stream and provenance before provider iteration begins."""
        prepared = self.prepare_grounded_personal_turn(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
            semantic_message_override=semantic_message_override,
        )
        # Log while the request-scoped ORM object is still attached.  FastAPI
        # may close yield dependencies before a StreamingResponse body starts.
        self._log_provider_attempt(prepared, conversation)
        db.rollback()
        return CompanionStreamPlan(
            stream=self._stream_prepared_response(prepared),
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
            request_id=prepared.request_id,
        )

    def _log_provider_attempt(
        self,
        prepared: PreparedCompanionInput,
        conversation: Conversation,
    ) -> None:
        _safe_log(
            logging.INFO,
            "companion_provider_call",
            request_id=prepared.request_id,
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            legacy_id=conversation.legacy_id,
            selected_memory_ids=list(prepared.memory_ids),
            grounding_context_created=bool(prepared.memory_ids),
            provider_call_attempted=True,
            query_mode=prepared.query_mode,
            web_search_requested=(
                prepared.external_knowledge_mode == "web_search"
            ),
            memory_count_supplied=len(prepared.memory_ids),
            web_search_completed=False,
            external_source_count=0,
            fallback_reason=None,
            provider_tool_exception_type=None,
        )

    @staticmethod
    def _apply_presentation_preferences(
        messages: list[AIMessage], conversation_style: str,
        response_length: str, live_call: bool,
    ) -> list[AIMessage]:
        if live_call:
            return messages
        style = {
            "natural": "Use the established natural conversational tone.",
            "gentle": "Use calm, soft wording.",
            "expressive": "Use slightly more energetic wording.",
        }[conversation_style]
        length = {
            "short": "Keep the answer concise when supported facts permit it.",
            "balanced": "Use the normal conversational level of detail.",
            "detailed": "Give a more complete answer using only supported information.",
        }[response_length]
        messages.insert(1, AIMessage(
            role="system",
            content=("Presentation preferences only; never alter retrieval, identity, grounding, "
                     f"contradiction handling, uncertainty, or factual content. {style} {length}"),
        ))
        return messages

    def prepare_live_call_input(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        legacy_name: str,
        relationship: str,
        user_message: str,
        history: Iterable[ConversationMessage],
    ) -> PreparedCompanionInput:
        """Build read-only grounded context for an authorized ephemeral call."""
        transient_conversation = SimpleNamespace(
            conversation_id=None,
            user_id=user_id,
            legacy_id=legacy_id,
            legacy=SimpleNamespace(
                owner_user_id=user_id,
                display_name=legacy_name,
                relationship=relationship,
            ),
        )
        return self.prepare_grounded_personal_turn(
            db,
            transient_conversation,
            user_message,
            history_override=history,
            live_call=True,
        )

    def prepare_conversation_live_call_input(
        self, db: Session, *, conversation: Conversation, user_message: str,
    ) -> PreparedCompanionInput:
        """Prepare a voice turn from the same durable bounded history as Chat."""
        history = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.conversation_id)
            .order_by(Message.created_at.desc(), Message.message_id.desc())
            .limit(self._context_builder.history_query_limit + 1)
            .all()
        )
        history.reverse()
        if (history and history[-1].role == "user"
                and history[-1].content.strip() == user_message.strip()):
            history.pop()
        return self.prepare_grounded_personal_turn(
            db, conversation, user_message, history_override=history, live_call=True,
        )

    def retrieve_live_call_identity(
        self, db: Session, *, user_id: int, legacy_id: int, query: str,
    ):
        """Reuse authoritative F2/F3 services for a compact direct identity lookup."""
        resolution = self._name_resolver.resolve(
            db, user_id=user_id, legacy_id=legacy_id, query=query,
        )
        arguments = {"user_id": user_id, "legacy_id": legacy_id, "query": query}
        if resolution.fact_type is not None:
            arguments["fact_type_override"] = resolution.fact_type
            arguments["canonical_value_override"] = resolution.canonical_value
        return self._identity_retrieval.retrieve(db, **arguments), resolution

    @staticmethod
    def _source_link_count(content: str) -> int:
        return len(set(re.findall(r"https?://[^\s)\]]+", content)))

    async def _stream_prepared_response(
        self,
        prepared: PreparedCompanionInput,
    ) -> AsyncIterator[str]:
        received = False
        try:
            synthesis_messages = prepared.messages
            external_facts = None
            if prepared.external_knowledge_mode == "web_search":
                external_facts = await self._ai_service.generate_response(
                    prepared.external_lookup_messages,
                    external_knowledge_mode="web_search",
                )
                synthesis_messages = attach_external_context(
                    prepared.messages,
                    external_facts,
                )
            async for chunk in self._ai_service.stream_response(
                synthesis_messages
            ):
                received = True
                yield chunk
            if prepared.external_knowledge_mode == "web_search":
                _safe_log(
                    logging.INFO,
                    "companion_external_knowledge_completed",
                    request_id=prepared.request_id,
                    query_mode=prepared.query_mode,
                    web_search_requested=True,
                    web_search_completed=True,
                    memory_count_supplied=len(prepared.memory_ids),
                    external_source_count=self._source_link_count(
                        external_facts or ""
                    ),
                    fallback_reason=None,
                    provider_tool_exception_type=None,
                )
        except AIProviderError as exc:
            if received or prepared.external_knowledge_mode != "web_search":
                raise
            _safe_log(
                logging.WARNING,
                "companion_external_knowledge_fallback",
                request_id=prepared.request_id,
                query_mode=prepared.query_mode,
                web_search_requested=True,
                web_search_completed=False,
                memory_count_supplied=len(prepared.memory_ids),
                external_source_count=0,
                fallback_reason="provider_tool_failure",
                provider_tool_exception_type=type(exc).__name__,
            )
            async for chunk in self._ai_service.stream_response(
                attach_web_failure_context(prepared.messages)
            ):
                yield chunk

    def stream_story_response(
        self,
        history: Iterable[ConversationMessage],
        *,
        chapter: str,
        relationship: str,
        display_name: str,
    ) -> AsyncIterator[str]:
        """Stream Story Guide text through the shared AI service."""
        messages = self._context_builder.build_story_messages(
            history,
            chapter=chapter,
            relationship=relationship,
            display_name=display_name,
        )
        return self._ai_service.stream_response(messages)

"""One factual answer boundary shared by Companion Chat and Live Call."""

from dataclasses import dataclass

from app.services.grounded_answer import GroundedAnswerPlan, GroundedAnswerService


@dataclass(frozen=True)
class EvidenceCoverage:
    requested_propositions: tuple[str, ...]
    satisfied_propositions: tuple[str, ...]
    partially_satisfied_propositions: tuple[str, ...]
    unsatisfied_propositions: tuple[str, ...]
    direct_evidence_count: int
    related_evidence_count: int
    background_evidence_count: int
    useful_supported_evidence_count: int
    collection_member_count: int
    status: str

    @property
    def no_memory_allowed(self) -> bool:
        return self.status == "UNKNOWN" and self.useful_supported_evidence_count == 0

    def public_dict(self) -> dict:
        return {
            "requested_propositions": list(self.requested_propositions),
            "satisfied_propositions": list(self.satisfied_propositions),
            "partially_satisfied_propositions": list(self.partially_satisfied_propositions),
            "unsatisfied_propositions": list(self.unsatisfied_propositions),
            "direct_evidence_count": self.direct_evidence_count,
            "related_evidence_count": self.related_evidence_count,
            "background_evidence_count": self.background_evidence_count,
            "useful_supported_evidence_count": self.useful_supported_evidence_count,
            "collection_member_count": self.collection_member_count,
            "status": self.status,
            "no_memory_allowed": self.no_memory_allowed,
        }


@dataclass(frozen=True)
class PersonalAnswerResult:
    subject: str
    intent: str
    requested_attributes: tuple[str, ...]
    selected_evidence_ids: tuple[int, ...]
    answer_facts: tuple[str, ...]
    must_include: tuple[str, ...]
    response_language: str
    validated_text: str
    status: str
    source: str
    plan: GroundedAnswerPlan
    coverage: EvidenceCoverage

    def public_dict(self) -> dict:
        return {
            "subject": self.subject,
            "intent": self.intent,
            "requested_attributes": list(self.requested_attributes),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "answer_facts": list(self.answer_facts),
            "must_include": list(self.must_include),
            "response_language": self.response_language,
            "validated_text": self.validated_text,
            "status": self.status,
            "source": self.source,
            "coverage": self.coverage.public_dict(),
        }


class PersonalAnswerService:
    """Own retrieval-result planning, rendering, validation, and fallback."""

    def __init__(self, grounded_answers: GroundedAnswerService, ai_service=None):
        self._answers = grounded_answers
        self._ai = ai_service

    @staticmethod
    def evidence_coverage(plan: GroundedAnswerPlan) -> EvidenceCoverage:
        requested = plan.requested_attributes or (plan.intent,)
        supported = [fact for fact in plan.facts if fact.get("status") == "supported"]
        levels = [GroundedAnswerService._target_relevance(
            plan.subject, plan.subject_type, plan.requested_attributes, fact,
        ) for fact in supported]
        direct = sum(level == 3 for level in levels)
        related = sum(level == 2 for level in levels)
        background = sum(level == 1 for level in levels)
        useful = direct + related + background
        if (
            not direct and plan.answer_facts
            and GroundedAnswerService.requests_collection("", plan.requested_attributes)
        ):
            direct = len(plan.answer_facts)
            useful = direct + related + background
        partial_markers = tuple(dict.fromkeys(plan.may_hedge))
        if plan.conflicts:
            status = "CONFLICTED"
        elif direct or (plan.answer_facts and not plan.requested_attributes):
            status = "PARTIAL" if partial_markers else "SUPPORTED"
        elif useful:
            status = "PARTIAL"
        else:
            status = "UNKNOWN"
        satisfied = requested if status == "SUPPORTED" else ()
        partially = requested if status in {"PARTIAL", "CONFLICTED"} else ()
        unsatisfied = requested if status == "UNKNOWN" else ()
        members = {
            str(fact.get("entity")).strip().casefold()
            for fact in plan.answer_facts if str(fact.get("entity") or "").strip()
        }
        return EvidenceCoverage(
            tuple(requested), tuple(satisfied), tuple(partially), tuple(unsatisfied),
            direct, related, background, useful, len(members), status,
        )

    @staticmethod
    def factual_result_from_prepared(
        prepared, *, legacy_name: str, original_query: str,
        response_language: str = "",
    ) -> dict:
        return {
            "status": "conflicted" if prepared.conflict_count else (
                "supported" if prepared.memory_ids or prepared.identity_evidence
                or prepared.identity_direct else "unsupported"
            ),
            "original_query": original_query,
            "response_language": response_language,
            "selected_legacy": {"name": legacy_name, "role": "self"},
            "identity": list(prepared.identity_evidence),
            "memories": list(prepared.memory_evidence),
            "selected_identity_fact_ids": list(
                prepared.grounded_turn.selected_identity_fact_ids
            ),
            "selected_memory_ids": list(prepared.grounded_turn.selected_memory_ids),
            "resolved_entities": list(prepared.grounded_turn.resolved_entities),
        }

    async def answer(self, query: str, factual_result: dict) -> PersonalAnswerResult:
        plan, text, source = await self._answers.produce(query, factual_result)
        coverage = self.evidence_coverage(plan)
        if not coverage.no_memory_allowed and "false_no_memory" in self._answers.validate(plan, text):
            text, source = self._answers.fallback(plan), "coverage_fallback"
        selected_ids = tuple(dict.fromkeys(
            fact["source_id"] for fact in plan.answer_facts
            if isinstance(fact.get("source_id"), int)
        ))
        return PersonalAnswerResult(
            subject=plan.subject,
            intent=plan.intent,
            requested_attributes=plan.requested_attributes,
            selected_evidence_ids=selected_ids,
            answer_facts=tuple(fact["statement"] for fact in plan.answer_facts),
            must_include=plan.must_include,
            response_language=str(factual_result.get("response_language") or ""),
            validated_text=text,
            status=plan.status,
            source=source,
            plan=plan,
            coverage=coverage,
        )

    async def answer_prepared(
        self, query: str, prepared, factual_result: dict,
    ) -> PersonalAnswerResult:
        """Run the frozen Chat synthesis first, then its shared validation boundary."""
        if self._ai is None:
            return await self.answer(query, factual_result)
        plan = self._answers.plan(query, factual_result)
        coverage = self.evidence_coverage(plan)
        text = await self._ai.generate_response(prepared.messages)
        source = "shared_chat_synthesis"
        # Preserve Chat's established concise/detail choices. Only structural
        # grounding failures trigger the existing validated repair path.
        issues = set(self._answers.validate(plan, text))
        structural = {
            "false_no_memory", "identity_denial", "wrong_perspective",
            "wrong_subject", "subject_irrelevance", "internal_language",
            "raw_storage_language", "authoritative_value_contradiction",
        }
        if issues & structural:
            plan, text, source = await self._answers.produce(query, factual_result)
            coverage = self.evidence_coverage(plan)
        selected_ids = tuple(dict.fromkeys(
            fact["source_id"] for fact in plan.answer_facts
            if isinstance(fact.get("source_id"), int)
        ))
        return PersonalAnswerResult(
            subject=plan.subject, intent=plan.intent,
            requested_attributes=plan.requested_attributes,
            selected_evidence_ids=selected_ids,
            answer_facts=tuple(fact["statement"] for fact in plan.answer_facts),
            must_include=plan.must_include,
            response_language=str(factual_result.get("response_language") or ""),
            validated_text=text, status=plan.status, source=source, plan=plan,
            coverage=coverage,
        )

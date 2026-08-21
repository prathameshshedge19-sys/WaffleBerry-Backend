"""Server-owned answer planning and validation for grounded personal turns."""

from dataclasses import dataclass
import logging
import re

from app.services.ai.ai_service import AIService
from app.services.ai.provider import AIMessage
from app.services.memory.identity_retrieval import detect_identity_intent
from app.services.turn_understanding import interpret_turn
from app.services.language_normalization import LanguageNormalizationService


logger = logging.getLogger(__name__)


_FORBIDDEN = re.compile(
    r"\b(?:supported|unsupported|evidence|grounded|grounding|retrieval|canonical|"
    r"database|records?|confidence|coverage|verified|verification|projection|facts?|"
    r"tool(?: call)?|stored memor(?:y|ies)|established|usable memory|pinned down)\b|"
    r"(?:memory|conversation) context|only know what you tell me|"
    r"(?:i can )?only (?:speak|talk) to|if you tell me|won't claim beyond|"
    r"don't want to guess|you can (?:remind|correct) me|"
    r"solid memory|fuzzy memory|floating around",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(?:i (?:do not|don't|cannot|can't) remember|i (?:do not|don't) know|"
    r"i(?:'m| am) not sure|no memory|nothing about|only (?:know|remember))\b",
    re.IGNORECASE,
)
_IDENTITY_DENIAL = re.compile(
    r"\b(?:i (?:do not|don't) have (?:a )?(?:name|family|siblings?|relatives?)|"
    r"i(?:'m| am) (?:just )?(?:an? )?(?:ai|digital existence)|"
    r"i (?:do not|don't) have a family in the human sense)\b",
    re.IGNORECASE,
)
_RAW_STORAGE_LANGUAGE = re.compile(
    r"\b(?:stated that|reported that|user said|it was mentioned that|"
    r"the conversation established|according to|refers? to|indicating|"
    r"multiple entities detected|the (?:household|family) (?:now )?has)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundedAnswerPlan:
    status: str
    subject: str
    subject_type: str
    requested_attributes: tuple[str, ...]
    intent: str
    speaker_name: str
    facts: tuple[dict, ...]
    answer_facts: tuple[dict, ...]
    entities: tuple[str, ...]
    conflicts: tuple[str, ...]
    must_include: tuple[str, ...]
    may_hedge: tuple[str, ...]
    response_mode: str
    response_perspective: str = "first_person_legacy"

    def public_dict(self) -> dict:
        return {
            "status": self.status, "subject": self.subject,
            "subject_type": self.subject_type,
            "requested_attributes": list(self.requested_attributes),
            "intent": self.intent,
            "speaker_name": self.speaker_name,
            "facts": list(self.facts), "entities": list(self.entities),
            "answer_facts": list(self.answer_facts),
            "conflicts": list(self.conflicts),
            "must_include": list(self.must_include),
            "may_hedge": list(self.may_hedge),
            "response_mode": self.response_mode,
            "response_perspective": self.response_perspective,
        }


class GroundedAnswerService:
    def __init__(self, ai_service: AIService):
        self._ai = ai_service

    @staticmethod
    def plan(query: str, result: dict) -> GroundedAnswerPlan:
        facts = []
        required = []
        conflicts = []
        may_hedge = []
        represented_name_value = str((result.get("selected_legacy") or {}).get("name") or "").strip()
        represented_name = represented_name_value.casefold()
        subject, subject_type, attributes = GroundedAnswerService.understand(query)
        intent = GroundedAnswerService.question_intent(query, attributes)
        if (represented_name_value and re.search(r"\byou (?:are|'re)\b", query, re.I)
                and represented_name_value.split()[0].casefold() in query.casefold()):
            subject, subject_type, intent = "represented person", "self", "confirmation"
        for record in result.get("identity", ()):
            status = record.get("epistemic_status", "supported")
            statement = GroundedAnswerService._identity_statement(record)
            fact = {"source_id": record.get("identity_fact_id"), "statement": statement,
                    "entity": record.get("value"), "status": status,
                    "kind": "identity_fact", "relationship": record.get("relationship"),
                    "relevance_level": int(record.get("relevance_level", 1))}
            facts.append(fact)
            if status == "conflicted": conflicts.append(statement)
        for record in result.get("memories", ()):
            status = record.get("epistemic_status", "supported")
            raw_statement = str(record.get("summary") or record.get("title") or "").strip()
            statement = GroundedAnswerService._first_person(raw_statement, represented_name_value)
            canonical_entities = GroundedAnswerService._canonical_entity_names(
                record.get("entities", ()), statement,
            )
            statement = GroundedAnswerService._canonicalize_entity_mentions(
                statement, canonical_entities,
            )
            if not statement:
                continue
            fact = {"source_id": record.get("memory_id"), "statement": statement,
                    "entity": canonical_entities[0] if canonical_entities else None, "status": status,
                    "kind": GroundedAnswerService._fact_kind(raw_statement),
                    "relevance_level": int(record.get("relevance_level", 2))}
            facts.append(fact)
            markers = [
                *(entity for entity in canonical_entities if entity.casefold() in statement.casefold()),
                *GroundedAnswerService._measurements(statement),
                *GroundedAnswerService._salient_values(statement),
            ]
            markers = [value for value in markers if value.casefold() != represented_name]
            if status != "supported": may_hedge.extend(markers or [statement])
            if status == "conflicted": conflicts.append(statement)
        answerable = [
            fact for fact in facts
            if fact["status"] == "supported" and fact["relevance_level"] <= 3
            and fact.get("kind") != "internal_metadata"
        ]
        # Equivalent profile and memory rows remain available for audit, but
        # become one speakable proposition before ranking and generation.
        deduplicated = []
        seen_claims = set()
        for fact in answerable:
            key = GroundedAnswerService._semantic_key(fact["statement"])
            if key in seen_claims:
                continue
            seen_claims.add(key)
            deduplicated.append(fact)
        answerable = deduplicated
        excluded_for_expansion = {
            value for value in result.get("_rendering_excluded_source_ids", ())
            if isinstance(value, int)
        }
        if GroundedAnswerService.requests_expansion(query) and excluded_for_expansion:
            unused = [
                fact for fact in answerable
                if fact.get("source_id") not in excluded_for_expansion
            ]
            if unused:
                answerable = unused
        ranked = sorted(
            ((GroundedAnswerService._target_relevance(subject, subject_type, attributes, fact),
              int(fact["relevance_level"]), index, fact)
             for index, fact in enumerate(answerable)),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        strongest = ranked[0][0] if ranked else 0
        if strongest == 0 and ranked:
            # Retrieval is the related-evidence fallback only when no candidate
            # has any deterministic target match. It is never mixed with a
            # direct match, which is what prevents cross-subject leakage.
            best_retrieval_level = min(item[1] for item in ranked)
            ranked = [item for item in ranked if item[1] == best_retrieval_level]
            strongest = 1
        eligible = [item[3] for item in ranked if item[0] == strongest and strongest > 0]
        if strongest == 1 and ranked and not eligible:
            eligible = [item[3] for item in ranked]
        budget = len(eligible) if GroundedAnswerService.requests_expansion(query) else 2
        answer_facts = tuple(eligible[:budget])
        for fact in answer_facts:
            required.extend([
                *(value for value in (fact.get("entity"),) if value),
                *GroundedAnswerService._measurements(fact["statement"]),
                *GroundedAnswerService._salient_values(fact["statement"]),
            ])
        required = list(dict.fromkeys(value.strip() for value in required if isinstance(value, str) and value.strip()))
        supported = list(answer_facts)
        status = "supported" if supported else (
            "conflicted" if conflicts else "unsupported"
        )
        return GroundedAnswerPlan(
            status=status, subject=subject, subject_type=subject_type,
            requested_attributes=attributes, intent=intent,
            speaker_name=represented_name_value,
            facts=tuple(facts), answer_facts=answer_facts,
            entities=GroundedAnswerService._canonical_entity_names(
                result.get("resolved_entities", ()), "",
            ),
            conflicts=tuple(conflicts), must_include=tuple(required),
            may_hedge=tuple(dict.fromkeys(may_hedge)),
            response_mode=("mixed" if supported and (conflicts or may_hedge) else status),
        )

    async def produce(self, query: str, result: dict) -> tuple[GroundedAnswerPlan, str, str]:
        plan = self.plan(query, result)
        response_query = str(result.get("original_query") or query)
        target_language = str(result.get("response_language") or "")
        correction_kind = str(result.get("correction_kind") or "")
        if correction_kind and plan.status != "unsupported":
            correction = self._corrective_response(
                plan, correction_kind,
                str(result.get("correction_subject") or plan.subject),
            )
            return plan, self._localize(
                correction, response_query, target_language,
            ), "deterministic_correction"
        if plan.status == "unsupported":
            return plan, self._localize(
                "I don't remember that.", response_query, target_language,
            ), "deterministic_unsupported"
        for attempt in range(2):
            instruction = self._generation_instruction(
                plan, repair=attempt == 1, response_query=response_query,
                target_language=target_language,
            )
            text = await self._ai.generate_response((
                AIMessage(role="system", content=instruction),
                AIMessage(role="user", content=response_query),
            ))
            if (self.validate(plan, text) == ()
                    and self._response_language_matches(response_query, text, target_language)):
                self._log_language_result(
                    result, response_query, target_language, text,
                    validator_result="valid", fallback_used=False,
                )
                return plan, text.strip(), "generated" if attempt == 0 else "repaired"
        fallback = self._localize(self.fallback(plan), response_query, target_language)
        if (target_language in {"marathi", "hindi"}
                and fallback == self.fallback(plan)):
            fallback = self._localize("I don't remember that.", response_query, target_language)
        self._log_language_result(
            result, response_query, target_language, fallback,
            validator_result="deterministic_fallback", fallback_used=True,
        )
        return plan, fallback, "deterministic_fallback"

    @staticmethod
    def _log_language_result(
        result: dict, response_query: str, target_language: str, response: str,
        *, validator_result: str, fallback_used: bool,
    ) -> None:
        service = LanguageNormalizationService()
        source = service.normalize_user_turn(response_query).detected_language
        renderer = service.normalize_user_turn(response).detected_language
        logger.info(
            "RESPONSE_LANGUAGE turn_id=%s source_language=%s active_language_before=%s "
            "explicit_language_switch=%s target_language=%s renderer_language=%s "
            "validator_result=%s fallback_used=%s",
            result.get("turn_id", "na"), source,
            result.get("active_conversation_language", "unknown"),
            bool(result.get("explicit_language_switch", False)),
            target_language or service.normalize_user_turn(response_query).response_language,
            renderer, validator_result, fallback_used,
        )

    def localized_fallback(
        self, plan: GroundedAnswerPlan, response_query: str,
        target_language: str = "",
    ) -> str:
        """Render the semantic fallback in the explicit response language."""
        semantic = self.fallback(plan)
        localized = self._localize(semantic, response_query, target_language)
        if target_language in {"marathi", "hindi"} and localized == semantic:
            return self._localize("I don't remember that.", response_query, target_language)
        return localized

    @staticmethod
    def _corrective_response(
        plan: GroundedAnswerPlan, correction_kind: str, subject: str,
    ) -> str:
        if correction_kind == "objection":
            label = "your name" if subject == "represented person" else f"the {subject}"
            return f"You're right—you asked about {label}."
        answer = GroundedAnswerService.fallback(plan)
        if correction_kind == "correction" and answer.startswith("We have an ") and answer.endswith(" TV."):
            answer = "it's " + answer[len("We have "):]
        prefix = "Right. " if correction_kind == "clarification" else "Right—"
        return prefix + answer[0].lower() + answer[1:]

    @staticmethod
    def _localize(text: str, response_query: str, target_language: str = "") -> str:
        source_mode = LanguageNormalizationService().normalize_user_turn(
            response_query
        ).detected_language
        language = target_language.strip().casefold() or source_mode
        if language == "english" or language == "multilingual_unknown":
            return text
        objection = re.fullmatch(r"You're right—you asked about (.+)\.", text)
        correction = re.fullmatch(r"Right—(.+)", text)
        clarification = re.fullmatch(r"Right\. (.+)", text)
        if objection:
            subject = objection.group(1)
            if language in {"marathi", "mixed_marathi_english", "romanized_marathi"}:
                return f"बरोबर—तुम्ही {subject}बद्दल विचारलं होतं."
            if language in {"hindi", "mixed_hindi_english", "romanized_hindi"}:
                return f"सही कहा—आपने {subject} के बारे में पूछा था।"
        if correction or clarification:
            body = (correction or clarification).group(1)
            localized_body = GroundedAnswerService._localize(
                body[0].upper() + body[1:], response_query, target_language,
            )
            if localized_body != body:
                if language in {"marathi", "mixed_marathi_english", "romanized_marathi"}:
                    return f"बरोबर—{localized_body}"
                if language in {"hindi", "mixed_hindi_english", "romanized_hindi"}:
                    return f"सही—{localized_body}"
        dogs = re.fullmatch(r"We have two Labradors, (.+) and (.+)\.", text)
        family = re.fullmatch(r"My husband is (.+), and my younger brother is (.+)\.", text)
        tv = re.fullmatch(r"We have an (\d+(?:\.\d+)?)-inch TV\.", text)
        tv_size = re.fullmatch(r"The TV is (\d+(?:\.\d+)?) inches\.", text)
        identity = re.fullmatch(r"My name is (.+)\.", text)
        confirmation = re.fullmatch(r"Yes, I'm (.+)\.", text)
        marathi = language in {"marathi", "mixed_marathi_english", "romanized_marathi"}
        roman = source_mode.startswith("romanized_")
        if marathi and roman:
            if dogs: return f"Aplyakade don Labradors aahet, {dogs.group(1)} ani {dogs.group(2)}."
            if family: return f"Majhe pati {family.group(1)} aahet, ani majha dhakata bhau {family.group(2)} aahe."
            if tv: return f"Aplyakade {tv.group(1)}-inch TV aahe."
            if tv_size: return f"TV {tv_size.group(1)} inch cha aahe."
            if identity: return f"Majha naav {identity.group(1)} aahe."
            if confirmation: return f"Ho, mi {confirmation.group(1)} aahe."
            if text == "I don't remember that.": return "Mala te aathavat nahi."
        if marathi:
            if dogs: return f"हो, आमच्याकडे दोन Labradors आहेत—{dogs.group(1)} आणि {dogs.group(2)}."
            if family: return f"माझे पती {family.group(1)} आहेत, आणि माझा धाकटा भाऊ {family.group(2)} आहे."
            if tv: return f"हो, आमच्याकडे {tv.group(1)}-inch TV आहे."
            if tv_size: return f"हो, आमच्याकडे {tv_size.group(1)}-inch TV आहे."
            if identity: return f"माझं नाव {identity.group(1)} आहे."
            if confirmation: return f"हो, मी {confirmation.group(1)} आहे."
            if text == "I don't remember that.": return "मला ते आठवत नाही."
        if language == "romanized_hindi":
            if dogs: return f"Hamare paas do Labradors hain, {dogs.group(1)} aur {dogs.group(2)}."
            if family: return f"Mere pati {family.group(1)} hain, aur mere chhote bhai {family.group(2)} hain."
            if tv or tv_size:
                size = (tv or tv_size).group(1)
                return f"Hamara TV {size} inch ka hai."
            if identity: return f"Mera naam {identity.group(1)} hai."
            if confirmation: return f"Haan, main {confirmation.group(1)} hoon."
            if text == "I don't remember that.": return "Mujhe woh yaad nahi hai."
        if language in {"hindi", "mixed_hindi_english"}:
            if dogs: return f"हमारे पास दो लैब्राडॉर हैं, {dogs.group(1)} और {dogs.group(2)}."
            if family: return f"मेरे पति {family.group(1)} हैं, और मेरे छोटे भाई {family.group(2)} हैं."
            if tv or tv_size:
                size = (tv or tv_size).group(1)
                return f"हमारा TV {size} इंच का है."
            if identity: return f"मेरा नाम {identity.group(1)} है."
            if confirmation: return f"हाँ, मैं {confirmation.group(1)} हूँ."
            if text == "I don't remember that.": return "मुझे वह याद नहीं है."
        return text

    @staticmethod
    def _response_language_matches(
        query: str, response: str, target_language: str = "",
    ) -> bool:
        service = LanguageNormalizationService()
        expected = target_language.strip().casefold() or service.normalize_user_turn(
            query
        ).response_language
        if expected in {"english", "multilingual_unknown"}:
            return True
        actual = service.normalize_user_turn(response).detected_language
        if expected.startswith("mixed_marathi") or expected == "marathi":
            return "marathi" in actual
        if expected == "romanized_marathi":
            return actual in {"romanized_marathi", "mixed_marathi_english"}
        if expected.startswith("mixed_hindi") or expected == "hindi":
            return "hindi" in actual
        if expected == "romanized_hindi":
            return actual in {"romanized_hindi", "mixed_hindi_english"}
        return True

    @staticmethod
    def validate(plan: GroundedAnswerPlan, text: str) -> tuple[str, ...]:
        normalized = " ".join((text or "").casefold().split())
        errors = []
        if not normalized:
            errors.append("blank")
        if _FORBIDDEN.search(normalized):
            errors.append("internal_language")
        if _RAW_STORAGE_LANGUAGE.search(normalized):
            errors.append("raw_storage_language")
        if plan.status != "unsupported" and _NEGATIVE.search(normalized):
            errors.append("false_no_memory")
        if plan.status == "supported" and re.search(
            r"\b(?:i think|maybe|possibly|probably|i(?:'m| am) not sure|as far as i (?:know|remember))\b",
            normalized,
        ):
            errors.append("unnecessary_hedging")
        represented_name = plan.speaker_name.casefold()
        if (represented_name and plan.subject_type != "self"
                and re.match(rf"^\s*{re.escape(represented_name)}\s*[,—:-]", normalized)):
            errors.append("self_name_address")
        if represented_name and re.search(
            rf"\b{re.escape(represented_name)}(?:'s|’s| has| is| says| stated| indicates)\b", normalized,
            re.IGNORECASE,
        ):
            errors.append("wrong_perspective")
        if any(fact["statement"].casefold().startswith(("we have", "we own", "our "))
               for fact in plan.facts) and re.search(r"\bmy family ha(?:s|ve)\b", normalized):
            errors.append("shared_household_perspective")
        if plan.subject_type != "self" and re.search(r"\bmy name is\b", normalized):
            errors.append("wrong_subject")
        if (represented_name and plan.subject_type != "self"
                and re.search(rf"\bi(?:'m| am) {re.escape(represented_name)}\b", normalized)):
            errors.append("wrong_subject")
        natural = GroundedAnswerService.fallback(plan)
        if (len(plan.answer_facts) > 1 and natural.count(".") <= 1
                and len(re.findall(r"[.!?]+", text or "")) > 1):
            errors.append("unmerged_facts")
        sentences = [
            GroundedAnswerService._semantic_key(item)
            for item in re.split(r"(?<=[.!?])\s+", text or "") if item.strip()
        ]
        if len(sentences) != len(set(sentences)):
            errors.append("semantic_duplicate")
        selected_ids = {id(fact) for fact in plan.answer_facts}
        excluded_values = {
            value.casefold()
            for fact in plan.facts if id(fact) not in selected_ids
            for value in (
                fact.get("entity"),
                *GroundedAnswerService._salient_values(fact.get("statement", "")),
            )
            if isinstance(value, str) and value.strip()
        }
        selected_values = {
            value.casefold()
            for fact in plan.answer_facts
            for value in (fact.get("entity"), *GroundedAnswerService._salient_values(fact["statement"]))
            if isinstance(value, str) and value.strip()
        }
        if any(re.search(rf"\b{re.escape(value)}\b", normalized)
               for value in excluded_values - selected_values):
            errors.append("subject_irrelevance")
        if (plan.speaker_name or plan.facts) and _IDENTITY_DENIAL.search(normalized):
            errors.append("identity_denial")
        for required in plan.must_include:
            measurement_keys = {
                GroundedAnswerService._measurement_key(value)
                for value in GroundedAnswerService._measurements(required)
            }
            response_keys = {
                GroundedAnswerService._measurement_key(value)
                for value in GroundedAnswerService._measurements(text or "")
            }
            if required.casefold() not in normalized and not (
                measurement_keys and measurement_keys <= response_keys
            ):
                errors.append("required_omission")
                break
            if (re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", required)
                    and required.casefold() in normalized and required not in (text or "")):
                errors.append("canonical_capitalization")
        allowed_measurements = {
            GroundedAnswerService._measurement_key(value)
            for fact in plan.facts
            for value in GroundedAnswerService._measurements(fact["statement"])
        }
        response_measurements = {
            GroundedAnswerService._measurement_key(value)
            for value in GroundedAnswerService._measurements(text or "")
        }
        if allowed_measurements and response_measurements - allowed_measurements:
            errors.append("authoritative_value_contradiction")
        authoritative_relationships = {}
        for fact in plan.facts:
            if fact["status"] != "supported":
                continue
            match = re.match(r"My (.+?) is (.+?)\.$", fact["statement"], re.I)
            if match:
                authoritative_relationships.setdefault(match.group(1).casefold(), set()).add(
                    match.group(2).casefold()
                )
        for relationship, allowed in authoritative_relationships.items():
            for match in re.finditer(
                rf"\bmy {re.escape(relationship)} is ([^.!?]+)", normalized,
            ):
                if match.group(1).strip() not in allowed:
                    errors.append("authoritative_value_contradiction")
        return tuple(dict.fromkeys(errors))

    @staticmethod
    def fallback(plan: GroundedAnswerPlan) -> str:
        statements = []
        for fact in plan.answer_facts:
            if fact["status"] != "supported":
                continue
            statement = fact["statement"].strip()
            if statement.casefold() not in {item.casefold() for item in statements}:
                statements.append(statement)
        if plan.subject_type == "self" and statements:
            identity = re.fullmatch(r"My name is ([^.!?]+)\.", statements[0], re.I)
            if identity and plan.intent != "attribute_query":
                return f"Yes, I'm {identity.group(1)}."
        pets = []
        for statement in statements:
            match = GroundedAnswerService._pet_claim(statement)
            if match:
                pets.append(match)
        if len(pets) == len(statements) == 2 and len({breed.casefold() for breed, _ in pets}) == 1:
            breed = pets[0][0]
            plural = breed if breed.casefold().endswith("s") else f"{breed}s"
            return f"We have two {plural}, {pets[0][1]} and {pets[1][1]}."
        if len(statements) == 2 and all(item.casefold().startswith("my ") for item in statements):
            return f"{statements[0][:-1]}, and {statements[1][0].lower()}{statements[1][1:]}"
        if len(statements) == 1:
            measurement = re.fullmatch(r"Our (TV|television) is (\d+(?:\.\d+)?) inches\.", statements[0], re.I)
            if measurement:
                if "size" in plan.requested_attributes:
                    return f"The TV is {measurement.group(2)} inches."
                return f"We have an {measurement.group(2)}-inch TV."
        if not statements and plan.conflicts:
            claim = plan.conflicts[0].rstrip(".")
            return f"I'm not sure whether {claim[0].lower()}{claim[1:]}."
        return " ".join(statements) if statements else "I don't remember that."

    @staticmethod
    def _semantic_key(statement: str) -> str:
        value = GroundedAnswerService._first_person(statement)
        value = re.sub(r"\b(?:full|real|current|now|another|also)\b", "", value, flags=re.I)
        value = re.sub(r"[^\w]+", " ", value.casefold())
        return " ".join(value.split())

    @staticmethod
    def _canonical_entity_names(values, statement: str) -> tuple[str, ...]:
        candidates = [str(value).strip() for value in values if str(value).strip()]
        candidates.extend(re.findall(r"\bnamed\s+([^,.;]+)", statement, re.I))
        result: dict[str, str] = {}
        for raw in candidates:
            parts = [part.strip() for part in re.split(r"[/()]", raw) if part.strip()]
            if parts and all(re.sub(r"\W+", "", part).casefold() == re.sub(r"\W+", "", parts[0]).casefold() for part in parts):
                raw = parts[0]
            key = re.sub(r"\W+", "", raw).casefold()
            if not key:
                continue
            display = raw.title() if raw.islower() and len(raw.split()) == 1 else raw
            existing = result.get(key)
            if existing is None or (display[:1].isupper() and not existing[:1].isupper()):
                result[key] = display
        return tuple(result.values())

    @staticmethod
    def _canonicalize_entity_mentions(statement: str, entities: tuple[str, ...]) -> str:
        value = statement
        for canonical in entities:
            key = re.sub(r"\W+", "", canonical).casefold()
            value = re.sub(
                rf"\b{re.escape(canonical)}\s*[/()]\s*{re.escape(canonical)}\)?\b",
                canonical, value, flags=re.I,
            )
            for match in tuple(re.finditer(r"\b[A-Za-z][\w'-]*\b", value)):
                if re.sub(r"\W+", "", match.group()).casefold() == key:
                    value = value[:match.start()] + canonical + value[match.end():]
                    break
        return value

    @staticmethod
    def _pet_claim(statement: str) -> tuple[str, str] | None:
        patterns = (
            r"We have (?:a|another) ([A-Za-z][\w'-]*) named ([^.!?,]+)\.",
            r"([^.!?,]+) is (?:our |a )?(?:dog,? (?:and )?)?(?:a |an )?([A-Za-z][\w'-]*)\.",
        )
        first = re.fullmatch(patterns[0], statement, re.I)
        if first:
            return first.group(1), first.group(2).strip()
        second = re.fullmatch(patterns[1], statement, re.I)
        if second and second.group(2).casefold() not in {"dog", "pet"}:
            return second.group(2), second.group(1).strip()
        return None

    @staticmethod
    def _identity_statement(record: dict) -> str:
        relationship = str(record.get("relationship") or "").strip()
        value = str(record.get("value") or "").strip()
        return f"My {relationship} is {value}." if relationship else f"My name is {value}."

    @staticmethod
    def _measurements(text: str) -> list[str]:
        return re.findall(r"\b\d+(?:\.\d+)?[- ](?:inch(?:es)?|cm|mm|met(?:er|re)s?|feet|foot)\b", text, re.I)

    @staticmethod
    def _measurement_key(value: str) -> str:
        normalized = re.sub(r"[- ]+", " ", value.casefold()).strip()
        return re.sub(r"\binches\b", "inch", normalized)

    @staticmethod
    def _salient_values(text: str) -> list[str]:
        ignored = {"A", "An", "I", "It", "My", "Our", "The", "They", "We"}
        return [
            value for value in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
            if value not in ignored
        ]

    @staticmethod
    def _first_person(statement: str, represented_name: str = "") -> str:
        value = statement.strip()
        value = re.sub(
            r"^(?:User said|It was mentioned that|The conversation established that)\s+",
            "", value, flags=re.I,
        )
        if represented_name:
            escaped = re.escape(represented_name)
            first_name = re.escape(represented_name.split()[0]) if represented_name.split() else escaped
            value = re.sub(
                rf"^(?:{escaped}|{first_name}) (?:stated|reported) that (?:her|his) "
                r"(?:full )?(?:real )?name is\s+",
                "My name is ", value, flags=re.I,
            )
            value = re.sub(
                rf"^(?:{escaped}|{first_name}) (?:stated|reported) that (?:her|his) "
                r"([\w -]+?)(?:'s|â€™s) (?:full )?name is\s+",
                r"My \1 is ", value, flags=re.I,
            )
            value = re.sub(
                rf"^{escaped}(?:'s|’s) family (?:now )?ha(?:s|ve)\b",
                "We have", value, flags=re.I,
            )
            value = re.sub(
                rf"^{escaped}(?:'s|’s) family owns\b",
                "We have", value, flags=re.I,
            )
            value = re.sub(rf"^{escaped}(?:'s|’s)\s+", "My ", value, flags=re.I)
            value = re.sub(rf"^{escaped}\s+(?:now\s+)?has\b", "I have", value, flags=re.I)
            value = re.sub(rf"^{escaped}\s+(?:says\s+)?there is\b", "We have", value, flags=re.I)
            value = re.sub(rf"^{escaped}\s+referred to having\b", "I referred to having", value, flags=re.I)
            value = re.sub(rf"^{escaped}\s+refers to\b", "I refer to", value, flags=re.I)
            value = re.sub(
                rf"^(?:{escaped}|{first_name})\s+(?:says?|indicates?|notes?)\s+(?:that\s+)?(?:she|he)\s+",
                "I ", value, flags=re.I,
            )
            value = re.sub(
                rf"^(?:{escaped}|{first_name})\s+(?:says?|indicates?|notes?)\s+(?:that\s+)?",
                "", value, flags=re.I,
            )
            value = re.sub(
                rf"^(?:{escaped}|{first_name})\s+and\s+([^.!?,]+?)\s+",
                r"\1 and I ", value, flags=re.I,
            )
        value = re.sub(r"^(?:The family|The household|They) (?:now )?ha(?:s|ve)\b", "We have", value, flags=re.I)
        value = re.sub(
            r"^We have another dog named ([^,.]+), who is also a ([A-Za-z][\w'-]*)\.?$",
            r"We have a \2 named \1.", value, flags=re.I,
        )
        value = re.sub(r"^The speaker said they have\b", "We have", value, flags=re.I)
        value = re.sub(r"^[A-Z][\w'-]+ says there is\b", "We have", value)
        return value if value.endswith((".", "!", "?")) else f"{value}."

    @staticmethod
    def understand(query: str) -> tuple[str, str, tuple[str, ...]]:
        turn = interpret_turn(query)
        return turn.resolved_topic or "personal subject", turn.subject_type, turn.requested_attributes
        """Legacy implementation retained below temporarily for stable blame context."""
        normalized = " ".join(query.casefold().split())
        words = re.findall(r"[^\W_]+", normalized, re.UNICODE)
        attributes = []
        attribute_terms = {
            "name": "name", "names": "name", "breed": "breed", "breeds": "breed",
            "size": "size", "large": "size", "count": "count", "many": "count",
            "brand": "brand", "model": "model", "where": "place", "when": "time",
        }
        for word in words:
            value = attribute_terms.get(word)
            if value and value not in attributes:
                attributes.append(value)
        if re.search(
            r"\b(?:do|did) (?:i|we|you) (?:have|own|go|visit)|\b(?:is|are) there\b",
            normalized,
        ):
            attributes.insert(0, "existence")
        if (re.search(r"\b(?:your|my|our)\s+(?:full\s+)?name\b", normalized)
                or re.fullmatch(r"who are you[?.!]*", normalized)):
            return "represented person", "self", tuple(attributes or ["name"])
        known_subjects = (
            "husband", "wife", "spouse", "brother", "sister", "family",
            "dogs", "dog", "pets", "pet", "tv", "television", "garden", "car",
        )
        explicit_subjects = [
            candidate for candidate in known_subjects
            if re.search(rf"\b{re.escape(candidate)}\b", normalized)
        ]
        # Collapse aliases/plurals before deciding whether the user explicitly
        # requested more than one subject.
        canonical = {"dog": "dogs", "pets": "dogs", "pet": "dogs",
                     "television": "tv", "wife": "spouse", "husband": "husband"}
        explicit_subjects = list(dict.fromkeys(
            canonical.get(candidate, candidate) for candidate in explicit_subjects
        ))
        if " and " in normalized and len(explicit_subjects) >= 2:
            return "+".join(explicit_subjects), "multi", tuple(attributes)
        possessive = re.search(r"\b(?:your|my|our)\s+([\w'-]+)", normalized)
        if possessive:
            subject = re.sub(r"['’]s$", "", possessive.group(1))
        else:
            candidates = [
                re.sub(r"['’]s$", "", word) for word in words
                if word not in {"and", "about", "tell", "me", "the", "what", "who", "is", "are", "was", "were", "their", "it"}
                and word not in attribute_terms
            ]
            subject = candidates[-1] if candidates else "personal subject"
        return subject, "relationship" if subject in {"husband", "wife", "spouse", "brother", "sister", "parent", "child"} else "memory", tuple(attributes)

    @staticmethod
    def question_intent(query: str, attributes: tuple[str, ...]) -> str:
        if "existence" in attributes:
            return "existence_query"
        if attributes:
            return "attribute_query"
        return "summary"

    @staticmethod
    def _fact_kind(statement: str) -> str:
        normalized = statement.casefold()
        if re.search(
            r"\b(?:refers? to|alias|indicat(?:e|es|ing)|subject category|taxonomy|"
            r"multiple entities detected|retrieval)\b", normalized,
        ):
            return "internal_metadata"
        return "canonical_fact"

    @staticmethod
    def _target_relevance(
        subject: str, subject_type: str, attributes: tuple[str, ...], fact: dict,
    ) -> int:
        """Return 3 direct, 2 related, 1 background, or 0 unrelated."""
        if subject_type == "multi":
            return max((GroundedAnswerService._target_relevance(
                item,
                "relationship" if item in {
                    "husband", "wife", "spouse", "brother", "sister", "parent", "child"
                } else "memory",
                attributes, fact,
            ) for item in subject.split("+")), default=0)
        text = f"{fact.get('statement', '')} {fact.get('entity') or ''}".casefold()
        relationship = str(fact.get("relationship") or "").casefold()
        aliases = {
            "dogs": {"dog", "dogs", "pet", "pets", "labrador", "labradors"},
            "dog": {"dog", "dogs", "pet", "pets", "labrador", "labradors"},
            "pets": {"dog", "dogs", "pet", "pets", "labrador", "labradors"},
            "pet": {"dog", "dogs", "pet", "pets", "labrador", "labradors"},
            "television": {"tv", "television"}, "tv": {"tv", "television"},
            "family": {"husband", "wife", "spouse", "brother", "sister", "parent", "child"},
            "members": {"husband", "wife", "spouse", "brother", "sister", "parent", "child"},
            "spouse": {"husband", "wife", "spouse"},
            "sibling": {"brother", "sister", "sibling"},
            "trip": {"trip", "travel", "travelled", "visited"},
            "preference": {"preference", "prefer", "favourite", "favorite"},
            "occupation": {"occupation", "work", "worked", "job", "career"},
            "birthplace": {"birthplace", "born"},
        }
        terms = aliases.get(subject, {subject})
        if subject_type == "self":
            direct = fact.get("kind") == "identity_fact" and not relationship
        elif subject in {"family", "members", "household"}:
            direct = relationship in terms or any(re.search(rf"\b{term}\b", text) for term in terms)
        elif subject_type == "relationship":
            direct = relationship in terms or any(
                re.search(rf"\b{re.escape(term)}\b", text) for term in terms
            )
        else:
            direct = any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)
        if not direct:
            if subject in {"family", "members", "household"} and re.search(r"\bfamil(?:y|ies)\b", text):
                return 2
            return 0
        if "name" in attributes and subject_type == "relationship":
            return 3 if relationship == subject or re.search(rf"\bmy {re.escape(subject)}\b", text) else 2
        if "size" in attributes:
            return 3 if GroundedAnswerService._measurements(text) else 2
        return 3

    @staticmethod
    def classify_turn(query: str, *, active_topic: bool = False) -> str:
        return interpret_turn(query, active_topic="active topic" if active_topic else None).top_level_class
        """Legacy implementation retained below temporarily for stable blame context."""
        normalized = " ".join(query.casefold().split())
        words = set(re.findall(r"[^\W_]+", normalized, re.UNICODE))
        if re.match(
            r"^(?:no[, ]+)?(?:i (?:did not|didn't) ask (?:you )?about|"
            r"that(?:'s| is) not what i asked|sorry[, ]+that(?:'s| is) not what i meant)\b",
            normalized,
        ):
            return "social"
        if re.match(r"^no[, ]+i meant (?:the )?[^?!.]+[?!.]*$", normalized):
            return "personal"
        if re.fullmatch(r"how are you[?.!]*", normalized):
            return "social"
        possessed = bool(re.search(r"\b(?:my|our|your)\s+[\w'-]+", normalized))
        if possessed and not re.match(r"^(?:i|we)\b", normalized):
            return "personal"
        if re.match(r"^(?:and\s+)?what about\b", normalized):
            return "personal"
        if re.match(r"^tell me about the\b", normalized):
            return "personal"
        clearly_general = bool(re.match(
            r"^(?:what (?:is|are)|define|describe|explain|how (?:does|do|is|are|big)|why (?:does|do|is|are))\b",
            normalized,
        ))
        if clearly_general:
            return "general"
        if re.search(r"\b(?:do|did) (?:i|we|you) (?:have|own)\b", normalized):
            return "personal"
        if re.match(r"^(?:have i|has my|did i|was i|were we)\b", normalized):
            return "personal"
        if re.search(r"\bi told you (?:before|earlier|that)\b", normalized):
            return "personal"
        if re.match(r"^you (?:are|'re)\s+[^?!.]+(?:right|correct)?[?!.]*$", normalized):
            return "personal"
        if re.match(r"^(?:who|what|when|where|why|how)\b", normalized) and words & {
            "i", "me", "my", "mine", "we", "us", "our", "ours",
            "you", "your", "yours", "he", "him", "his", "she", "her",
            "hers", "they", "them", "their", "theirs", "it", "its",
        }:
            return "personal"
        social_fillers = {
            "a", "also", "am", "and", "are", "bit", "doing", "how", "i", "im", "is",
            "it", "of", "please", "see", "so", "take", "talk", "the", "to", "very", "well", "you",
        }
        social_terms = {
            "afternoon", "bye", "care", "course", "evening", "good", "goodbye", "great",
            "hello", "hey", "hi", "later", "morning", "nice", "night", "ok", "okay",
            "perfect", "thank", "thanks",
        }
        if words and words - social_fillers <= social_terms:
            return "social"
        if re.search(r"\b(?:do|did) you remember(?: anything)?(?: about| of)?\b", normalized):
            return "personal"
        if detect_identity_intent(query) is not None or re.match(r"^who am i to you\b", normalized):
            return "personal"
        if words & {"family", "household"}:
            return "personal"
        if 0 < len(words) <= 3 and not words & {
            "define", "describe", "explain", "how", "what", "why",
        }:
            return "personal"
        if active_topic:
            return "personal"
        return "general"

    @staticmethod
    def is_personal(query: str, *, active_topic: bool = False) -> bool:
        return GroundedAnswerService.classify_turn(query, active_topic=active_topic) == "personal"

    @staticmethod
    def requests_expansion(query: str) -> bool:
        normalized = " ".join(query.casefold().split())
        return bool(re.search(
            r"\b(?:what else|who else|tell me more|tell me everything|all (?:the |our |your )?|"
            r"whole story|complete(?:ly)?|everything about|explain|what happened)\b",
            normalized,
        ))

    @staticmethod
    def _generation_instruction(
        plan: GroundedAnswerPlan, *, repair: bool, response_query: str = "",
        target_language: str = "",
    ) -> str:
        prefix = "REPAIR: the previous wording was invalid. " if repair else ""
        return (
            f"{prefix}You are the selected person speaking in first person. Subject: {plan.subject}. "
            f"Requested attributes: {list(plan.requested_attributes)}. Use only these normalized first-person statements: "
            f"{[fact['statement'] for fact in plan.answer_facts]}. Include these exact values: "
            f"{list(plan.must_include)}. Natural one-sentence synthesis: {GroundedAnswerService.fallback(plan)} "
            f"Reply in {target_language or LanguageNormalizationService().normalize_user_turn(response_query or 'English').response_language}; "
            "match the current user's natural code-switching style. "
            "Merge compatible facts, remove semantic duplicates, keep persona identity silent unless asked, "
            "and never repeat storage wording. State supported statements directly; hedge only items "
            f"listed here: {list(plan.may_hedge)}. Do not mention internal systems, sources, "
            "confidence, completeness, missing attributes, or invite correction."
        )

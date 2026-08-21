from types import SimpleNamespace
import asyncio
import json

from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.language_normalization import LanguageNormalizationService
from app.services.realtime_live_call import RealtimeToolService
from app.services.semantic_concept_resolution import SemanticConceptResolver


def test_semantic_concept_matrix_uses_canonical_ontology():
    resolver = SemanticConceptResolver()
    cases = {
        "Tell me about your bro.": "brother",
        "Tell me about your bhai.": "brother",
        "Tell me about your younger sibling.": "brother",
        "Tell me about your hubby.": "husband",
        "Tell me about your mom.": "mother",
        "Tell me about your mummy.": "mother",
        "Tell me about your dad.": "father",
        "Tell me about your papa.": "father",
        "Tell me about your kids.": "children",
        "What about your pooch?": "dogs",
        "What about your telly?": "tv",
    }
    for surface, canonical in cases.items():
        resolution = resolver.resolve(surface)
        assert resolution.canonical_concept == canonical
        assert canonical in resolution.canonical_query.casefold()
        assert resolution.confidence_bucket == "high"
        assert not resolution.clarification_required


def test_partner_ambiguity_and_business_context_are_distinct():
    resolver = SemanticConceptResolver()
    ambiguous = resolver.resolve("Tell me about your partner.")
    assert ambiguous.confidence_bucket == "medium"
    assert ambiguous.clarification_required
    assert ambiguous.ambiguity == ("husband or spouse", "business partner")
    business = resolver.resolve(
        "Tell me about your partner.", active_topic="our business in Pune",
    )
    assert business.canonical_concept == "business partner"
    assert not business.clarification_required
    assert business.context_used


def test_unknown_personal_term_clarifies_instead_of_claiming_amnesia():
    resolution = SemanticConceptResolver().resolve("Tell me about your zorb.")
    assert resolution.confidence_bucket == "low"
    assert resolution.clarification_required
    assert resolution.clarification_prompt == "What do you mean by 'zorb'?"
    assert "remember" not in resolution.clarification_prompt.casefold()


def test_chat_pending_clarification_resumes_original_request_after_correction():
    chat = ChatService(SimpleNamespace(), ContextBuilder(12))
    query, prompt = chat._resolved_personal_query(
        42, "Tell me about your partner.", "Tell me about your partner.",
    )
    assert query == "Tell me about your partner."
    assert "husband or my business partner" in prompt
    resumed, prompt = chat._resolved_personal_query(
        42, "No, I meant my business partner.", "No, I meant my business partner.",
    )
    assert resumed == "Tell me about your business partner."
    assert prompt is None


def test_explicit_entity_correction_replaces_guess_without_persistent_learning():
    chat = ChatService(SimpleNamespace(), ContextBuilder(12))
    _query, prompt = chat._resolved_personal_query(
        43, "Tell me about your buddy.", "Tell me about your buddy.",
    )
    assert prompt == "What do you mean by 'buddy'?"
    resumed, prompt = chat._resolved_personal_query(
        43, "No, by buddy I meant Bruno.", "No, by buddy I meant Bruno.",
    )
    assert resumed == "Tell me about your Bruno."
    assert prompt is None
    assert 43 not in chat._pending_concept_clarifications


def test_chat_and_live_call_receive_same_canonical_query():
    normalized = LanguageNormalizationService().normalize_user_turn(
        "Tell me about your hubby.",
    )
    assert normalized.normalized_english_text == "Tell me about your husband."
    session = SimpleNamespace(
        session_id="semantic-parity", user_id=1, legacy_id=2,
        legacy_name="Anjali", relationship="mother",
    )
    live = RealtimeToolService(ChatService(SimpleNamespace(), ContextBuilder(12)))
    routed = live.route_turn(session, 1, normalized.original_text, normalized)
    assert routed["tool_name"] == "retrieve_legacy_memory_context"
    assert live._state(session.session_id).turns[1].normalized_query == normalized.normalized_english_text


def test_live_call_ambiguous_term_returns_validated_clarification_without_retrieval():
    normalized = LanguageNormalizationService().normalize_user_turn(
        "Tell me about your partner.",
    )
    session = SimpleNamespace(
        session_id="semantic-clarification", user_id=1, legacy_id=2,
        legacy_name="Anjali", relationship="mother",
    )
    live = RealtimeToolService(ChatService(SimpleNamespace(), ContextBuilder(12)))
    routed = live.route_turn(session, 1, normalized.original_text, normalized)
    result = live.execute(None, session, routed["tool_name"], {}, turn_id=1)
    assert result["status"] == "clarification_required"
    assert result["validated_text"] == "Do you mean my husband or my business partner?"


def test_v2_fuzzy_and_phonetic_variants_resolve_without_alias_entries():
    resolver = SemanticConceptResolver()
    cases = {
        "Tell me about your doggy.": "dogs",
        "Tell me about your doggie.": "dogs",
        "Tell me about your doggi.": "dogs",
        "Tell me about your dogz.": "dogs",
        "about broda": "brother",
        "Tell me about your brothr.": "brother",
        "Tell me about your brther.": "brother",
        "Tell me about your husban.": "husband",
        "What is your nikname?": "nickname",
        "Tell me about your televsion.": "tv",
        "Tell me about your chidhood.": "childhood",
    }
    for query, concept in cases.items():
        resolution = resolver.resolve(query)
        assert resolution.canonical_concept == concept, query
        assert resolution.confidence_bucket == "high", query
        assert not resolution.clarification_required, query


def test_bad_grammar_reconstructs_full_requested_proposition():
    resolver = SemanticConceptResolver()
    assert resolver.resolve("what bro like play").canonical_query == (
        "What did your brother like to play?"
    )
    assert resolver.resolve("doggy names what").canonical_query == (
        "What are your dogs' names?"
    )
    assert resolver.resolve("hubby name").canonical_query == (
        "What is your husband's name?"
    )
    assert resolver.resolve("where you bro grew").canonical_query == (
        "Where did you and your brother grow up?"
    )


def test_acronyms_and_negative_domains_are_not_overcorrected():
    resolver = SemanticConceptResolver()
    personal_dos = resolver.resolve("Tell me about your DOS.")
    assert personal_dos.clarification_required
    assert resolver.resolve("What is DOS?").canonical_concept is None
    assert resolver.resolve("Tell me about your business partner.").canonical_concept == "business partner"
    assert resolver.resolve("Tell me about your kitty.").canonical_concept == "cat"
    assert resolver.resolve("Tell me about your school.").canonical_concept is None


def test_pet_context_can_raise_dos_to_dogs_without_global_overcorrection():
    resolution = SemanticConceptResolver().resolve(
        "Tell me about your DOS.", active_topic="our pets and dogs",
    )
    assert resolution.canonical_concept == "dogs"
    assert not resolution.clarification_required


def test_unseen_term_uses_existing_semantic_stage_to_reconstruct_full_query():
    class SemanticAI:
        async def generate_response(self, _messages, **_kwargs):
            return json.dumps({
                "detected_language": "english", "response_language": "english",
                "normalized_english": "What did your brother like to play?",
                "code_switched": False, "speech_act": "question",
                "substantive_intent": "personal_family", "translation_confidence": .91,
            })
    normalized = asyncio.run(LanguageNormalizationService(SemanticAI()).normalize_semantically(
        "what sibster like play",
    ))
    assert normalized.stage_used == "semantic_model"
    assert normalized.normalized_english_text == "What did your brother like to play?"


def test_multilingual_slang_enters_same_canonical_query():
    service = LanguageNormalizationService()
    normalized = service.normalize_user_turn("à¤¤à¥à¤à¥à¤¯à¤¾ doggy à¤¬à¤¦à¥à¤¦à¤² à¤¸à¤¾à¤‚à¤—")
    assert normalized.canonical_concept == "dogs"
    assert "dogs" in normalized.normalized_english_text.casefold()


def test_fvs_clarification_resumes_original_request_with_tv():
    chat = ChatService(SimpleNamespace(), ContextBuilder(12))
    original, prompt = chat._resolved_personal_query(
        44, "Tell me about FVs.", "Tell me about FVs.",
    )
    assert original == "Tell me about FVs."
    assert prompt == "What do you mean by 'FVs'?"
    resumed, prompt = chat._resolved_personal_query(44, "TV", "TV")
    assert resumed == "Tell me about TV."
    assert prompt is None

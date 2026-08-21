import unittest

from app.services.grounded_answer import GroundedAnswerService
from app.services.personal_answer import PersonalAnswerService


class FakeAI:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def generate_response(self, _messages):
        self.calls += 1
        return self.outputs.pop(0)


def result(*summaries, entities=(), identity=()):
    return {
        "status": "supported", "topic_anchor": "personal subject",
        "selected_legacy": {"name": "Anjali"},
        "identity": list(identity), "resolved_entities": list(entities),
        "memories": [
            {"memory_id": index, "summary": summary,
             "entities": [entity] if entity else [],
             "epistemic_status": "supported"}
            for index, (summary, entity) in enumerate(zip(
                summaries, [*entities, *([None] * len(summaries))], strict=False
            ), 1)
        ],
    }


class GroundedAnswerTests(unittest.IsolatedAsyncioTestCase):
    def test_dirty_self_tv_and_pet_aliases_normalize_before_rendering(self):
        tv = result("Anjali says there is an 85-inch TV at home.")
        tv_plan = GroundedAnswerService.plan("What about your TV?", tv)
        self.assertEqual(GroundedAnswerService.fallback(tv_plan), "We have an 85-inch TV at home.")
        self.assertNotIn("Anjali", GroundedAnswerService.fallback(tv_plan))

        dogs = result(
            "The family now has a Labrador named bruno.",
            "They have another dog named luffy, who is also a Labrador.",
            entities=("bruno", "Luffy/luffy", "Luffy (luffy)"),
        )
        dog_plan = GroundedAnswerService.plan("Do you have dogs?", dogs)
        self.assertEqual(
            GroundedAnswerService.fallback(dog_plan),
            "We have two Labradors, Bruno and Luffy.",
        )
        self.assertEqual(dog_plan.entities, ("Bruno", "Luffy"))

    async def test_corrective_rendering_is_short_grounded_and_silences_persona_name(self):
        payload = result("Our TV is 85 inches.")
        payload.update({
            "original_query": "That's 85 inch, not 85 inches.",
            "response_language": "english", "correction_kind": "correction",
            "correction_subject": "tv",
        })
        plan, answer, source = await GroundedAnswerService(FakeAI([])).produce("tv", payload)
        self.assertEqual((answer, source), ("Right—it's an 85-inch TV.", "deterministic_correction"))
        self.assertNotIn("Anjali", answer)
        self.assertEqual(GroundedAnswerService.validate(plan, "Anjali, we have an 85-inch TV."), ("self_name_address",))

        payload.update({"original_query": "Did I ask you that?", "correction_kind": "objection"})
        _plan, answer, _source = await GroundedAnswerService(FakeAI([])).produce("tv", payload)
        self.assertEqual(answer, "You're right—you asked about the tv.")

    def test_shared_profile_selection_is_strictly_question_targeted(self):
        payload = result(
            "My husband is Mohan Deshmukh.",
            "My younger brother is Aditya Deshmukh.",
            "We have a Labrador named Bruno.",
            "We have another Labrador named Luffy.",
            "Our TV is 85 inches.",
            "I refer to the family pets as 'our dogs,' indicating multiple entities detected.",
            entities=("Mohan Deshmukh", "Aditya Deshmukh", "Bruno", "Luffy"),
            identity=({"identity_fact_id": 91, "fact_type": "full_name",
                       "value": "Anjali Deshmukh", "epistemic_status": "supported"},),
        )
        cases = (
            ("What's your name?", ("Anjali Deshmukh",), ("Aditya", "Mohan", "Bruno", "85")),
            ("Tell me about your family members.", ("Mohan", "Aditya"), ("Bruno", "85")),
            ("Do you know anything about dogs?", ("Bruno", "Luffy", "Labrador"), ("Aditya", "Mohan", "85")),
            ("Do you have any dogs?", ("Bruno", "Luffy", "Labrador"), ("Aditya", "Mohan", "85")),
            ("Do you have a TV?", ("85",), ("Aditya", "Mohan", "Bruno")),
            ("Do you know anything about a TV?", ("85",),
             ("Anjali", "Aditya", "Mohan", "Bruno")),
        )
        for query, included, excluded in cases:
            with self.subTest(query=query):
                plan = GroundedAnswerService.plan(query, payload)
                answer = GroundedAnswerService.fallback(plan)
                self.assertTrue(all(value in answer for value in included), answer)
                self.assertTrue(all(value not in answer for value in excluded), answer)
                self.assertTrue(all(fact.get("kind") != "internal_metadata"
                                    for fact in plan.answer_facts))

        television = GroundedAnswerService.plan(
            "Do you know anything about a TV?", payload,
        )
        self.assertEqual(television.subject, "tv")
        self.assertEqual(television.must_include, ("85 inches",))
        self.assertIn("subject_irrelevance", GroundedAnswerService.validate(
            television, "I'm Anjali Deshmukh. We have an 85-inch TV at home.",
        ))
        self.assertEqual(GroundedAnswerService.validate(
            television, "We have an 85-inch TV at home.",
        ), ())

    def test_nickname_and_activity_attributes_outrank_related_identity(self):
        nickname = GroundedAnswerService.plan(
            "What is your nickname?",
            result(
                "My full name is Anjali Deshmukh.",
                "My nickname is Pinky.",
                identity=({
                    "identity_fact_id": 91, "fact_type": "full_name",
                    "value": "Anjali Deshmukh", "epistemic_status": "supported",
                },),
            ),
        )
        self.assertEqual(nickname.requested_attributes, ("nickname",))
        self.assertIn("Pinky", GroundedAnswerService.fallback(nickname))
        self.assertNotIn("full name", GroundedAnswerService.fallback(nickname))

        evening = GroundedAnswerService.plan(
            "What did you and your brother do in the evenings?",
            result(
                "My younger brother is Aditya Deshmukh.",
                "My younger brother Aditya and I sat together and talked every evening.",
                entities=("Aditya Deshmukh",),
            ),
        )
        self.assertEqual(evening.requested_attributes, ("activity", "time_context"))
        answer = GroundedAnswerService.fallback(evening)
        self.assertIn("talked every evening", answer)
        self.assertNotEqual(answer, "My younger brother is Aditya Deshmukh.")

    def test_explicit_multi_subject_query_selects_each_requested_subject_only(self):
        payload = result(
            "My husband is Mohan Deshmukh.",
            "My younger brother is Aditya Deshmukh.",
            "We have a Labrador named Bruno.",
            "We have another Labrador named Luffy.",
            "Our TV is 85 inches.",
            entities=("Mohan Deshmukh", "Aditya Deshmukh", "Bruno", "Luffy"),
            identity=({"identity_fact_id": 91, "fact_type": "full_name",
                       "value": "Anjali Deshmukh", "epistemic_status": "supported"},),
        )
        plan = GroundedAnswerService.plan(
            "Tell me about your husband and your dogs.", payload,
        )
        answer = GroundedAnswerService.fallback(plan)
        self.assertEqual((plan.subject, plan.subject_type), ("husband+dogs", "multi"))
        self.assertIn("Mohan Deshmukh", answer)
        self.assertTrue("Bruno" in answer or "Luffy" in answer)
        self.assertNotIn("Anjali", answer)
        self.assertNotIn("Aditya", answer)
        self.assertNotIn("85 inches", answer)

    def test_corrective_turn_is_personal_and_validator_rejects_true_but_wrong_subject(self):
        self.assertEqual(GroundedAnswerService.classify_turn(
            "I didn't ask you about your younger brother.", active_topic=True,
        ), "personal")
        self.assertEqual(GroundedAnswerService.classify_turn(
            "No, I meant the TV.", active_topic=True,
        ), "personal")
        payload = result(
            "My younger brother is Aditya Deshmukh.", "Our TV is 85 inches.",
            entities=("Aditya Deshmukh",),
        )
        plan = GroundedAnswerService.plan("Do you have a TV?", payload)
        self.assertIn("subject_irrelevance", GroundedAnswerService.validate(
            plan, "My younger brother is Aditya Deshmukh.",
        ))

    def test_self_paraphrases_resolve_to_identity_only(self):
        payload = result(
            "My younger brother is Aditya.",
            identity=({"identity_fact_id": 91, "fact_type": "full_name",
                       "value": "Anjali", "epistemic_status": "supported"},),
        )
        for query in ("What's your name?", "Who are you?"):
            plan = GroundedAnswerService.plan(query, payload)
            self.assertEqual(plan.intent, "attribute_query")
            self.assertEqual(GroundedAnswerService.fallback(plan), "My name is Anjali.")
    async def test_invalid_generation_repairs_once_without_rerunning_plan(self):
        payload = result("Our TV is 85 inches.")
        ai = FakeAI([
            "I don't remember anything about our TV.",
            "Our TV is 85 inches.",
        ])
        plan, text, source = await GroundedAnswerService(ai).produce("Our TV", payload)
        self.assertEqual((text, source, ai.calls), ("Our TV is 85 inches.", "repaired", 2))
        self.assertEqual(plan.must_include, ("85 inches",))

    async def test_second_invalid_generation_uses_natural_deterministic_fallback(self):
        payload = result(
            "The family has a Labrador named Bruno.",
            "The family has a Labrador named Luffy.",
            entities=("Bruno", "Luffy"),
        )
        ai = FakeAI([
            "We have Bruno.",
            "According to the stored memory, we have Bruno.",
        ])
        plan, text, source = await GroundedAnswerService(ai).produce("Our dogs", payload)
        self.assertEqual(source, "deterministic_fallback")
        self.assertEqual(ai.calls, 2)
        self.assertIn("Bruno", text)
        self.assertIn("Luffy", text)
        self.assertEqual(text, "We have two Labradors, Bruno and Luffy.")
        self.assertEqual(GroundedAnswerService.validate(plan, text), ())

    def test_validator_rejects_internal_language_false_unknown_omission_and_contradiction(self):
        plan = GroundedAnswerService.plan(
            "our TV", result("Our TV is 85 inches."),
        )
        self.assertIn("false_no_memory", GroundedAnswerService.validate(
            plan, "I don't remember our TV.",
        ))
        self.assertIn("internal_language", GroundedAnswerService.validate(
            plan, "According to the stored memory, our TV is 85 inches.",
        ))
        dogs = GroundedAnswerService.plan("our dogs", result(
            "Bruno is a Labrador.", "Luffy is a Labrador.",
            entities=("Bruno", "Luffy"),
        ))
        self.assertIn("required_omission", GroundedAnswerService.validate(
            dogs, "We have Bruno.",
        ))
        self.assertIn("authoritative_value_contradiction", GroundedAnswerService.validate(
            plan, "Our TV is both 30 inches and 85 inches.",
        ))

    def test_plan_normalizes_first_person_and_rejects_wrong_perspective_or_subject(self):
        dogs = GroundedAnswerService.plan(
            "Dogs' names and breeds?",
            result("Anjali's family has a Labrador named Bruno.", entities=("Bruno",)),
        )
        self.assertEqual(dogs.response_perspective, "first_person_legacy")
        self.assertEqual(dogs.requested_attributes, ("name", "breed"))
        self.assertTrue(dogs.facts[0]["statement"].startswith("We have"))
        self.assertIn("wrong_perspective", GroundedAnswerService.validate(
            dogs, "Anjali's family has a Labrador named Bruno.",
        ))
        self.assertIn("wrong_subject", GroundedAnswerService.validate(
            dogs, "My name is Anjali. Bruno is a Labrador.",
        ))
        self.assertIn("unnecessary_hedging", GroundedAnswerService.validate(
            dogs, "I'm not sure, but maybe Bruno is a Labrador.",
        ))
        self.assertIn("shared_household_perspective", GroundedAnswerService.validate(
            dogs, "My family has a Labrador named Bruno.",
        ))

    def test_social_general_personal_precedence_ignores_active_topic_for_closings(self):
        cases = {
            "Hello, how are you?": "social", "Thanks.": "social",
            "Okay.": "social", "Perfect, bye.": "social", "Good night.": "social",
            "Describe a mango.": "general", "How does an OLED TV work?": "general",
            "Thanks, tell me about our dogs.": "personal",
            "Okay, and the TV?": "personal",
            "Bye, what time was our flight?": "personal",
            "Their names?": "personal", "What size?": "personal",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(
                    GroundedAnswerService.classify_turn(query, active_topic=True), expected,
                )

    def test_mixed_world_question_stays_general_and_identity_confirmation_is_personal(self):
        self.assertEqual(GroundedAnswerService.classify_turn(
            "What is an avocado? I've never eaten an avocado.", active_topic=True,
        ), "general")
        self.assertEqual(GroundedAnswerService.classify_turn(
            "How does an OLED TV work? I've never used one.", active_topic=True,
        ), "general")
        self.assertEqual(GroundedAnswerService.classify_turn(
            "Have I ever eaten avocado?",
        ), "personal")
        self.assertEqual(GroundedAnswerService.classify_turn(
            "You are Anjali, right?",
        ), "personal")

    def test_canonical_persona_rejects_name_and_family_denial(self):
        family = GroundedAnswerService.plan(
            "Who else is there in your family?",
            result("My husband is Mohan.", "My younger brother is Aditya."),
        )
        self.assertIn("identity_denial", GroundedAnswerService.validate(
            family, "I don't have a family in the human sense; I'm just an AI.",
        ))
        identity = GroundedAnswerService.plan(
            "You are Anjali, right?", {
                "selected_legacy": {"name": "Anjali"},
                "identity": [{"identity_fact_id": 1, "fact_type": "full_name",
                              "value": "Anjali", "epistemic_status": "supported"}],
                "memories": [], "resolved_entities": ["Anjali"],
            },
        )
        self.assertIn("identity_denial", GroundedAnswerService.validate(
            identity, "I don't have a name.",
        ))

    def test_subject_precedes_name_attribute_and_general_gate_stays_direct(self):
        self.assertEqual(
            GroundedAnswerService.understand("Dogs' names and breeds?"),
            ("dogs", "memory", ("name", "breed")),
        )
        self.assertEqual(
            GroundedAnswerService.understand("What is your husband's name?"),
            ("husband", "relationship", ("name",)),
        )
        self.assertTrue(GroundedAnswerService.is_personal("What size is our TV?"))
        self.assertFalse(GroundedAnswerService.is_personal("How does an OLED TV work?"))
        self.assertEqual(
            GroundedAnswerService.understand("Do you have any dogs?"),
            ("dogs", "memory", ("existence",)),
        )

    def test_cross_subject_plans_are_nonempty_and_server_owned(self):
        fixtures = {
            "family": "My younger brother is Aditya.",
            "spouse": "My husband is Mohan.",
            "sibling": "My sister is Meera.",
            "pets": "We have a dog named Bruno.",
            "TV": "Our TV is 85 inches.",
            "car": "Our car is a Honda City.",
            "house": "Our house has a balcony.",
            "garden": "Our garden has jasmine.",
            "trip": "We travelled to Goa.",
            "preference": "My favourite food is poha.",
            "occupation": "I worked as a teacher.",
            "birthplace": "I was born in Pune.",
        }
        for subject, statement in fixtures.items():
            with self.subTest(subject=subject):
                plan = GroundedAnswerService.plan(subject, result(statement))
                self.assertEqual(plan.status, "supported")
                self.assertTrue(plan.facts)
                self.assertNotEqual(GroundedAnswerService.fallback(plan), "I don't remember that.")

    def test_retrieves_broadly_but_defaults_to_two_best_facts(self):
        payload = result(
            "My husband is Mohan Deshmukh.",
            "My younger brother is Aditya Deshmukh.",
            "We have a Labrador named Bruno.",
            "We have another Labrador named Luffy.",
            "I grew up with my family in Pune.",
            "We took a family trip to Goa.",
        )
        plan = GroundedAnswerService.plan("Tell me about your family.", payload)
        self.assertEqual(len(plan.facts), 6)
        self.assertEqual(len(plan.answer_facts), 2)
        self.assertIn("Mohan Deshmukh", GroundedAnswerService.fallback(plan))
        self.assertIn("Aditya Deshmukh", GroundedAnswerService.fallback(plan))
        self.assertNotIn("Bruno", GroundedAnswerService.fallback(plan))

    def test_one_or_indirect_fact_prevents_false_no_memory(self):
        one = GroundedAnswerService.plan(
            "Tell me about your family.", result("My husband is Mohan Deshmukh."),
        )
        self.assertEqual(len(one.answer_facts), 1)
        self.assertEqual(GroundedAnswerService.fallback(one), "My husband is Mohan Deshmukh.")

        indirect_payload = result("I grew up with my family in Pune.")
        indirect_payload["memories"][0]["relevance_level"] = 3
        indirect = GroundedAnswerService.plan("Tell me about your family.", indirect_payload)
        self.assertEqual(indirect.status, "supported")
        self.assertEqual(indirect.answer_facts[0]["relevance_level"], 3)
        self.assertIn("Pune", GroundedAnswerService.fallback(indirect))

    def test_expansion_keeps_all_best_level_facts_and_level_four_is_noise(self):
        payload = result("Mohan.", "Aditya.", "Bruno.", "Luffy.")
        initial = GroundedAnswerService.plan("Tell me about your family.", payload)
        expanded = GroundedAnswerService.plan("What else?", payload)
        self.assertEqual((len(initial.facts), len(initial.answer_facts)), (4, 2))
        self.assertEqual((len(expanded.facts), len(expanded.answer_facts)), (4, 4))

        noise = result("The bicycle shop is downtown.")
        noise["memories"][0]["relevance_level"] = 4
        empty = GroundedAnswerService.plan("What was our bicycle like?", noise)
        self.assertEqual(empty.status, "unsupported")
        self.assertEqual(empty.answer_facts, ())

    def test_natural_renderer_merges_profile_examples_and_keeps_persona_silent(self):
        profile = result(
            "The family now has a Labrador named Bruno.",
            "They have another dog named Luffy, who is also a Labrador.",
            "Our TV is 85 inches.",
            entities=("Bruno", "Luffy"),
        )
        dogs = GroundedAnswerService.plan("Do you have dogs?", profile)
        self.assertEqual(
            GroundedAnswerService.fallback(dogs),
            "We have two Labradors, Bruno and Luffy.",
        )
        tv = GroundedAnswerService.plan("What about our TV?", profile)
        self.assertEqual(GroundedAnswerService.fallback(tv), "We have an 85-inch TV.")
        self.assertNotIn("Anjali", GroundedAnswerService.fallback(tv))

    def test_natural_renderer_joins_relationships_and_deduplicates_identity(self):
        family = GroundedAnswerService.plan("Who is in your family?", result(
            "My husband is Mohan Deshmukh.",
            "My younger brother is Aditya Deshmukh.",
        ))
        self.assertEqual(
            GroundedAnswerService.fallback(family),
            "My husband is Mohan Deshmukh, and my younger brother is Aditya Deshmukh.",
        )
        identity = GroundedAnswerService.plan("What's your name?", result(
            "Anjali stated that her full real name is Anjali Deshmukh.",
            identity=({"identity_fact_id": 1, "fact_type": "full_name",
                       "value": "Anjali Deshmukh", "epistemic_status": "supported"},),
        ))
        self.assertEqual(len(identity.answer_facts), 1)
        self.assertEqual(GroundedAnswerService.fallback(identity), "My name is Anjali Deshmukh.")

    def test_renderer_firewall_rejects_storage_language_duplicates_and_bad_case(self):
        plan = GroundedAnswerService.plan("What's your name?", result(
            identity=({"identity_fact_id": 1, "fact_type": "full_name",
                       "value": "Anjali Deshmukh", "epistemic_status": "supported"},),
        ))
        self.assertIn("raw_storage_language", GroundedAnswerService.validate(
            plan, "Anjali stated that her full real name is Anjali Deshmukh.",
        ))
        self.assertIn("semantic_duplicate", GroundedAnswerService.validate(
            plan, "My name is Anjali Deshmukh. My name is Anjali Deshmukh.",
        ))
        self.assertIn("canonical_capitalization", GroundedAnswerService.validate(
            plan, "My name is anjali deshmukh.",
        ))

    def test_scoped_conflict_fallback_does_not_claim_global_amnesia(self):
        payload = result("Our TV is 85 inches.")
        payload["memories"][0]["epistemic_status"] = "conflicted"
        plan = GroundedAnswerService.plan("What size is our TV?", payload)
        self.assertEqual(plan.status, "conflicted")
        self.assertTrue(GroundedAnswerService.fallback(plan).startswith("I'm not sure whether"))
        self.assertNotIn("remember", GroundedAnswerService.fallback(plan).casefold())

    def test_identity_confirmation_is_a_brief_acknowledgement(self):
        plan = GroundedAnswerService.plan(
            "You are not ChatGPT, you are Anjali.",
            result(identity=({"identity_fact_id": 1, "fact_type": "full_name",
                              "value": "Anjali Deshmukh", "epistemic_status": "supported"},)),
        )
        self.assertEqual(plan.subject_type, "self")
        self.assertEqual(plan.intent, "confirmation")
        self.assertEqual(GroundedAnswerService.fallback(plan), "Yes, I'm Anjali Deshmukh.")

    def test_expansion_prefers_unused_rendering_facts_without_mutating_payload(self):
        payload = result(
            "My younger brother is Aditya Deshmukh.",
            "My younger brother grew up in Pune.",
            "My younger brother worked as a teacher.",
        )
        first = GroundedAnswerService.plan("Tell me about your brother.", payload)
        used = tuple(fact["source_id"] for fact in first.answer_facts)
        expanded_payload = {**payload, "_rendering_excluded_source_ids": used}
        expanded = GroundedAnswerService.plan("What else?", expanded_payload)
        self.assertTrue(expanded.answer_facts)
        self.assertTrue(all(fact["source_id"] not in used for fact in expanded.answer_facts))
        self.assertNotIn("_rendering_excluded_source_ids", payload)

    async def test_non_english_turn_rejects_english_generation_and_localizes_fallback(self):
        payload = result("Our TV is 85 inches.")
        payload["original_query"] = "आपला TV किती inch आहे?"
        ai = FakeAI(["The TV is 85 inches.", "The TV is 85 inches."])
        _plan, text, source = await GroundedAnswerService(ai).produce(
            "What size is our TV?", payload,
        )
        self.assertEqual(source, "deterministic_fallback")
        self.assertEqual(text, "हो, आमच्याकडे 85-inch TV आहे.")

    async def test_explicit_marathi_target_never_speaks_english_canonical_tv_or_dogs(self):
        tv_payload = result("Our TV is 85 inches.")
        tv_payload.update({
            "original_query": "तुझ्याकडे टीव्ही आहे का?",
            "response_language": "marathi",
        })
        _plan, tv, source = await GroundedAnswerService(
            FakeAI(["We have an 85-inch TV at home.", "We have an 85-inch TV at home."])
        ).produce("Do you have a TV?", tv_payload)
        self.assertEqual((source, tv), (
            "deterministic_fallback", "हो, आमच्याकडे 85-inch TV आहे.",
        ))

        dogs_payload = result(
            "The family now has a Labrador named Bruno.",
            "They have another dog named Luffy, who is also a Labrador.",
            entities=("Bruno", "Luffy"),
        )
        dogs_payload.update({
            "original_query": "Okay, तुझ्याकडे डॉग्स आहेत का?",
            "response_language": "marathi",
        })
        _plan, dogs, source = await GroundedAnswerService(
            FakeAI(["We have two Labradors, bruno and luffy.",
                    "We have two Labradors, bruno and luffy."])
        ).produce("Do you have dogs?", dogs_payload)
        self.assertEqual(source, "deterministic_fallback")
        self.assertEqual(dogs, "हो, आमच्याकडे दोन Labradors आहेत—Bruno आणि Luffy.")

    async def test_detailed_english_memory_never_leaks_verbatim_on_marathi_failure(self):
        payload = result("My younger brother grew up with me in Pune and we were very close.")
        payload.update({
            "original_query": "तुझ्या भावाबद्दल अजून सांग.",
            "response_language": "marathi",
        })
        english = "My younger brother grew up with me in Pune and we were very close."
        _plan, text, source = await GroundedAnswerService(
            FakeAI([english, english])
        ).produce("Tell me more about your brother.", payload)
        self.assertEqual(source, "deterministic_fallback")
        self.assertNotEqual(text, english)
        self.assertRegex(text, r"[\u0900-\u097f]")

    def test_teasing_request_selects_teasing_and_closeness_not_evening_activity(self):
        payload = result(
            "My younger brother is Aditya Deshmukh.",
            "I used to take evening walks with my brother, and we played cricket.",
            "I used to tease Aditya a lot, and we were very close.",
            entities=("Aditya Deshmukh",),
        )
        plan = GroundedAnswerService.plan("Did you use to tease your brother?", payload)
        statements = tuple(fact["statement"] for fact in plan.answer_facts)
        self.assertTrue(any("tease" in value.casefold() for value in statements))
        self.assertFalse(any("evening walks" in value.casefold() for value in statements))
        self.assertIn("teasing", plan.requested_attributes)

    def test_childhood_request_selects_shared_story_not_school_as_self_name(self):
        payload = result(
            "My school was Vidya Mandir School in Pune.",
            "My younger brother is Aditya Deshmukh.",
            "Aditya and I grew up together in Pune, played cricket, and were very close.",
            entities=("Aditya Deshmukh",),
        )
        plan = GroundedAnswerService.plan(
            "What is your childhood memory with your brother?", payload,
        )
        statements = tuple(fact["statement"] for fact in plan.answer_facts)
        self.assertTrue(any("grew up together" in value.casefold() for value in statements))
        self.assertFalse(any("my name is vidya" in value.casefold() for value in statements))
        self.assertIn("childhood_narrative", plan.requested_attributes)

    async def test_shared_personal_result_has_evidence_fact_and_text_parity(self):
        payload = result(
            "My younger brother is Aditya Deshmukh.",
            "I used to tease Aditya a lot, and we were very close.",
            entities=("Aditya Deshmukh",),
        )
        service = PersonalAnswerService(GroundedAnswerService(FakeAI([
            "Yes, I used to tease Aditya a lot, and we were very close.",
            "Yes, I used to tease Aditya a lot, and we were very close.",
        ])))
        chat = await service.answer("Did you tease your brother?", payload)
        live = await service.answer("Did you tease your brother?", payload)
        self.assertEqual(chat.selected_evidence_ids, live.selected_evidence_ids)
        self.assertEqual(chat.answer_facts, live.answer_facts)
        self.assertEqual(chat.must_include, live.must_include)
        self.assertEqual(chat.validated_text, live.validated_text)

    def test_generic_small_collections_are_complete_across_domains(self):
        cases = (
            ("Who are your siblings?", ("My brother is Aditya.", "My sister is Riya."), ("Aditya", "Riya")),
            ("Who are your children?", ("My son is Aarav.", "My daughter is Mira."), ("Aarav", "Mira")),
            ("Which cars did we own?", ("We owned a car named Honda City.", "We owned a car named Volkswagen Golf."), ("Honda City", "Volkswagen Golf")),
            ("Which schools did you attend?", ("I attended Vidya Mandir school.", "I attended ABC College."), ("Vidya Mandir", "ABC College")),
            ("Where have you travelled?", ("I travelled to Pune.", "I travelled to Goa.", "I travelled to Berlin."), ("Pune", "Goa", "Berlin")),
        )
        for query, summaries, entities in cases:
            with self.subTest(query=query):
                plan = GroundedAnswerService.plan(query, result(*summaries, entities=entities))
                self.assertEqual(
                    {fact["entity"] for fact in plan.answer_facts}, set(entities),
                )

    async def test_supported_coverage_repairs_false_amnesia_on_first_answer(self):
        payload = result(
            "We have a Labrador named Bruno.",
            "We have a Labrador named Luffy.",
            entities=("Bruno", "Luffy"),
        )
        service = PersonalAnswerService(GroundedAnswerService(FakeAI([
            "I don't remember their names.",
            "I don't remember their names.",
        ])))
        answer = await service.answer("What are their names?", payload)
        self.assertEqual(answer.coverage.status, "SUPPORTED")
        self.assertFalse(answer.coverage.no_memory_allowed)
        self.assertIn("Bruno", answer.validated_text)
        self.assertIn("Luffy", answer.validated_text)

    def test_partial_knowledge_uses_known_value_and_scopes_unknown_member(self):
        payload = result("Aarav's birthday is June 4.", entities=("Aarav",))
        payload["memories"].append({
            "memory_id": 2, "summary": "Mira's birthday is unknown.",
            "entities": ["Mira"], "epistemic_status": "uncertain",
        })
        plan = GroundedAnswerService.plan("What are their birthdays?", payload)
        coverage = PersonalAnswerService.evidence_coverage(plan)
        text = GroundedAnswerService.fallback(plan)
        self.assertEqual(coverage.status, "PARTIAL")
        self.assertIn("June 4", text)
        self.assertIn("Mira", text)
        self.assertNotEqual(text, "I don't remember that.")

    async def test_narrow_question_repairs_background_memory_dump(self):
        payload = result(
            "My younger brother is Aditya.",
            "We grew up together in Pune.",
            "We were very close.",
            "We took evening walks.",
            "We played cricket.",
            "I teased Aditya as a child.",
            "I attended Vidya Mandir School.",
            "We have an 85-inch TV.",
            entities=("Aditya",),
        )
        dump = "My brother is Aditya. We grew up in Pune. We were close. We took evening walks. We played cricket. I teased him. I attended Vidya Mandir School. We have an 85-inch TV."
        direct = "Yes, I used to tease Aditya, and we were very close."
        service = PersonalAnswerService(GroundedAnswerService(FakeAI([dump, direct])))
        answer = await service.answer("Did you tease your brother?", payload)
        self.assertIn("tease", answer.validated_text.casefold())
        self.assertNotIn("85-inch", answer.validated_text)
        self.assertNotIn("Vidya Mandir", answer.validated_text)

    def test_other_one_is_a_generic_unused_evidence_expansion(self):
        self.assertTrue(GroundedAnswerService.requests_expansion(
            "What's the other one's name?",
        ))
        self.assertTrue(GroundedAnswerService.requests_expansion(
            "What about the other car?",
        ))

    def test_broad_childhood_budget_uses_strong_subset_without_household_dump(self):
        payload = result(
            "My younger brother is Aditya.",
            "We grew up together in Pune.",
            "We played cricket together as children.",
            "I teased Aditya when we were young.",
            "We were very close.",
            "We took evening walks.",
            "I attended Vidya Mandir School.",
            "We have an 85-inch TV.",
            entities=("Aditya",),
        )
        plan = GroundedAnswerService.plan(
            "Tell me about your childhood with your brother.", payload,
        )
        text = GroundedAnswerService.fallback(plan)
        self.assertGreaterEqual(len(plan.answer_facts), 2)
        self.assertLessEqual(len(plan.answer_facts), 4)
        self.assertNotIn("85-inch", text)

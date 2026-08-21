from app.services.turn_understanding import interpret_turn


def test_classification_matrix_is_independent_of_previous_personal_topic():
    general = (
        "What is an avocado?", "Why is avocado bitter?", "What is a Labrador?",
        "How do dogs see colors?", "How does a TV work?", "Why are TVs measured diagonally?",
    )
    personal = (
        "Have I eaten avocado?", "Do we have dogs?", "What breed are our dogs?",
        "What about our TV?", "Who is your brother?",
    )
    social = ("Hello", "Thanks", "Perfect, bye")
    for query in general:
        assert interpret_turn(query, active_topic="brother").top_level_class == "general"
    for query in personal:
        assert interpret_turn(query, active_topic="mango").top_level_class == "personal"
    for query in social:
        assert interpret_turn(query, active_topic="tv").top_level_class == "social"
    followup = interpret_turn("What size is it?", active_topic="tv")
    assert followup.top_level_class == "personal"
    assert followup.referential_followup
    assert followup.resolved_topic == "tv"


def test_exact_sequence_and_random_explicit_topic_switches():
    topic = None
    sequence = (
        ("Hi, what's your name?", "personal", "represented person"),
        ("Can you describe an avocado? I've never eaten one.", "general", None),
        ("Why is avocado bitter?", "general", None),
        ("Tell me about your dogs.", "personal", "dogs"),
        ("Okay, what about the TV?", "personal", "tv"),
        ("What size is it?", "personal", "tv"),
    )
    for query, expected_class, expected_topic in sequence:
        turn = interpret_turn(query, active_topic=topic)
        assert turn.top_level_class == expected_class
        assert turn.resolved_topic == expected_topic
        topic = turn.resolved_topic if expected_class == "personal" else None

    for query, expected in (
        ("Who is your brother?", "brother"), ("What about our TV?", "tv"),
        ("Why is avocado bitter?", None), ("Tell me about your dogs", "dogs"),
        ("What's your name?", "represented person"), ("Why are mangoes sweet?", None),
        ("What about our TV?", "tv"),
    ):
        turn = interpret_turn(query, active_topic=topic)
        assert turn.resolved_topic == expected
        topic = expected


def test_corrections_objections_and_refocus_stay_on_immediate_subject():
    cases = (
        ("That's 85 inch, not 85 inches.", "correction", "tv", True),
        ("Did I ask you that dumbass?", "objection", "tv", True),
        ("That's not what I asked.", "objection", "tv", True),
        ("I asked you about the TV.", "clarification", "tv", False),
        ("No, I meant your brother.", "clarification", "brother", False),
        ("No, Bruno, not Luffy.", "correction", "bruno", False),
    )
    for query, act, subject, anchored in cases:
        turn = interpret_turn(query, active_topic="tv")
        assert turn.top_level_class == "personal"
        assert turn.speech_act == act
        assert turn.resolved_topic == subject
        assert turn.uses_previous_answer_anchor is anchored


def test_general_turn_clears_personal_continuity_in_live_router():
    from types import SimpleNamespace
    from app.services.realtime_live_call import RealtimeToolService

    service = RealtimeToolService(SimpleNamespace())
    session = SimpleNamespace(session_id="continuity-cancel")
    assert service.route_turn(session, 1, "Who is your brother?")["route"] == "memory"
    state = service._state(session.session_id)
    state.last_query = "Who is your brother?"
    state.last_memory_topic = "brother"
    assert service.route_turn(session, 2, "Why is avocado bitter?")["route"] == "direct"
    assert state.last_query is None
    assert state.last_memory_topic is None


def test_marathi_and_hindi_normalized_corrections_refocus_tv_and_keep_language():
    from types import SimpleNamespace
    from app.services.realtime_live_call import RealtimeToolService

    service = RealtimeToolService(SimpleNamespace())
    for turn_id, language, english in (
        (1, "marathi", "I asked about the TV."),
        (2, "hindi", "I asked about the TV."),
    ):
        session = SimpleNamespace(session_id=f"correction-{language}")
        service._state(session.session_id).last_answer_subject = "dogs"
        normalized = SimpleNamespace(
            normalized_english_text=english, response_language=language,
            stage_used="semantic_model", detected_language=language,
            code_switching=False, language_confidence=.95,
            normalization_success=True, fallback_used=False,
        )
        route = service.route_turn(session, turn_id, "localized correction", normalized)
        turn = service._state(session.session_id).turns[turn_id]
        assert route == {
            "route": "memory", "tool_name": "retrieve_legacy_memory_context",
            "response_language": language,
        }
        assert turn.understanding.resolved_topic == "tv"
        assert turn.understanding.corrective_kind == "clarification"


def test_native_persistence_requires_completed_playback_and_is_idempotent():
    from types import SimpleNamespace
    from app.services.realtime_live_call import RealtimeToolService

    service = RealtimeToolService(SimpleNamespace())
    session = SimpleNamespace(session_id="native-persistence")
    service.route_turn(session, 1, "Why is avocado bitter?")
    assert service.assistant_turn_decision(
        session, 1, "native-1", "A final provider transcript.",
        response_owner="native_realtime", playback_completed=False,
    ) == "conflict"
    assert service.assistant_turn_decision(
        session, 1, "native-1", "A final provider transcript.",
        response_owner="native_realtime", playback_completed=True,
    ) == "create"
    assert service.assistant_turn_decision(
        session, 1, "native-1", "A final provider transcript.",
        response_owner="native_realtime", playback_completed=True,
    ) == "duplicate"

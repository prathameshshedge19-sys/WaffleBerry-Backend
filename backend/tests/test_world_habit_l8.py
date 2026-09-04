from datetime import date, timedelta

from sqlalchemy import distinct, func, select

from app.models.collaboration import LegacyCollaborator
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryStatus
from app.models.progress import BuilderActivity, DailyPrompt
from app.services.memory import MemoryAnalysis, MemoryCandidate
from app.services.progression import daily_prompt, legacy_progress, record_builder_activity, streak_summary
from app.services.web_search import WebSearchResult, WebSource, minimize_search_query
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import active_legacy, create_visitor_chat, generate_legacy_code, grant, headers


def visitor_fixture(test_context, suffix="base"):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email=f"l8-owner-{suffix}@example.com", name="Prathamesh")
    owner_chat = active_legacy(client, sessions, owner)
    visitor = register_user(client, codes, email=f"l8-visitor-{suffix}@example.com", name="Visitor")
    grant(client, visitor, generate_legacy_code(client, owner, owner_chat["legacy_id"]))
    chat = create_visitor_chat(client, visitor, owner_chat["legacy_id"])
    return client, sessions, provider, owner, owner_chat, visitor, chat


def visitor_stream(client, visitor, chat, content):
    return client.post(
        f"/api/v1/legacy-conversations/{chat['id']}/messages/stream?legacy_id={chat['legacy_id']}",
        json={"content": content}, headers=headers(visitor),
    )


def builder_stream(client, auth, conversation, content, timezone="Europe/Berlin"):
    return client.post(
        f"/api/v1/conversations/{conversation['id']}/messages/stream?legacy_id={conversation['legacy_id']}&timezone={timezone}",
        json={"content": content}, headers=headers(auth),
    )


def add_raw_memory(db, legacy_id, text, category, fingerprint, status="active", story_key=None):
    memory = Memory(
        legacy_id=legacy_id, canonical_text=text, category=category, subject_reference="Pallavi",
        source_language="english", source_excerpt=text, confidence=.95, status=status, operation_type="new",
        explicit_save=False, normalized_fingerprint=fingerprint, story_key=story_key,
    )
    db.add(memory); db.flush(); return memory


def test_general_and_personal_do_not_search_but_fresh_and_mixed_do(test_context):
    client, sessions, provider, owner, owner_chat, visitor, chat = visitor_fixture(test_context, "routing")
    with sessions() as db:
        add_raw_memory(db, chat["legacy_id"], "Pallavi studied in Pune.", "education", "a" * 64)
        add_raw_memory(db, chat["legacy_id"], "Pallavi loves jasmine flowers.", "preference", "b" * 64)
        db.commit()
    questions = ["Describe a mango.", "What flowers do you like?", "What is happening in Kashmir today?", "You studied in Pune. What is the weather there today?"]
    for question in questions:
        response = visitor_stream(client, visitor, chat, question)
        assert response.status_code == 200 and "event: error" not in response.text
    assert provider.web_provider.calls == questions[2:]
    mixed_prompt = "\n".join(turn.content for turn in provider.persona_provider.calls[-1])
    assert "Pallavi studied in Pune" in mixed_prompt and "CURRENT WEB INFORMATION" in mixed_prompt
    assert minimize_search_query("You studied in Pune. What is the weather there today?") == "Pune. What is the weather there today?"


def test_fresh_sources_are_safe_persistent_and_never_write_memory(test_context):
    client, sessions, provider, owner, owner_chat, visitor, chat = visitor_fixture(test_context, "sources")
    question = "What is happening in Kashmir today?"
    provider.web_provider.results[question] = WebSearchResult(
        digest="A verified current digest.",
        sources=(WebSource("Official update", "gov.example", "https://gov.example/update"),),
    )
    before = client.get(f"/api/v1/memories?legacy_id={chat['legacy_id']}", headers=headers(owner)).json()
    response = visitor_stream(client, visitor, chat, question)
    assert 'event: activity' in response.text and 'event: sources' in response.text
    assert "gov.example" in response.text and "route\": \"fresh" in response.text
    history = client.get(f"/api/v1/legacy-conversations/{chat['id']}/messages?legacy_id={chat['legacy_id']}", headers=headers(visitor)).json()
    assert history[-1]["web_sources"] == [{"title": "Official update", "domain": "gov.example", "url": "https://gov.example/update", "publication_date": None}]
    after = client.get(f"/api/v1/memories?legacy_id={chat['legacy_id']}", headers=headers(owner)).json()
    assert after == before
    with sessions() as db:
        assert db.scalar(select(func.count(BuilderActivity.id))) == 0


def test_web_failure_is_graceful_in_role_and_multilingual_fresh_routes(test_context):
    client, sessions, provider, _owner, _owner_chat, visitor, chat = visitor_fixture(test_context, "failure")
    failed = "What is the weather today?"
    provider.web_provider.failures.add(failed)
    response = visitor_stream(client, visitor, chat, failed)
    assert response.status_code == 200 and "event: done" in response.text and "event: error" not in response.text
    prompt = "\n".join(turn.content for turn in provider.persona_provider.calls[-1])
    assert "CURRENT INFORMATION UNAVAILABLE" in prompt
    for question in ("Pune ka mausam aaj kya hai?", "Pune चे हवामान आज काय आहे?", "Wie ist das Wetter heute in Pune?"):
        assert visitor_stream(client, visitor, chat, question).status_code == 200
    assert provider.web_provider.calls[-3:] == ["Pune ka mausam aaj kya hai?", "Pune चे हवामान आज काय आहे?", "Wie ist das Wetter heute in Pune?"]


def test_personal_missing_plus_current_keeps_memory_boundary(test_context):
    client, sessions, provider, _owner, _owner_chat, visitor, chat = visitor_fixture(test_context, "missing")
    question = "What was your Kashmir trip like, and what is Kashmir like today?"
    provider.persona_provider.responses[question] = "I don't remember my Kashmir experience clearly. From what I can see, Kashmir currently has a new public update."
    response = visitor_stream(client, visitor, chat, question)
    assert "don't remember my Kashmir" in response.text
    prompt = "\n".join(turn.content for turn in provider.persona_provider.calls[-1])
    assert "Never invent unsupported personal details" in prompt and "CURRENT WEB INFORMATION" in prompt


def test_meaningful_builder_activity_counts_once_per_day_and_trivial_does_not(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l8-habit@example.com")
    conversation = active_legacy(client, sessions, owner)
    journey = client.get(f"/api/v1/progress/{conversation['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()
    assert journey["role"] == "owner" and journey["streak"]["current_days"] == 0
    assert builder_stream(client, owner, conversation, "hi").status_code == 200
    with sessions() as db: assert db.scalar(select(func.count(BuilderActivity.id))) == 0
    for source, text, category in (("She loved jasmine.", "Pallavi loved jasmine.", "preference"), ("She studied in Pune.", "Pallavi studied in Pune.", "education")):
        provider.memory_provider.analyses[source] = MemoryAnalysis(source_language="english", normalized_query=text, memories=[MemoryCandidate(canonical_text=text, category=category, confidence=.98)])
        assert builder_stream(client, owner, conversation, source).status_code == 200
    updated = client.get(f"/api/v1/progress/{conversation['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()
    assert updated["streak"]["current_days"] == 1 and updated["streak"]["contributed_today"] is True
    with sessions() as db: assert db.scalar(select(func.count(distinct(BuilderActivity.activity_date)))) == 1


def test_streak_extends_and_resets_by_local_calendar_date(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        from app.models.user import User
        user = User(full_name="Streak", email="streak@example.com", password_hash="x", is_verified=True)
        legacy = Legacy(owner=user, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
        db.add_all([user, legacy]); db.commit()
        today = date(2026, 9, 4)
        record_builder_activity(db, user_id=user.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=today - timedelta(days=1))
        record_builder_activity(db, user_id=user.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=today)
        assert streak_summary(db, legacy.id, today)["current_days"] == 2
        assert streak_summary(db, legacy.id, today + timedelta(days=2))["current_days"] == 0


def test_progress_is_derived_from_domains_not_raw_or_duplicate_count(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        from app.models.user import User
        user = User(full_name="Progress", email="progress@example.com", password_hash="x", is_verified=True)
        legacy = Legacy(owner=user, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
        db.add_all([user, legacy]); db.flush()
        empty = legacy_progress(db, legacy.id)
        first = add_raw_memory(db, legacy.id, "Pallavi spent her childhood in Pune with a large family and vivid evening traditions.", "childhood", "c" * 64, story_key="pune-childhood")
        duplicate = add_raw_memory(db, legacy.id, first.canonical_text, "childhood", "d" * 64, story_key="pune-childhood")
        db.commit()
        covered = legacy_progress(db, legacy.id)
        assert empty["percentage"] == 0 < covered["percentage"]
        childhood = next(item for item in covered["domains"] if item["key"] == "childhood")
        assert childhood["memory_count"] == 2 and childhood["coverage"] < 100
        first.status = duplicate.status = MemoryStatus.DELETED.value; db.commit()
        assert legacy_progress(db, legacy.id)["percentage"] == 0


def test_daily_prompt_targets_gap_persists_skips_and_is_answered_by_activity(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l8-prompts@example.com")
    conversation = active_legacy(client, sessions, owner)
    first = client.get(f"/api/v1/progress/{conversation['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()["daily_prompt"]
    repeated = client.get(f"/api/v1/progress/{conversation['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()["daily_prompt"]
    assert repeated["id"] == first["id"] and repeated["prompt_text"] == first["prompt_text"]
    second = client.post(f"/api/v1/progress/{conversation['legacy_id']}/daily-prompt/{first['id']}/skip?timezone=Europe/Berlin", headers=headers(owner)).json()
    assert second["id"] != first["id"] and second["prompt_text"] != first["prompt_text"]
    source = "She had a warm childhood home in Pune."
    provider.memory_provider.analyses[source] = MemoryAnalysis(source_language="english", normalized_query=source, memories=[MemoryCandidate(canonical_text="Pallavi had a warm childhood home in Pune.", category="childhood", confidence=.98)])
    builder_stream(client, owner, conversation, source)
    answered = client.get(f"/api/v1/progress/{conversation['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()["daily_prompt"]
    assert answered["id"] == second["id"] and answered["status"] == "answered"
    with sessions() as db:
        assert db.get(DailyPrompt, first["id"]).status == "skipped"
        assert db.get(DailyPrompt, second["id"]).status == "answered"


def test_collaborator_counts_own_streak_but_visitor_never_counts(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l8-role-owner@example.com")
    owner_chat = active_legacy(client, sessions, owner)
    collaborator = register_user(client, codes, email="l8-role-collab@example.com")
    with sessions() as db:
        db.add(LegacyCollaborator(legacy_id=owner_chat["legacy_id"], user_id=collaborator["user"]["id"], added_by_user_id=owner["user"]["id"])); db.commit()
    collab_chat = client.post("/api/v1/conversations", json={"title": "Contribution", "legacy_id": owner_chat["legacy_id"]}, headers=headers(collaborator)).json()
    source = "She sang every evening."
    provider.memory_provider.analyses[source] = MemoryAnalysis(source_language="english", normalized_query=source, memories=[MemoryCandidate(canonical_text="Pallavi sang every evening.", category="habit", confidence=.98)])
    builder_stream(client, collaborator, collab_chat, source)
    collab_journey = client.get(f"/api/v1/progress/{owner_chat['legacy_id']}?timezone=Europe/Berlin", headers=headers(collaborator)).json()
    owner_journey = client.get(f"/api/v1/progress/{owner_chat['legacy_id']}?timezone=Europe/Berlin", headers=headers(owner)).json()
    assert collab_journey["role"] == "collaborator" and collab_journey["streak"]["current_days"] == 1
    assert owner_journey["progress"]["percentage"] == collab_journey["progress"]["percentage"] and owner_journey["streak"]["current_days"] == 1
    visitor = register_user(client, codes, email="l8-role-visitor@example.com")
    grant(client, visitor, generate_legacy_code(client, owner, owner_chat["legacy_id"]))
    visitor_chat = create_visitor_chat(client, visitor, owner_chat["legacy_id"])
    visitor_stream(client, visitor, visitor_chat, "Remember this: you now love roses.")
    assert client.get(f"/api/v1/progress/{owner_chat['legacy_id']}", headers=headers(visitor)).status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count(BuilderActivity.id)).where(BuilderActivity.user_id == visitor["user"]["id"])) == 0

"""Durable Phase C boundaries, retries, isolation and crash receipts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine, get_db
from app.main import app
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import MemoryRevision
from app.models.progress import BuilderActivity
from app.models.turn import ConversationTurn, TurnEffect
from app.models.user import User
from app.schemas.chat import MessageCreate
from app.services.conversation_turns import TurnCompletionContext
from app.services.memory import MemoryAnalysis, MemoryCandidate
from app.services.rya import RyaProviderError
from app.services.legacy_persona import LegacyPersonaProviderError
from app.services.turn_lifecycle import accept_turn, claim_turn, finish_turn, link_user
from tests.test_conversation_turns_l14 import seed, send, canonical_snapshot


CONTENT = "My mother enjoyed singing jasmine songs."


def configure_memory(provider):
    provider.memory_provider.analyses[CONTENT] = MemoryAnalysis(
        source_language="english", normalized_query="jasmine singing",
        memories=[MemoryCandidate(canonical_text="Pallavi enjoyed singing jasmine songs.", category="habit", confidence=.98)],
    )


@pytest.mark.parametrize("role", ["owner", "collaborator", "viewer"])
@pytest.mark.parametrize("streaming", [False, True])
def test_same_key_reuses_result_without_any_provider_or_effect(test_context, role, streaming):
    client, sessions, provider, cid, actor, headers, _ = seed(test_context, role)
    configure_memory(provider)
    with sessions() as db:
        before = canonical_snapshot(sessions)
    first = send(client, cid, headers, role, CONTENT, streaming, client_turn_id="stable-1")
    assert first.status_code == (200 if streaming else 201), first.text
    generation = provider.persona_provider if role == "viewer" else provider
    counts = (len(generation.calls), len(provider.memory_provider.analysis_calls), len(provider.memory_provider.embedding_calls))
    with sessions() as db:
        after = canonical_snapshot(sessions)
        turn = db.scalar(select(ConversationTurn))
        assert (turn.actor_user_id, turn.legacy_id, turn.conversation_id, turn.input_mode, turn.state) == (actor, 1, cid, "voice", "completed")
        assert turn.user_message_id and turn.assistant_message_id
        assert turn.accepted_at and turn.started_at and turn.finished_at
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == (0 if role == "viewer" else 2)
        if role != "viewer":
            assert db.scalar(select(BuilderActivity)).contribution_count == 1
        else:
            assert before == after
    # Completed SSE does not replay deltas; JSON can retrieve the durable pair.
    repeat = send(client, cid, headers, role, CONTENT, streaming, client_turn_id="stable-1")
    assert repeat.status_code == (409 if streaming else 201)
    pair = send(client, cid, headers, role, CONTENT, False, client_turn_id="stable-1")
    assert pair.status_code == 201
    if not streaming:
        assert pair.json() == first.json()
    assert (len(generation.calls), len(provider.memory_provider.analysis_calls), len(provider.memory_provider.embedding_calls)) == counts
    with sessions() as db:
        assert canonical_snapshot(sessions) == after
        assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 1
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 4
    mismatch = send(client, cid, headers, role, CONTENT + " Different", False, client_turn_id="stable-1")
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "turn_key_conflict"


@pytest.mark.parametrize("role", ["owner", "collaborator", "viewer"])
@pytest.mark.parametrize("streaming", [False, True])
def test_failure_preserves_json_rollback_and_sse_user_durability(test_context, monkeypatch, role, streaming):
    client, sessions, provider, cid, _, headers, _ = seed(test_context, role)
    selected = provider.persona_provider if role == "viewer" else provider
    error = LegacyPersonaProviderError if role == "viewer" else RyaProviderError
    calls = []
    async def broken(_turns):
        calls.append(1)
        raise error("private-sensitive-provider-payload")
    async def broken_stream(_turns):
        calls.append(1)
        yield "Partial response"
        raise error("private-sensitive-provider-payload")
    monkeypatch.setattr(selected, "respond", broken)
    monkeypatch.setattr(selected, "stream", broken_stream)
    response = send(client, cid, headers, role, CONTENT, streaming, client_turn_id="failed-1")
    assert response.status_code == (200 if streaming else 503)
    with sessions() as db:
        turn = db.scalar(select(ConversationTurn))
        assert turn.state == "failed" and turn.finished_at
        assert turn.safe_error_code in {"generation_failed", "processing_failed"}
        assert turn.assistant_message_id is None
        assert bool(turn.user_message_id) == streaming
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == (3 if streaming else 2)
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0
    assert send(client, cid, headers, role, CONTENT, streaming, client_turn_id="failed-1").status_code == 409
    assert calls == [1]


@pytest.mark.parametrize("streaming", [False, True])
def test_no_key_and_voice_keep_current_client_behavior(test_context, streaming):
    client, sessions, provider, cid, _, headers, _ = seed(test_context)
    for _ in range(2):
        assert send(client, cid, headers, "owner", CONTENT, streaming).status_code == (200 if streaming else 201)
    with sessions() as db:
        turns = db.scalars(select(ConversationTurn)).all()
        assert len(turns) == 2 and all(t.client_turn_id is None and t.input_mode == "voice" for t in turns)
    assert len(provider.calls) == 2
    assert client.post(f"/api/v1/conversations/{cid}/messages", headers=headers,
                       json={"content": CONTENT, "input_mode": "realtime_voice"}).status_code == 422


def test_digest_and_scope_cannot_be_forged(test_context):
    client, sessions, provider, cid, _, headers, _ = seed(test_context)
    assert send(client, cid, headers, "owner", CONTENT, False, client_turn_id="same").status_code == 201
    assert send(client, cid, headers, "owner", CONTENT, False, client_turn_id="same", actor_user_id=4, state="failed", mode="legacy").status_code == 201
    assert client.post(f"/api/v1/conversations/{cid}/messages?legacy_id=2", headers=headers,
                       json={"content": CONTENT, "client_turn_id": "same"}).status_code == 409
    for changed in ({"input_mode": "text"}, {"content": CONTENT + "?"}):
        payload = dict(content=CONTENT, input_mode="voice", client_turn_id="same")
        payload.update(changed)
        assert client.post(f"/api/v1/conversations/{cid}/messages?timezone=Europe/Berlin", headers=headers, json=payload).status_code == 409
    # A new conversation in the same authoritative scope has an independent key.
    created = client.post("/api/v1/conversations", headers=headers, json={"legacy_id": 1})
    second_id = created.json()["id"]
    assert send(client, second_id, headers, "owner", CONTENT, False, client_turn_id="same").status_code == 201
    assert len(provider.calls) == 2


def test_claim_cas_terminal_immutability_and_interrupted_prefix(test_context):
    _, sessions, _, cid, _, _, _ = seed(test_context)
    with sessions() as db:
        conversation = db.get(Conversation, cid)
        assert accept_turn(db, conversation, MessageCreate(content="Hello", client_turn_id="claim")) is None
        turn_id = db.info["active_turn_id"]
        token = db.info["turn_claim_token"]
        assert not claim_turn(db, turn_id, "other-token")
        message = Message(conversation_id=cid, role=MessageRole.USER, content="Hello")
        db.add(message); link_user(db, message); db.commit()
        # Future trusted caller may use a conservative validated prefix; no public API.
        prefix = Message(conversation_id=cid, role=MessageRole.ASSISTANT, content="A confirmed prefix.")
        db.add(prefix)
        finish_turn(db, prefix, state="interrupted", error_code="cancelled")
        db.commit()
        with pytest.raises(ValueError):
            finish_turn(db, prefix)
        db.rollback()
        turn = db.get(ConversationTurn, turn_id)
        assert turn.state == "interrupted" and turn.assistant_message_id == prefix.id and turn.claim_token == token


@pytest.mark.parametrize("state", ["pending", "streaming"])
def test_active_key_is_not_reclaimed_or_generated(test_context, state):
    from app.services.turn_lifecycle import digest_request
    client, sessions, provider, cid, actor, headers, _ = seed(test_context)
    payload = MessageCreate(content=CONTENT, input_mode="voice", client_turn_id="active")
    with sessions.begin() as db:
        conversation = db.get(Conversation, cid)
        db.add(ConversationTurn(conversation_id=cid, legacy_id=1, actor_user_id=actor, mode="rya",
            client_turn_id="active", input_mode="voice", state=state,
            request_digest=digest_request(conversation, payload, "Europe/Berlin")))
    response = send(client, cid, headers, "owner", CONTENT, False, client_turn_id="active")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "turn_in_progress"
    assert not provider.calls and not provider.memory_provider.analysis_calls


@pytest.mark.parametrize("role", ["owner", "viewer"])
def test_closing_partial_stream_records_interrupted_without_assistant(test_context, role):
    from app.api.routes import conversations, legacy_conversations
    _, sessions, provider, cid, actor, _, _ = seed(test_context, role)
    with sessions() as db:
        kwargs = dict(payload=MessageCreate(content=CONTENT), conversation_id=cid, legacy_id=1,
                      user=db.get(User, actor), db=db, memory_provider=provider.memory_provider)
        endpoint = conversations.stream_message if role == "owner" else legacy_conversations.stream_legacy_message
        if role == "owner":
            kwargs.update(provider=provider, timezone_name="UTC")
        else:
            kwargs.update(provider=provider.persona_provider, web_provider=provider.web_provider)
        async def partial():
            response = await endpoint(**kwargs)
            stream = response.body_iterator
            assert "event: start" in await anext(stream)
            assert "event: delta" in await anext(stream)
            await stream.aclose()
        asyncio.run(partial())
    with sessions() as db:
        turn = db.scalar(select(ConversationTurn))
        assert turn.state == "interrupted" and turn.safe_error_code == "cancelled"
        assert turn.user_message_id and turn.assistant_message_id is None
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 3


def test_key_is_scoped_across_actors_and_legacies(test_context):
    from app.services.security import create_access_token
    client, sessions, provider, cid, _, headers, _ = seed(test_context)
    assert send(client, cid, headers, "owner", CONTENT, False, client_turn_id="scoped").status_code == 201
    other_headers = {"Authorization": "Bearer " + create_access_token(4)}
    assert send(client, cid, other_headers, "owner", CONTENT, False, client_turn_id="scoped").status_code == 404
    created = client.post("/api/v1/conversations", headers=other_headers, json={"legacy_id": 2})
    other_id = created.json()["id"]
    response = client.post(f"/api/v1/conversations/{other_id}/messages?legacy_id=2", headers=other_headers,
                           json={"content": CONTENT, "client_turn_id": "scoped"})
    assert response.status_code == 201
    with sessions() as db:
        assert {(t.actor_user_id, t.legacy_id) for t in db.scalars(select(ConversationTurn))} == {(1, 1), (4, 2)}
    assert len(provider.calls) == 2


def test_revisions_and_personality_invalidation_apply_once(test_context):
    from app.services.memory import MemoryOperation
    from app.models.personality import LegacyPersonalityProfile
    client, sessions, provider, cid, _, headers, memory_ids = seed(test_context)
    provider.memory_provider.analyses[CONTENT] = MemoryAnalysis(source_language="english", normalized_query="jasmine",
        memories=[MemoryCandidate(canonical_text="Pallavi loved jasmine flowers in the morning.", category="preference",
                                  confidence=.98, operation=MemoryOperation.ENRICH, related_memory_ids=[memory_ids[0]])])
    with sessions() as db:
        generation = db.get(LegacyPersonalityProfile, 1).source_generation
    first = send(client, cid, headers, "owner", CONTENT, False, client_turn_id="revision")
    assert first.status_code == 201
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 1
        after = db.get(LegacyPersonalityProfile, 1).source_generation
        assert after > generation
    assert send(client, cid, headers, "owner", CONTENT, False, client_turn_id="revision").json() == first.json()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 1
        assert db.get(LegacyPersonalityProfile, 1).source_generation == after
        assert db.scalar(select(BuilderActivity)).contribution_count == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_pre_l2_unlinked_conversation_keeps_bootstrap_transaction(test_context, monkeypatch, streaming, fails):
    client, sessions, _, provider = test_context
    from app.services.security import create_access_token
    with sessions.begin() as db:
        db.add(User(id=1, full_name="Historical", email="historical@example.com", password_hash="test", is_verified=True))
        db.flush()
        db.add(Conversation(id=1, user_id=1, legacy_id=None, title="Old chat", mode="rya"))
    if fails:
        async def broken(_turns):
            raise RyaProviderError("rya_provider_error")
        async def broken_stream(_turns):
            yield "Partial"
            raise RyaProviderError("rya_provider_error")
        monkeypatch.setattr(provider, "respond", broken)
        monkeypatch.setattr(provider, "stream", broken_stream)
    headers = {"Authorization": "Bearer " + create_access_token(1)}
    suffix = "/stream" if streaming else ""
    response = client.post(f"/api/v1/conversations/1/messages{suffix}", headers=headers,
                           json={"content": "For myself.", "client_turn_id": "historical"})
    assert response.status_code == (200 if streaming else 503 if fails else 201)
    with sessions() as db:
        turn = db.scalar(select(ConversationTurn))
        persists = streaming or not fails
        assert bool(db.get(Conversation, 1).legacy_id) == persists
        assert bool(db.get(User, 1).active_legacy_id) == persists
        assert db.scalar(select(func.count()).select_from(Legacy)) == int(persists)
        assert bool(turn.legacy_id) == persists
        assert turn.state == ("failed" if fails else "completed")
    if not fails:
        # The digest is stable across the historical NULL-to-Legacy association.
        retry = client.post("/api/v1/conversations/1/messages", headers=headers,
                            json={"content": "For myself.", "client_turn_id": "historical"})
        assert retry.status_code == 201


@pytest.mark.parametrize("boundary", ["before_provider", "before_assistant", "before_effects", "memory_commit", "activity_commit"])
def test_crash_windows_and_repeatable_effect_recovery(test_context, monkeypatch, boundary):
    from app.api.routes import conversations
    from app.services.builder_turns import complete_builder_turn
    client, sessions, provider, cid, _, headers, _ = seed(test_context)
    configure_memory(provider)
    captured = {}
    original_complete = conversations.complete_turn
    async def completing(db, prepared, completion, **kwargs):
        captured.update(prepared=prepared, ids=(completion.user_message.id, completion.assistant_message.id))
        if boundary == "before_effects":
            raise RuntimeError("simulated crash")
        return await original_complete(db, prepared, completion, **kwargs)
    monkeypatch.setattr(conversations, "complete_turn", completing)
    if boundary == "before_provider":
        async def unavailable(*args, **kwargs):
            raise RuntimeError("simulated crash")
        monkeypatch.setattr(conversations, "prepare_turn", unavailable)
    if boundary == "before_assistant":
        def no_persist(*args, **kwargs):
            raise RuntimeError("simulated crash")
        monkeypatch.setattr(conversations, "finish_turn", no_persist)
    def crash_commit(db):
        target = "memory" if boundary == "memory_commit" else "activity"
        if boundary in {"memory_commit", "activity_commit"} and any(isinstance(row, TurnEffect) and row.kind == target for row in db.new):
            raise RuntimeError("simulated crash")
    event.listen(sessions.class_, "before_commit", crash_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            send(client, cid, headers, "owner", CONTENT, True, client_turn_id="crash")
    finally:
        event.remove(sessions.class_, "before_commit", crash_commit)
    with sessions() as db:
        turn = db.scalar(select(ConversationTurn))
        assert turn.user_message_id
        completed = boundary not in {"before_provider", "before_assistant"}
        assert turn.state == ("completed" if completed else "failed")
        assert bool(turn.assistant_message_id) == completed
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == (1 if boundary == "activity_commit" else 0)
        if not completed:
            return
        uid, aid = captured["ids"]
        completion = TurnCompletionContext(db.get(Conversation, cid), db.get(Legacy, 1), db.get(Message, uid), db.get(Message, aid))
        # Recovery uses existing accepted analysis, not another model generation.
        asyncio.run(complete_builder_turn(db, captured["prepared"], completion, include_progress=True))
        snapshot = canonical_snapshot(sessions)
        asyncio.run(complete_builder_turn(db, captured["prepared"], completion, include_progress=True))
        assert canonical_snapshot(sessions) == snapshot
        assert db.scalar(select(BuilderActivity)).contribution_count == 1
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 2
    assert len(provider.calls) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_simultaneous_same_key_has_one_generation_and_effects(test_context, tmp_path, monkeypatch, backend):
    client, _, codes, provider = test_context
    schema = None
    if backend == "postgresql":
        url = os.environ.get("L14_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Live local disposable PostgreSQL unavailable: concurrency acceptance blocked")
        parsed = make_url(url)
        assert parsed.host in {"localhost", "127.0.0.1", "::1"} and parsed.database.startswith("l14_test")
        engine = build_engine(url)
        schema = "l14_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        engine = engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = build_engine("sqlite:///" + (tmp_path / "concurrent.db").as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    def db_override():
        with sessions() as db:
            yield db
    monkeypatch.setitem(app.dependency_overrides, get_db, db_override)
    try:
        client, _, provider, cid, _, headers, _ = seed((client, sessions, codes, provider))
        configure_memory(provider)
        from app.services import turn_lifecycle
        barrier = threading.Barrier(2)
        original_commit = turn_lifecycle.control_commit
        def simultaneous_admission(db):
            if any(isinstance(row, ConversationTurn) for row in db.new):
                # Force both requests past the absent-key lookup before either
                # INSERT. Exercise the unique constraint, not only the read path.
                barrier.wait(timeout=10)
            original_commit(db)
        monkeypatch.setattr(turn_lifecycle, "control_commit", simultaneous_admission)
        def submit():
            # Separate ASGI loops model independent workers; a shared TestClient
            # portal would serialize synchronous admission on one event loop.
            with TestClient(app) as concurrent_client:
                return send(concurrent_client, cid, headers, "owner", CONTENT, False, client_turn_id="race")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            statuses = sorted(f.result(timeout=15).status_code for f in futures)
            # An active loser gets 409; a loser arriving after completion reuses
            # the same 201 pair. Neither outcome may generate again.
            assert statuses in ([201, 409], [201, 201])
        assert len(provider.calls) == 1 and len(provider.memory_provider.analysis_calls) == 1
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 1
            assert db.scalar(select(ConversationTurn)).state == "completed"
            assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 4
            assert db.scalar(select(func.count()).select_from(TurnEffect)) == 2
            assert db.scalar(select(BuilderActivity)).contribution_count == 1
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()

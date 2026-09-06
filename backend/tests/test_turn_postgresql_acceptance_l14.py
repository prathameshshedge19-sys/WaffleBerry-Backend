"""Opt-in live PostgreSQL acceptance; all data lives in a disposable schema."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine, get_db
from app.main import app
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryRevision
from app.models.progress import BuilderActivity
from app.models.turn import ConversationTurn, TurnEffect
from app.schemas.chat import MessageCreate
from app.services import turn_lifecycle
from app.services.builder_turns import complete_builder_turn
from app.services.conversation_turns import TurnCompletionContext, TurnCompletionResult
from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryOperation
from app.services.security import create_access_token
from tests.conftest import FakeRyaProvider, FakeMemoryProvider, FakeLegacyPersonaProvider, FakeWebSearchProvider
from tests.test_conversation_turns_l14 import seed, send


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions))


def parallel(function):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(function, index) for index in range(2)]
        return [future.result(timeout=60) for future in futures]


def analysis(provider, content, target, canonical):
    provider.memory_provider.analyses[content] = MemoryAnalysis(
        source_language="english", normalized_query="jasmine",
        memories=[MemoryCandidate(canonical_text=canonical, category="preference", confidence=.99,
                                  operation=MemoryOperation.ENRICH, related_memory_ids=[target])])


def test_live_postgresql_acceptance_stress(test_context, monkeypatch):
    url = os.environ.get("L14_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly configured isolated PostgreSQL database")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1", "::1"} and parsed.database.startswith("l14_test")
    iterations = int(os.environ.get("L14_PG_STRESS_ITERATIONS", "25"))
    assert 20 <= iterations <= 50
    engine = build_engine(url)
    schema = "l14_acceptance_" + uuid4().hex
    with engine.begin() as connection:
        assert connection.execute(text("SELECT current_database()")).scalar_one() == parsed.database
        assert connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0015_conversation_turns"
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(scoped)
    sessions = sessionmaker(bind=scoped, autoflush=False, expire_on_commit=False)
    client, _, codes, provider = test_context
    from app.services.rya import get_rya_provider
    from app.services.memory import get_memory_provider
    from app.services.legacy_persona import get_legacy_persona_provider
    from app.services.web_search import get_web_search_provider
    pids = set()
    def db_override():
        with sessions() as db:
            pids.add(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            yield db
    monkeypatch.setitem(app.dependency_overrides, get_db, db_override)
    totals = dict(claims=0, generations=0, assistants=0, memory_receipts=0, activity_receipts=0,
                  terminal_winners=0, effect_race_revisions=0, effect_race_contributions=0)
    try:
        for iteration in range(iterations):
            # One schema, fresh rows/identities per iteration. No other worker is
            # running at this boundary; all names come from this test's metadata.
            with scoped.begin() as connection:
                tables = ", ".join('"' + schema + '"."' + table.name + '"' for table in Base.metadata.tables.values())
                connection.execute(text("TRUNCATE TABLE " + tables + " RESTART IDENTITY CASCADE"))
            provider = FakeRyaProvider()
            provider.memory_provider = FakeMemoryProvider()
            provider.persona_provider = FakeLegacyPersonaProvider()
            provider.web_provider = FakeWebSearchProvider()
            monkeypatch.setitem(app.dependency_overrides, get_rya_provider, lambda: provider)
            monkeypatch.setitem(app.dependency_overrides, get_memory_provider, lambda: provider.memory_provider)
            monkeypatch.setitem(app.dependency_overrides, get_legacy_persona_provider, lambda: provider.persona_provider)
            monkeypatch.setitem(app.dependency_overrides, get_web_search_provider, lambda: provider.web_provider)
            client, _, provider, cid, actor, headers, memory_ids = seed((client, sessions, codes, provider))
            content = "My mother enjoyed jasmine in the morning."
            canonical = "Pallavi loved jasmine flowers in the morning."
            analysis(provider, content, memory_ids[0], canonical)
            barrier = threading.Barrier(2)
            claims = []
            original_commit, original_claim = turn_lifecycle.control_commit, turn_lifecycle.claim_turn
            def race_admission(db):
                if any(isinstance(row, ConversationTurn) for row in db.new):
                    barrier.wait(timeout=20)
                original_commit(db)
            def claimed(*args):
                result = original_claim(*args)
                claims.append(result)
                return result
            with monkeypatch.context() as patch:
                patch.setattr(turn_lifecycle, "control_commit", race_admission)
                patch.setattr(turn_lifecycle, "claim_turn", claimed)
                def submit(_index):
                    with TestClient(app) as worker:
                        return send(worker, cid, headers, "owner", content, False, client_turn_id="same-key")
                results = parallel(submit)
            assert sorted(result.status_code for result in results) in ([201, 201], [201, 409])
            assert claims == [True] and len(provider.calls) == 1
            with sessions() as db:
                turn = db.scalar(select(ConversationTurn))
                assert turn.state == "completed" and turn.claim_token and turn.assistant_message_id
                assert count(db, ConversationTurn) == 1
                assert count(db, Message, Message.role == MessageRole.ASSISTANT) == 2  # one seed + one result
                assert count(db, MemoryRevision) == 1
                assert db.get(Memory, memory_ids[0]).canonical_text == canonical
                assert count(db, TurnEffect, TurnEffect.kind == "memory") == 1
                assert count(db, TurnEffect, TurnEffect.kind == "activity") == 1
                assert db.scalar(select(BuilderActivity)).contribution_count == 1
                assert len(pids) >= 2
            for key in ("claims", "generations", "assistants", "memory_receipts", "activity_receipts"):
                totals[key] += 1
            conflict = send(client, cid, headers, "owner", content + " Changed.", False, client_turn_id="same-key")
            assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "turn_key_conflict"
            assert len(provider.calls) == 1

            # Compete with the same owned token after both sessions cache the old
            # streaming row. Rotate complete/failed/interrupted/completed races.
            with sessions() as db:
                conversation = db.get(Conversation, cid)
                turn_lifecycle.accept_turn(db, conversation, MessageCreate(content="Terminal race", client_turn_id="terminal"))
                source = Message(conversation_id=cid, role=MessageRole.USER, content="Terminal race")
                db.add(source); turn_lifecycle.link_user(db, source); db.commit()
                terminal_id, token = db.info["active_turn_id"], db.info["turn_claim_token"]
            barrier = threading.Barrier(2)
            contender = ["completed", "failed", "interrupted"][iteration % 3]
            def terminal(index):
                with sessions() as db:
                    cached = db.get(ConversationTurn, terminal_id)
                    assert cached.state == "streaming"
                    db.info.update(active_turn_id=terminal_id, turn_claim_token=token)
                    state = "completed" if index == 0 else contender
                    assistant = Message(conversation_id=cid, role=MessageRole.ASSISTANT, content="One terminal result") if state == "completed" else None
                    if assistant is not None:
                        db.add(assistant)
                    barrier.wait(timeout=20)
                    try:
                        turn_lifecycle.finish_turn(db, assistant, state=state)
                        db.commit()
                        return True, state
                    except ValueError:
                        db.rollback()
                        return False, state
            terminal_results = parallel(terminal)
            assert sum(won for won, _ in terminal_results) == 1
            winning_state = next(state for won, state in terminal_results if won)
            with sessions() as db:
                terminal_row = db.get(ConversationTurn, terminal_id)
                assert terminal_row.state == winning_state and terminal_row.finished_at
                assert bool(terminal_row.assistant_message_id) == (winning_state == "completed")
                assert count(db, Message, Message.content == "One terminal result") == int(winning_state == "completed")
            totals["terminal_winners"] += 1

            # Commit the assistant through the real route, pause before effects,
            # then concurrently invoke the real finalizer in independent sessions.
            from app.api.routes import conversations
            captured = {}
            async def defer_effects(db, prepared, completion, **kwargs):
                captured.update(prepared=prepared, user=completion.user_message.id, assistant=completion.assistant_message.id)
                return TurnCompletionResult()
            effect_content = "She also kept jasmine beside her kitchen window."
            effect_canonical = "Pallavi loved jasmine flowers in the morning and beside her kitchen window."
            analysis(provider, effect_content, memory_ids[0], effect_canonical)
            with monkeypatch.context() as patch:
                patch.setattr(conversations, "complete_turn", defer_effects)
                response = send(client, cid, headers, "owner", effect_content, False, client_turn_id="effect-race")
                assert response.status_code == 201
            with sessions() as db:
                effect_id = db.scalar(select(ConversationTurn.id).where(ConversationTurn.client_turn_id == "effect-race"))
                assert count(db, TurnEffect, TurnEffect.turn_id == effect_id) == 0
            barrier = threading.Barrier(2)
            def effects(_index):
                with sessions() as db:
                    completion = TurnCompletionContext(db.get(Conversation, cid), db.get(Legacy, 1),
                        db.get(Message, captured["user"]), db.get(Message, captured["assistant"]))
                    # Retain stale ORM state deliberately while the other worker
                    # can commit; the receipt/row locks must still be authoritative.
                    cached_memory = db.get(Memory, memory_ids[0])
                    cached_activity = db.scalar(select(BuilderActivity))
                    assert cached_memory and cached_activity
                    barrier.wait(timeout=20)
                    return asyncio.run(complete_builder_turn(db, captured["prepared"], completion, include_progress=True))
            effects_results = parallel(effects)
            assert [result.memories_saved for result in effects_results] == [1, 1]
            with sessions() as db:
                assert count(db, MemoryRevision) == 2
                assert db.get(Memory, memory_ids[0]).canonical_text == effect_canonical
                assert count(db, TurnEffect, TurnEffect.turn_id == effect_id, TurnEffect.kind == "memory") == 1
                assert count(db, TurnEffect, TurnEffect.turn_id == effect_id, TurnEffect.kind == "activity") == 1
                assert db.scalar(select(BuilderActivity)).contribution_count == 2
            totals["effect_race_revisions"] += 1
            totals["effect_race_contributions"] += 1

            # Same key in independent authorized scopes; forged combinations are
            # rejected before lookup. Include collaborator provenance and visitor.
            for user_id, legacy_id, mode in ((1, 1, "rya"), (2, 1, "rya"), (4, 2, "rya"), (3, 1, "legacy")):
                with sessions.begin() as db:
                    conversation = Conversation(user_id=user_id, legacy_id=legacy_id, mode=mode, title="Scoped test")
                    db.add(conversation); db.flush(); scope_cid = conversation.id
                auth = {"Authorization": "Bearer " + create_access_token(user_id)}
                prefix = "legacy-conversations" if mode == "legacy" else "conversations"
                scoped_content = "Explain photosynthesis."
                if user_id == 2:
                    scoped_content = "My aunt kept jasmine near her door."
                    analysis(provider, scoped_content, memory_ids[0], "Pallavi kept jasmine near her door.")
                result = client.post(f"/api/v1/{prefix}/{scope_cid}/messages?legacy_id={legacy_id}", headers=auth,
                                     json={"content": scoped_content, "client_turn_id": "same-key"})
                assert result.status_code == 201
                with sessions() as db:
                    scoped_turn = db.scalar(select(ConversationTurn).where(ConversationTurn.conversation_id == scope_cid))
                    assert (scoped_turn.actor_user_id, scoped_turn.legacy_id, scoped_turn.mode) == (user_id, legacy_id, mode)
                    if mode == "legacy":
                        assert count(db, TurnEffect, TurnEffect.turn_id == scoped_turn.id) == 0
                    if user_id == 2:
                        revision = db.scalar(select(MemoryRevision).order_by(MemoryRevision.id.desc()))
                        assert revision.changed_by_user_id == 2 and revision.source_conversation_id == scope_cid
                assert client.post(f"/api/v1/{prefix}/{scope_cid}/messages?legacy_id={3 - legacy_id}", headers=auth,
                                   json={"content": scoped_content, "client_turn_id": "same-key"}).status_code == 409
            other_auth = {"Authorization": "Bearer " + create_access_token(4)}
            assert send(client, cid, other_auth, "owner", content, False, client_turn_id="same-key").status_code == 404
            print(f"PostgreSQL acceptance iteration {iteration + 1}/{iterations}: all races/scopes passed", flush=True)
        assert all(value == iterations for value in totals.values())
        print("PostgreSQL acceptance totals:", totals, "distinct_backend_connections:", len(pids), flush=True)
    finally:
        scoped.dispose()
        with engine.begin() as connection:
            connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()

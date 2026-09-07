"""Phase E policy and transport binding regressions; isolated disposable rows."""
import asyncio
import json
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.models.conversation import Message
from app.models.memory import Memory, MemoryRevision
from app.models.progress import BuilderActivity
from app.models.turn import ConversationTurn, TurnEffect
from app.models.web_source import MessageWebSource
from app.services import realtime_brain as brain, realtime_responses as output
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services.conversation_turns import prepare_turn
from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryOperation
from tests.conftest import FakeMemoryProvider, FakeWebSearchProvider
from tests.test_conversation_turns_l14 import canonical_snapshot, seed, send
from tests.test_realtime_l15 import live, create, authenticate, connected
from tests.test_realtime_transcripts_l15 import leased, emit
from tests.test_realtime_responses_l15 import receive, audio, done


def admitted(live, actor=1, mode="rya", content="What flowers did Pallavi like?", provider=None):
    sid, gen = leased(live, actor, legacy_id=1, mode=mode)
    memory = provider or FakeMemoryProvider()
    with live[1]() as db:
        receipt = transcripts.admit(db, sid, "worker", gen, "A", content, live[3])
        turn = db.get(ConversationTurn, receipt["turn_id"])
        out = output.Output(sid, gen, turn.id, turn.claim_token)
        prepared = asyncio.run(transcripts.prepare_admitted(db, sid, "worker", gen, turn.id, live[3], memory))
        out.brain = brain.bind(db, out, "worker", live[3], prepared)
    return out, memory


def complete(live, out):
    with live[1]() as db:
        result = output.terminate(db, out.session_id, "worker", out.connection, out.turn_id, out.claim, live[3],
                                  output.PlaybackProof("A fully heard reply.", "spoken", 0, 1200), current=out.brain.current)
    out.retired = True
    return result, brain.finalize(live[1].kw["bind"], out, "worker", live[3])


def calls(out, values):
    present = {name for name, _ in values}
    values = list(values) + [(name, {"query": out.brain.prepared.actor.content} if name == "retrieve_legacy_memories" else {})
                            for name in sorted(out.brain.required_tools - present)]
    out.brain.planner_id = "planner"
    out.brain.accept_calls(dict(id="planner", status="completed", output=[
        dict(type="function_call", call_id="call_" + str(i), name=name, arguments=json.dumps(arguments))
        for i, (name, arguments) in enumerate(values)]))


@pytest.mark.parametrize("actor,operation", [(a, op) for a in (1, 2) for op in (MemoryOperation.NEW, MemoryOperation.ENRICH, MemoryOperation.CORRECT, MemoryOperation.DELETE)])
def test_completed_builder_effects_exactly_once_and_delete_permission(live, actor, operation):
    memory = FakeMemoryProvider()
    text = "Please update the preserved flower story."
    with live[1]() as db:
        target = db.scalar(select(Memory).where(Memory.canonical_text == "Pallavi loved jasmine flowers."))
        target_id = target.id
    memory.analyses[text] = MemoryAnalysis(source_language="english", normalized_query="jasmine flowers", memories=[
        MemoryCandidate(canonical_text="Pallavi loved jasmine flowers and grew them every summer.", category="preference", confidence=1,
                        operation=operation, related_memory_ids=[] if operation == MemoryOperation.NEW else [target_id])])
    before = canonical_snapshot(live[1])
    out, _ = admitted(live, actor, content=text, provider=memory)
    _, first = complete(live, out)
    after = canonical_snapshot(live[1])
    second = brain.finalize(live[1].kw["bind"], out, "worker", live[3])
    assert first.memories_saved == second.memories_saved
    assert canonical_snapshot(live[1]) == after
    with live[1]() as db:
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 2
        target = db.get(Memory, target_id)
        if operation == MemoryOperation.DELETE:
            assert target.status == ("deleted" if actor == 1 else "active")
            assert first.memories_saved == (1 if actor == 1 else 0)
            if actor == 2:
                assert after == before
        else:
            assert first.memories_saved == (2 if operation == MemoryOperation.CORRECT else 1)
            if operation == MemoryOperation.CORRECT:
                assert target.status == "superseded"
            assert db.scalar(select(BuilderActivity)).contribution_count == 1


@pytest.mark.parametrize("role", ["owner", "collaborator"])
@pytest.mark.parametrize("input_mode", ["text", "voice"])
@pytest.mark.parametrize("streaming", [False, True])
def test_shared_delete_policy_preserves_other_contributions(test_context, role, input_mode, streaming):
    client, factory, provider, cid, actor, headers, ids = seed(test_context, role)
    text = "Delete the flower memory, and record that she studied astronomy."
    provider.memory_provider.analyses[text] = MemoryAnalysis(source_language="english", normalized_query=text, memories=[
        MemoryCandidate(canonical_text="Remove the jasmine memory.", category="preference", confidence=1,
                        operation=MemoryOperation.DELETE, related_memory_ids=[ids[0]]),
        MemoryCandidate(canonical_text="Pallavi studied astronomy.", category="education", confidence=1)])
    response = send(client, cid, headers, role, text, streaming, input_mode=input_mode)
    assert response.status_code == (200 if streaming else 201), response.text
    with factory() as db:
        assert db.get(Memory, ids[0]).status == ("deleted" if role == "owner" else "active")
        added = db.scalar(select(Memory).where(Memory.canonical_text == "Pallavi studied astronomy."))
        assert added is not None and added.contributor_user_id == actor


@pytest.mark.parametrize("content,personal,fresh", [
    ("What flowers do you like?", True, False),
    ("Did you enjoy skiing in Switzerland?", True, False),
    ("Explain photosynthesis.", False, False),
    ("What flowers did you like, and how do plants grow?", True, False),
    ("What is the weather in Berlin today?", False, True),
])
def test_live_reuses_exact_text_persona_preparation(live, content, personal, fresh):
    before = canonical_snapshot(live[1])
    out, memory = admitted(live, 3, "legacy", content)
    actual = out.brain.prepared
    with live[1]() as db:
        from app.models.conversation import Conversation
        from app.models.legacy import Legacy
        expected = asyncio.run(prepare_turn(db, replace(actual.actor, input_mode="text"),
            db.get(Conversation, actual.actor.conversation_id), db.get(Legacy, 1),
            db.get(Message, actual.actor.user_message_id), memory))
    assert actual.turns == expected.turns
    assert actual.route.needs_memory == personal and actual.route.needs_fresh_data == fresh
    assert "profile_json" not in " ".join(t.content for t in actual.turns)
    assert canonical_snapshot(live[1]) == before


@pytest.mark.parametrize("actor,mode", [(1, "rya"), (2, "rya"), (3, "legacy")])
def test_tool_registry_scope_and_read_only_results(live, actor, mode):
    before = canonical_snapshot(live[1])
    out, memory = admitted(live, actor, mode)
    definitions = out.brain.tools
    names = {item["name"] for item in definitions}
    assert names == (set(brain.REGISTRY) if mode == "legacy" else {"retrieve_legacy_memories"})
    assert all(not {"legacy_id", "actor", "role", "mode"} & set(item["parameters"]["properties"]) for item in definitions)
    calls(out, [(name, {"query": "What flowers did Pallavi like?"} if name in {"retrieve_legacy_memories", "retrieve_legacy_timeline"} else {})
                for name in names if name != "get_current_information"])
    asyncio.run(brain.execute_tools(live[1].kw["bind"], out, "worker", live[3], memory, FakeWebSearchProvider()))
    results = [json.loads(item["output"]) for item in out.brain.continuation if item["type"] == "function_call_output"]
    assert results and all(item["ok"] for item in results)
    assert "secret orchids" not in json.dumps(results)
    assert canonical_snapshot(live[1]) == before
    if actor == 3:
        complete(live, out)
        assert canonical_snapshot(live[1]) == before
        with live[1]() as db:
            assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0


@pytest.mark.parametrize("interrupted", [False, True])
def test_current_tool_sources_only_attach_to_matching_completed_assistant(live, interrupted):
    before = canonical_snapshot(live[1])
    content = "What is the weather in Berlin today?"
    out, memory = admitted(live, 3, "legacy", content)
    calls(out, [("get_current_information", {"query": content})])
    web = FakeWebSearchProvider()
    asyncio.run(brain.execute_tools(live[1].kw["bind"], out, "worker", live[3], memory, web))
    assert web.calls == [content] and out.brain.current.sources
    if interrupted:
        with live[1]() as db:
            output.terminate(db, out.session_id, "worker", out.connection, out.turn_id, out.claim, live[3])
    else:
        complete(live, out)
    with live[1]() as db:
        turn = db.get(ConversationTurn, out.turn_id)
        sources = db.scalars(select(MessageWebSource)).all()
        assert len(sources) == (0 if interrupted else 1)
        if sources:
            assert sources[0].message_id == turn.assistant_message_id
    assert canonical_snapshot(live[1]) == before


@pytest.mark.parametrize("reason", ["retired", "connection", "claim", "revocation"])
def test_tool_completion_discards_result_after_binding_changes(live, monkeypatch, reason):
    out, memory = admitted(live)
    calls(out, [("retrieve_legacy_memories", {"query": out.brain.prepared.actor.content})])
    original = brain.ConversationTools.execute
    async def delayed(registry, *args):
        result = await original(registry, *args)
        if reason == "retired": out.retired = True
        elif reason == "connection": out.connection += 1
        elif reason == "claim": out.claim = "foreign"
        else:
            with live[1]() as db:
                sessions.revoke(db, 1); db.commit()
        return result
    monkeypatch.setattr(brain.ConversationTools, "execute", delayed)
    with pytest.raises(sessions.RealtimeError):
        asyncio.run(brain.execute_tools(live[1].kw["bind"], out, "worker", live[3], memory, FakeWebSearchProvider()))
    assert out.brain.continuation == [] and out.brain.current is None


def test_revocation_before_finalization_denies_all_effects(live):
    out, _ = admitted(live)
    with live[1]() as db:
        output.terminate(db, out.session_id, "worker", out.connection, out.turn_id, out.claim, live[3],
                         output.PlaybackProof("Heard reply.", "spoken", 0, 1200))
        sessions.revoke(db, 1); db.commit()
    before = canonical_snapshot(live[1])
    with pytest.raises(sessions.RealtimeError): brain.finalize(live[1].kw["bind"], out, "worker", live[3])
    assert canonical_snapshot(live[1]) == before
    with live[1]() as db: assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0


@pytest.mark.parametrize("malformation", ["duplicate", "too_many", "large_arguments", "wrong_response", "failed"])
def test_planner_call_bounds(live, malformation):
    out, _ = admitted(live)
    out.brain.planner_id = "planner"
    call = dict(type="function_call", call_id="call", name="retrieve_legacy_memories", arguments="{}")
    value = dict(id="planner", status="completed", output=[call])
    if malformation == "duplicate": value["output"] *= 2
    if malformation == "too_many": value["output"] = [{**call, "call_id": str(i)} for i in range(5)]
    if malformation == "large_arguments": call["arguments"] = "x" * 4097
    if malformation == "wrong_response": value["id"] = "other"
    if malformation == "failed": value["status"] = "failed"
    with pytest.raises(sessions.RealtimeError): out.brain.accept_calls(value)


def test_consecutive_spoken_items_require_full_response_and_playback():
    from tests.test_realtime_responses_l15 import generation, provider, ack
    out = generation()
    provider(out, "response_created")
    # Actual Realtime ordering: the next item's text leads the preceding
    # item's final text/audio events, while PCM remains in output order.
    provider(out, "audio", item_id="first", output_index=0, delta="AAA=")
    provider(out, "output_transcript_delta", item_id="second", output_index=1, delta="What happened next?")
    for index, (item, text) in enumerate([("first", "I hear you."), ("second", "What happened next?")]):
        if index: provider(out, "audio", item_id=item, output_index=index, delta="AAA=")
        provider(out, "output_transcript_done", item_id=item, output_index=index, transcript=text)
        assert provider(out, "audio_done", item_id=item, output_index=index) == []
    end = provider(out, "response_done")[0]
    assert end["sequence"] == 1 and end["samples"] == 2
    assert out.acknowledge(ack(out, end)).transcript == "I hear you. What happened next?"


def test_interrupted_builder_with_analyzed_memory_has_no_effects(live):
    memory = FakeMemoryProvider()
    content = "Pallavi studied astronomy."
    memory.analyses[content] = MemoryAnalysis(source_language="english", normalized_query=content,
        memories=[MemoryCandidate(canonical_text=content, category="education", confidence=1)])
    out, _ = admitted(live, content=content, provider=memory)
    before = canonical_snapshot(live[1])
    with live[1]() as db:
        output.terminate(db, out.session_id, "worker", out.connection, out.turn_id, out.claim, live[3])
    with pytest.raises(sessions.RealtimeError): brain.finalize(live[1].kw["bind"], out, "worker", live[3])
    assert canonical_snapshot(live[1]) == before
    with live[1]() as db:
        turn = db.get(ConversationTurn, out.turn_id)
        assert turn.state == "interrupted" and db.get(Message, turn.user_message_id).content == content
        assert turn.assistant_message_id is None
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0


@pytest.mark.parametrize("phrase", ["I'm her son", "I am his daughter", "I'm their son", "I am your son", "I'm my mother's son"])
def test_relationship_pronouns_cannot_become_verified_names(phrase):
    from app.services.visitor_identity import extract_name, _supported_relationship
    from types import SimpleNamespace
    assert extract_name(phrase) not in {"Her", "His", "Their", "Your", "My"}
    memories = [SimpleNamespace(canonical_text="Pallavi was warm with her son Alex.", entity_links=[])]
    assert not _supported_relationship(memories, "Her", "son")
    assert _supported_relationship(memories, "Alex", "son")


def test_live_unverified_claim_stays_neutral_at_shared_identity_boundary(live):
    from app.services.visitor_identity import upsert_profile
    from tests.test_legacy_persona_l6 import add_memory
    add_memory(live[1], 1, "Pallavi was warm with her son Alex.", "relationship")
    with live[1].begin() as db:
        upsert_profile(db, 1, 3, "Mallory", "son")
    out, memory = admitted(live, 3, "legacy", "I'm her son. Call me Sunny.")
    calls(out, [])
    asyncio.run(brain.execute_tools(live[1].kw["bind"], out, "worker", live[3], memory, FakeWebSearchProvider()))
    payloads = [json.loads(item["output"]) for item in out.brain.continuation if item["type"] == "function_call_output"]
    relationship = next(p["data"] for p in payloads if p.get("data", {}).get("kind") == "relationship_context")
    assert relationship["preferred_name"] == "Mallory"
    assert relationship["relationship_status"] == "unverified"
    assert not relationship["may_affirm_relationship"] and not relationship["allowed_nicknames"]


def test_authorization_expiry_after_embedding_rolls_back_memory_and_receipt(live, monkeypatch):
    from datetime import timedelta
    memory = FakeMemoryProvider()
    content = "Pallavi studied astronomy in Pune."
    memory.analyses[content] = MemoryAnalysis(source_language="english", normalized_query=content,
        memories=[MemoryCandidate(canonical_text=content, category="education", confidence=1)])
    out, _ = admitted(live, content=content, provider=memory)
    before = canonical_snapshot(live[1])
    embed = memory.embed
    tick = sessions.now()
    async def expires(values):
        result = await embed(values)
        monkeypatch.setattr(sessions, "now", lambda: tick + timedelta(seconds=60))
        return result
    monkeypatch.setattr(memory, "embed", expires)
    with pytest.raises(sessions.RealtimeError): complete(live, out)
    assert canonical_snapshot(live[1]) == before
    with live[1]() as db:
        assert db.get(ConversationTurn, out.turn_id).state == "completed"
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0


def test_socket_uses_bound_tool_results_before_spoken_completion(live):
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    before = canonical_snapshot(live[1])
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        emit(ws, live[2], "input_committed", item_id="A")
        emit(ws, live[2], "input_transcript_done", item_id="A", transcript="What is the weather in Berlin today?")
        receipt = receive(ws, "transcript_final")
        thinking = receive(ws, "assistant_thinking")
        async def wait_for_response():
            for _ in range(200):
                if live[2].requests: return
                await asyncio.sleep(.02)
            raise AssertionError("No final spoken request")
        ws.portal.call(wait_for_response)
        continuation = live[2].continuations[0]
        assert len(continuation) == 6
        assert all(continuation[i]["call_id"] == continuation[i + 1]["call_id"] for i in range(0, 6, 2))
        emit(ws, live[2], "response_created", response={"id": "spoken", "metadata": {"generation_id": thinking["active_generation_id"]}})
        binding = receive(ws, "assistant_started")
        audio(ws, live, binding)
        end = done(ws, live, binding)
        bound = {k: binding[k] for k in ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
        ws.send_json(dict(type="playback_drained", **bound, sequence=end["sequence"], samples=end["samples"], seal=end["seal"]))
        receive(ws, "assistant_completed")
        ws.send_json({"type": "end_call"})
        assert ws.receive_json()["type"] == "ended"
    assert canonical_snapshot(live[1]) == before
    with live[1]() as db:
        turn = db.get(ConversationTurn, receipt["turn_id"])
        assert db.scalar(select(MessageWebSource)).message_id == turn.assistant_message_id


@pytest.mark.parametrize("input_mode", ["text", "voice"])
def test_text_and_l12_share_pronoun_claim_fix(test_context, input_mode):
    from app.services.visitor_identity import upsert_profile
    from app.models.visitor import LegacyVisitorProfile
    from tests.test_legacy_persona_l6 import add_memory
    client, factory, provider, cid, actor, headers, _ = seed(test_context, "viewer")
    add_memory(factory, 1, "Pallavi was warm with her son Alex.", "relationship")
    with factory.begin() as db: upsert_profile(db, 1, actor, "Mallory", "son")
    before = canonical_snapshot(factory)
    response = send(client, cid, headers, "viewer", "I'm her son. Call me Sunny.", False, input_mode=input_mode)
    assert response.status_code == 201
    with factory() as db:
        profile = db.scalar(select(LegacyVisitorProfile).where(LegacyVisitorProfile.viewer_user_id == actor))
        assert profile.preferred_name == "Mallory" and profile.relationship_status == "unverified"
    assert canonical_snapshot(factory) == before


def test_preexisting_invalid_verified_profile_cannot_unlock_relationship():
    from types import SimpleNamespace
    from app.services.visitor_identity import visitor_evidence
    memory = SimpleNamespace(id=1, canonical_text="Pallavi was warm with her son Alex.", entity_links=[])
    profile = SimpleNamespace(preferred_name="Her", claimed_relationship="son", relationship_status="verified_from_memory", matched_entity_id=None)
    evidence = visitor_evidence([memory], profile)
    assert evidence["relationship_status"] == "unverified" and evidence["visitor_specific_nicknames"] == []

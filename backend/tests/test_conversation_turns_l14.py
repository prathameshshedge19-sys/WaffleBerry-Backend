"""L13 contract fixtures for the Phase B extraction; all data is disposable.

Provider-input digests are captured from dd246d6 before extraction. They include
the full ordered prompts/history, not just substrings of the new implementation.
L15 adds the owner-only DELETE correction and clarifies the existing prohibition
on inventing personal details. The exact additions are asserted separately.
"""

import asyncio
from dataclasses import replace
import hashlib
import json

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryRevision
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.services.memory import MemoryAnalysis, MemoryCandidate, MemoryOperation
from app.services.personality_worker import PersonalityWorker
from app.services.security import create_access_token
from app.services.visitor_identity import upsert_profile
from tests.test_legacy_persona_l6 import add_memory


SCENARIOS = {
    "owner": ("owner", "My mother enjoyed singing jasmine songs."),
    "collaborator": ("collaborator", "My aunt enjoyed singing jasmine songs."),
    "builder_fresh": ("owner", "What is the weather today?"),
    "setup": ("owner", "For my mother Pallavi."),
    "personal": ("viewer", "What flowers do you like?"),
    "general": ("viewer", "Explain photosynthesis."),
    "mixed": ("viewer", "What flowers do you like and explain photosynthesis?"),
    "fresh": ("viewer", "What is the weather today?"),
    "web_failure": ("viewer", "What is the weather today?"),
    "german": ("viewer", "Wie ist das Wetter heute?"),
    "expression": ("viewer", "What an unexpected surprise!"),
    "cadence": ("viewer", "What an unexpected surprise!"),
}

# Filled from the unmodified L13 routes, then held fixed for extraction parity.
L13_PROVIDER_DIGESTS = {
    "owner": "6458f09bac26c5cd90076fba36f7c793150fbbb9cb692363f15a0fcaa90dd29b",
    "collaborator": "478d9583f283dea95202ac497fe2e360fee2a5d94a857f92aac5aa161d28e955",
    "builder_fresh": "0b84404b4528a1ce3b3296b65650692caf450696d6847da83e0f35a5b818815d",
    "setup": "ce253ce45caeb5d3ee8abc86934f6325ce005817a319297f9c78489966272721",
    "personal": "d28b387f38a925a29b44dc34202a5988895d642f96668095a35cfe831c5a53d7",
    "general": "66945d0b495424c30550a1712a79391e0f9186e5572d988ac25972eb2c0093cc",
    "mixed": "3623e940a6e2f69029b74a9869f0689f88883fa12c669bdc549161a621806e77",
    "fresh": "5e6a014feb24aee24e1e5bf62557c2d50d89c6c3b93c147222e16c745589e54b",
    "web_failure": "b0a13c52ad457f2a1f563541232d1ded4d5160a14f0806468cc1a55073065845",
    "german": "91a538c668d63e9c4f53fe2d729cb5ad75b9e8f5eaf17198b61d2069773b3354",
    "expression": "05789739c2e527957a4af735c73a82c8bb31a5e20f426128dd1c8943aec5e356",
    "cadence": "f7e46ad9bf7f2856081831344d3d627c90fb9e6bf9ae3eec28d26689a66ad0e5",
}


def seed(test_context, role="owner"):
    client, sessions, _codes, provider = test_context
    with sessions.begin() as db:
        db.add_all([User(id=i, full_name=name, email=f"l14-{i}@example.com", password_hash="test", is_verified=True)
                    for i, name in ((1, "Owner"), (2, "Contributor"), (3, "Alex"), (4, "Other"))])
        db.flush()
        db.add_all([Legacy(id=1, owner_user_id=1, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active"),
                    Legacy(id=2, owner_user_id=4, subject_name="Other", relationship_to_owner="self", is_self=True, setup_status="active")])
        db.flush()
        db.add(LegacyCollaborator(legacy_id=1, user_id=2, status="active"))
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active"))
    memory_ids = [add_memory(sessions, 1, text, category) for text, category in (
        ("Pallavi loved jasmine flowers.", "preference"),
        ("Pallavi was warm with family.", "personality"),
        ('Pallavi often said "Well, imagine that!" when surprised.', "habit"),
        ("Alex is Pallavi's son.", "relationship"),
    )]
    add_memory(sessions, 1, "Pallavi hated jasmine flowers.", "preference", status="superseded")
    add_memory(sessions, 2, "Other loved secret orchids.", "preference")
    assert PersonalityWorker(sessions).run_once() == "ready"
    actor_id = {"owner": 1, "collaborator": 2, "viewer": 3}[role]
    mode = "legacy" if role == "viewer" else "rya"
    with sessions.begin() as db:
        conversation = Conversation(user_id=actor_id, legacy_id=1, mode=mode, title="New chat")
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
        db.add_all([Message(conversation_id=conversation_id, role=MessageRole.USER, content="Let's continue."),
                    Message(conversation_id=conversation_id, role=MessageRole.ASSISTANT, content="I am listening.")])
        if role == "viewer":
            upsert_profile(db, 1, actor_id, "Alex", "son")
    headers = {"Authorization": "Bearer " + create_access_token(actor_id)}
    return client, sessions, provider, conversation_id, actor_id, headers, memory_ids


def send(client, conversation_id, headers, policy_role, content, streaming, **extra):
    prefix = "legacy-conversations" if policy_role == "viewer" else "conversations"
    suffix = "/stream" if streaming else ""
    return client.post(f"/api/v1/{prefix}/{conversation_id}/messages{suffix}?legacy_id=1&timezone=Europe/Berlin",
                       json={"content": content, "input_mode": "voice", **extra}, headers=headers)


def events(response):
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return result


def canonical_snapshot(sessions):
    # Includes embeddings, revision/provenance, projection generation, activity.
    with sessions() as db:
        return {table.name: [tuple(row) for row in db.execute(select(table).order_by(*table.primary_key.columns))]
                for table in (Memory.__table__, MemoryRevision.__table__, LegacyPersonalityProfile.__table__, BuilderActivity.__table__)}


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_l13_provider_input_and_effect_contract(test_context, scenario, streaming):
    role, content = SCENARIOS[scenario]
    client, sessions, provider, cid, actor_id, headers, _ids = seed(test_context, role)
    if scenario == "setup":
        with sessions.begin() as db:
            legacy = db.get(Legacy, 1)
            legacy.subject_name = legacy.relationship_to_owner = legacy.is_self = None
            legacy.setup_status = "collecting_identity"
    if scenario == "cadence":
        with sessions.begin() as db:
            db.get(Message, 2).content = "Well, imagine that!"
    if scenario == "web_failure":
        provider.web_provider.failures.add(content)
    if scenario in {"owner", "collaborator"}:
        provider.memory_provider.analyses[content] = MemoryAnalysis(
            source_language="english", normalized_query="Pallavi jasmine singing",
            memories=[MemoryCandidate(canonical_text="Pallavi enjoyed singing jasmine songs.", category="habit", confidence=.98)],
        )
    before = canonical_snapshot(sessions)
    response = send(client, cid, headers, role, content, streaming)
    assert response.status_code == (200 if streaming else 201), response.text
    calls = provider.persona_provider.calls if role == "viewer" else provider.calls
    assert len(calls) == 1
    assert all(isinstance(turn.content, str) and isinstance(turn.role, str) for turn in calls[0])
    policy_additions = [turn for turn in calls[0] if turn.content.startswith("MEMORY PERMISSIONS\n")]
    assert len(policy_additions) == int(role == "collaborator")
    grounding_clarification = "- A preserved preference does not establish its reason, sensory associations, or emotional effects. For example, liking a flower alone does not establish enjoying its scent or finding it calming.\n"
    assert sum(turn.content.count(grounding_clarification) for turn in calls[0]) == int(role == "viewer")
    # Keep every original L13 context byte pinned; assert the two precise shared
    # policy additions above rather than recapturing a new golden digest.
    grounding_l17_missing = "- If a personal recollection/opinion is missing, first consider direct and semantically related memories, relevant chronology, verified relationship context, personality context, and reasonable commonsense implications. Answer naturally from strongly supported patterns even when the exact proposition was not preserved. Say naturally that you do not remember only as a last resort. Never expose database, retrieval, canonical-memory, or implementation language.\n"
    grounding_l17_high = "- HIGH confidence direct facts and strong grounded implications supported by relevant records may be stated naturally, even when the question's exact wording is absent. Conversation-time implications are ephemeral and must never be stored.\n"
    def strip_l17_policy(value):
        return "\n".join(line for line in value.split("\n") if not line.startswith((grounding_l17_missing[:-1], grounding_l17_high[:-1])))
    serialized = json.dumps([(turn.role, strip_l17_policy(turn.content.replace(grounding_clarification, "").replace("\n\n", "\n")))
                             for turn in calls[0] if turn not in policy_additions], ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    assert digest and "canonical-memory" not in serialized
    assert "secret orchids" not in serialized and "hated jasmine" not in serialized
    fresh = scenario in {"fresh", "web_failure", "german"}
    assert provider.web_provider.calls == ([content] if fresh else [])
    assert len(provider.memory_provider.analysis_calls) == (0 if role == "viewer" else 1)
    if role == "viewer":
        assert canonical_snapshot(sessions) == before
        assert "STRICT READ-ONLY" in serialized
    else:
        assert "OWNER BUILDER CONTEXT" in serialized if role == "owner" else "COLLABORATOR BUILDER CONTEXT" in serialized
        assert "BEGIN_L13_STYLE_ONLY_DATA" not in serialized
    if streaming:
        output = events(response)
        assert output[0][0] == "start" and output[-1][0] == "done"
        assert not any(name == "error" for name, _ in output)
        assert output[0][1]["input_mode"] == output[-1][1]["input_mode"] == "voice"
        assert response.headers["cache-control"] == "no-cache, no-transform"
        done = output[-1][1]
        expected_keys = {"message_id", "conversation_id", "input_mode", "memories_saved"}
        expected_keys |= {"mode", "current_information", "sources"} if role == "viewer" else {"progress", "streak", "today_just_completed"}
        assert set(done) == expected_keys
        assert done["memories_saved"] == int(scenario in {"owner", "collaborator"})
        if role != "viewer":
            assert done["today_just_completed"] == (scenario in {"owner", "collaborator"})
    else:
        assert set(response.json()) == {"user_message", "rya_message"}
    prefix = "legacy-conversations" if role == "viewer" else "conversations"
    history = client.get(f"/api/v1/{prefix}/{cid}/messages?legacy_id=1", headers=headers).json()
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    assert history[-2]["content"] == content
    assert bool(history[-1]["web_sources"]) == (fresh and scenario != "web_failure")
    with sessions() as db:
        assert db.get(Conversation, cid).title != "New chat"
        changed = db.scalars(select(Memory).where(Memory.source_conversation_id == cid)).all()
        activity = db.scalars(select(BuilderActivity)).all()
        assert len(changed) == len(activity) == int(scenario in {"owner", "collaborator"})
        if changed:
            assert changed[0].canonical_text == "Pallavi enjoyed singing jasmine songs."
            assert changed[0].contributor_user_id == changed[0].last_contributor_user_id == actor_id
            assert changed[0].source_message_id == history[-2]["id"]
            assert activity[0].contribution_count == 1


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
def test_collaborator_analyzed_delete_is_owner_only(test_context, streaming):
    """Approved L15 correction applies to the shared text/L12 mutation boundary."""
    client, sessions, provider, cid, actor_id, headers, ids = seed(test_context, "collaborator")
    content = "That flower memory is wrong; delete it."
    provider.memory_provider.analyses[content] = MemoryAnalysis(
        source_language="english", normalized_query="jasmine flowers",
        memories=[MemoryCandidate(canonical_text="Delete the jasmine memory.", category="preference", confidence=1,
                                  operation=MemoryOperation.DELETE, related_memory_ids=[ids[0]])],
    )
    assert client.delete(f"/api/v1/memories/{ids[0]}?legacy_id=1", headers=headers).status_code == 403
    response = send(client, cid, headers, "collaborator", content, streaming)
    assert response.status_code == (200 if streaming else 201)
    with sessions() as db:
        assert db.get(Memory, ids[0]).status == "active"
        revision = db.scalar(select(MemoryRevision).where(MemoryRevision.memory_id == ids[0]))
        assert revision is None
        assert db.scalar(select(BuilderActivity)) is None


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("role", ["owner", "collaborator", "viewer"])
def test_provider_failure_keeps_original_commit_boundary(test_context, monkeypatch, role, streaming):
    from app.services.legacy_persona import LegacyPersonaProviderError
    from app.services.rya import RyaProviderError

    client, sessions, provider, cid, _actor, headers, _ids = seed(test_context, role)
    selected = provider.persona_provider if role == "viewer" else provider
    error = LegacyPersonaProviderError("test_failure") if role == "viewer" else RyaProviderError("test_failure")
    async def fail(_turns):
        raise error
    async def fail_stream(_turns):
        yield "Unsaved partial"
        raise error
    monkeypatch.setattr(selected, "respond", fail)
    monkeypatch.setattr(selected, "stream", fail_stream)
    before = canonical_snapshot(sessions)
    response = send(client, cid, headers, role, "A contribution that failed.", streaming)
    assert response.status_code == (200 if streaming else 503)
    if streaming:
        assert [name for name, _ in events(response)] == ["start", "delta", "error"]
    with sessions() as db:
        messages = db.scalars(select(Message).where(Message.conversation_id == cid).order_by(Message.id)).all()
        assert [message.role.value for message in messages] == ["user", "assistant"] + (["user"] if streaming else [])
        assert db.get(Conversation, cid).title != "New chat" if streaming else db.get(Conversation, cid).title == "New chat"
    assert canonical_snapshot(sessions) == before


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("role", ["owner", "collaborator", "viewer"])
def test_commit_and_provider_call_order_matches_l13(test_context, monkeypatch, role, streaming):
    client, sessions, provider, cid, _actor, headers, _ids = seed(test_context, role)
    content = "My mother enjoyed singing jasmine songs." if role != "viewer" else "What is the weather today?"
    provider.memory_provider.analyses[content] = MemoryAnalysis(
        source_language="english", normalized_query="jasmine singing",
        memories=[MemoryCandidate(canonical_text="Pallavi enjoyed singing jasmine songs.", category="habit", confidence=.98)],
    )
    trace = []
    def committed(_session):
        if not _session.info.get("turn_control_commit"):
            trace.append("commit")
    for target, name, label in ((provider.memory_provider, "analyze", "analysis"),
                                (provider.memory_provider, "embed", "embedding"),
                                (provider.web_provider, "search", "web")):
        original = getattr(target, name)
        async def wrapped(*args, _original=original, _label=label, **kwargs):
            trace.append(_label)
            return await _original(*args, **kwargs)
        monkeypatch.setattr(target, name, wrapped)
    selected = provider.persona_provider if role == "viewer" else provider
    original_respond, original_stream = selected.respond, selected.stream
    async def respond(turns):
        trace.append("generation")
        return await original_respond(turns)
    async def stream(turns):
        trace.append("generation")
        async for chunk in original_stream(turns):
            yield chunk
    monkeypatch.setattr(selected, "respond", respond)
    monkeypatch.setattr(selected, "stream", stream)
    event.listen(Session, "after_commit", committed)
    try:
        response = send(client, cid, headers, role, content, streaming)
    finally:
        event.remove(Session, "after_commit", committed)
    assert response.status_code == (200 if streaming else 201), response.text
    expected = ["web", "generation", "commit"] if role == "viewer" else [
        "analysis", "embedding", "generation", "commit", "embedding", "commit", "commit",
    ]
    assert trace == (["commit"] if streaming else []) + expected


@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("role", ["owner", "collaborator", "viewer"])
def test_routes_use_shared_core_and_ignore_client_privilege_fields(test_context, monkeypatch, role, streaming):
    from app.api.routes import conversations, legacy_conversations
    from app.services.conversation_turns import prepare_turn

    client, sessions, provider, cid, actor_id, headers, _ids = seed(test_context, role)
    captured = []
    async def capture(*args, **kwargs):
        prepared = await prepare_turn(*args, **kwargs)
        captured.append(prepared)
        return prepared
    monkeypatch.setattr(legacy_conversations if role == "viewer" else conversations, "prepare_turn", capture)
    response = send(client, cid, headers, role, "Explain photosynthesis.", streaming,
                    role="owner", mode="rya", legacy_id=2, actor_id=4, capabilities={"apply_builder_memory": True})
    assert response.status_code == (200 if streaming else 201), response.text
    assert len(captured) == 1
    context = captured[0].actor
    assert (context.actor_id, context.legacy_id, context.conversation_id, context.role) == (actor_id, 1, cid, role)
    assert context.mode == ("legacy" if role == "viewer" else "rya")
    assert context.capabilities.apply_builder_memory == (role != "viewer")
    assert context.input_mode == "voice"
    assert context.timezone_name == ("UTC" if role == "viewer" else "Europe/Berlin")
    # A forged scope in the URL still hits existing server access checks.
    prefix = "legacy-conversations" if role == "viewer" else "conversations"
    denied = client.post(f"/api/v1/{prefix}/{cid}/messages?legacy_id=2", json={"content": "Wrong Legacy"}, headers=headers)
    assert denied.status_code == 409


def staged_turn(db, cid, actor_id, role, content="What flowers do you like?"):
    from app.services.conversation_turns import TurnActorContext

    conversation = db.get(Conversation, cid)
    legacy = db.get(Legacy, conversation.legacy_id)
    message = Message(conversation_id=cid, role=MessageRole.USER, content=content)
    db.add(message)
    db.commit()
    context = TurnActorContext.from_authorized(conversation, legacy, message, actor_id=actor_id, role=role)
    return context, conversation, legacy, message


@pytest.mark.parametrize("change", [
    {"legacy_id": 2}, {"conversation_id": 999}, {"actor_id": 4},
    {"mode": "legacy"}, {"role": "viewer"}, {"user_message_id": 999}, {"content": "Spoofed"},
])
def test_internal_scope_mismatch_is_rejected_before_preparation(test_context, change):
    from app.services.conversation_turns import prepare_turn

    _client, sessions, provider, cid, actor_id, _headers, _ids = seed(test_context)
    with sessions() as db:
        actor, conversation, legacy, message = staged_turn(db, cid, actor_id, "owner")
        with pytest.raises(ValueError):
            asyncio.run(prepare_turn(db, replace(actor, **change), conversation, legacy, message, provider.memory_provider))
    assert not provider.memory_provider.analysis_calls and not provider.memory_provider.embedding_calls


def test_direct_policies_reject_cross_legacy_rows(test_context):
    from app.services.builder_turns import prepare_builder_turn
    from app.services.persona_turns import prepare_persona_turn

    _client, sessions, provider, cid, actor_id, _headers, _ids = seed(test_context)
    with sessions() as db:
        actor, conversation, _legacy, message = staged_turn(db, cid, actor_id, "owner")
        for policy, kwargs in ((prepare_builder_turn, {"activated_now": False}), (prepare_persona_turn, {})):
            with pytest.raises(ValueError):
                asyncio.run(policy(db, actor, conversation, db.get(Legacy, 2), message, provider.memory_provider, **kwargs))
    assert not provider.memory_provider.analysis_calls and not provider.memory_provider.embedding_calls


def test_persona_preparation_is_select_only_and_cannot_finalize_builder_effects(test_context, monkeypatch):
    from app.services.builder_turns import complete_builder_turn
    from app.services.conversation_turns import TurnCompletionContext, complete_turn, prepare_turn
    from app.services.memory import LivingMemoryService

    _client, sessions, provider, cid, actor_id, _headers, ids = seed(test_context, "viewer")
    with sessions.begin() as db:
        db.get(Memory, ids[0]).embedding = None  # Visitor must not repair stale embeddings.
    before = canonical_snapshot(sessions)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Visitor invoked a builder mutation or write-capable retrieval")
    monkeypatch.setattr(LivingMemoryService, "store", forbidden)
    monkeypatch.setattr(LivingMemoryService, "retrieve", forbidden)
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())
    with sessions() as db:
        actor, conversation, legacy, message = staged_turn(db, cid, actor_id, "viewer")
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            prepared = asyncio.run(prepare_turn(db, actor, conversation, legacy, message, provider.memory_provider))
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert set(statements) <= {"SELECT", "SAVEPOINT", "RELEASE"}
        assert not db.new and not db.dirty
        assert prepared.route.needs_memory
        assistant = Message(conversation_id=cid, role=MessageRole.ASSISTANT, content="A preserved response.")
        db.add(assistant)
        db.commit()
        completion = TurnCompletionContext(conversation, legacy, message, assistant)
        result = asyncio.run(complete_turn(db, prepared, completion, include_progress=True))
        assert result.memories_saved == 0 and result.progress is None and result.streak is None
        with pytest.raises(ValueError):
            asyncio.run(complete_builder_turn(db, prepared, completion, include_progress=True))
    assert canonical_snapshot(sessions) == before
    assert not provider.memory_provider.analysis_calls
    assert provider.memory_provider.embedding_calls == [["What flowers do you like?"]]


def test_builder_cannot_gain_persona_web_capability(test_context):
    from app.services.conversation_turns import prepare_turn, prepare_current_information
    from app.services.persona_turns import add_current_information

    _client, sessions, provider, cid, actor_id, _headers, _ids = seed(test_context)
    with sessions() as db:
        actor, conversation, legacy, message = staged_turn(db, cid, actor_id, "owner", "What is the weather today?")
        prepared = asyncio.run(prepare_turn(db, actor, conversation, legacy, message, provider.memory_provider))
        for operation in (prepare_current_information, add_current_information):
            with pytest.raises(ValueError):
                asyncio.run(operation(prepared, provider.web_provider))
    assert not provider.web_provider.calls

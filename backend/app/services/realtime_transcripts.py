"""Provider-only final admission into L14. No assistant or canonical effects.

The bounded ordering buffer is connection-local; durable identity lives solely
in L14 client_turn_id/digest/message links. A reconnect reads receipts, never
replays audio or reclaims a processing token.
"""
from dataclasses import dataclass, field
import hashlib
import re
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select, update

from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession as Live
from app.models.turn import ConversationTurn
from app.services import realtime_sessions as sessions, turn_lifecycle as lifecycle
from app.services.conversation_turns import TurnActorContext, prepare_turn

MAX_TRANSCRIPT = 8000
MAX_ITEMS = 256  # Hard per-provider-connection cap, including deduplication tombstones.


def item_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise sessions.RealtimeError("realtime_provider_failed", 502)
    return value


def normalize(value):
    if not isinstance(value, str) or len(value) > MAX_TRANSCRIPT:
        raise sessions.RealtimeError("realtime_transcript_invalid", 422)
    if any(ord(c) < 32 and not c.isspace() for c in value):
        raise sessions.RealtimeError("realtime_transcript_invalid", 422)
    return " ".join(value.split())


def key_prefix(session_id):
    return "live:" + session_id + ":"


def admission_key(session_id, item_id):
    # Generation is intentionally excluded: redelivery of the same provider
    # identity must never create another message. Lease generation is checked
    # independently before every admission/replay.
    return key_prefix(session_id) + hashlib.sha256(item_identity(item_id).encode()).hexdigest()


@dataclass(repr=False)
class Item:
    committed: bool = False
    previous: str | None = None
    final: str | None = field(default=None, repr=False)
    failed: bool = False
    resolved: bool = False


class TranscriptOrder:
    def __init__(self, generation, depth=16):
        self.generation, self.depth = generation, depth
        self.items = {}
        self.tail = None
        self.speaking = False

    @property
    def unfinished(self):
        return self.speaking or any(not item.resolved for item in self.items.values())

    def observe(self, event):
        if event.generation != self.generation:
            return []
        if event.kind not in {"speech_started", "speech_stopped", "input_committed", "input_transcript_delta",
                              "input_transcript_done", "input_transcript_failed"}:
            return []
        identity = item_identity(event.payload.get("item_id"))
        item = self.items.get(identity)
        if item is None:
            if len(self.items) >= MAX_ITEMS or sum(not i.resolved for i in self.items.values()) >= self.depth:
                raise sessions.RealtimeError("realtime_queue_overrun", 429)
            item = self.items[identity] = Item()
        if event.kind == "speech_started":
            self.speaking = True
        elif event.kind == "speech_stopped":
            self.speaking = False
        elif event.kind == "input_committed":
            previous = event.payload.get("previous_item_id")
            if previous is not None:
                item_identity(previous)
            if previous == identity or (item.committed and item.previous != previous):
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            if any(k != identity and i.committed and i.previous == previous for k, i in self.items.items()):
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            item.committed, item.previous = True, previous
        elif event.kind == "input_transcript_done":
            final = normalize(event.payload.get("transcript"))
            if item.failed or (item.final is not None and item.final != final):
                raise sessions.RealtimeError("realtime_transcript_conflict", 409)
            item.final = final
        elif event.kind == "input_transcript_failed":
            if item.final is not None:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            item.failed = True
        elif event.kind == "input_transcript_delta" and not item.resolved:
            delta = event.payload.get("delta")
            if not isinstance(delta, str) or len(delta) > MAX_TRANSCRIPT:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            return [{"type": "transcript_provisional", "item_id": identity, "delta": delta}]
        return []

    def ready(self):
        """Advance only through committed predecessor links, never completion order."""
        while True:
            found = next(((k, i) for k, i in self.items.items()
                          if i.committed and not i.resolved and i.previous == self.tail), None)
            if found is None:
                return
            identity, item = found
            if item.final is None and not item.failed:
                return
            item.resolved = True
            self.tail = identity
            yield identity, item.final, item.failed


def receipt(db, turn, *, replayed=False):
    message = db.get(Message, turn.user_message_id)
    return {"type": "transcript_final", "turn_id": turn.id, "message_id": message.id,
            "conversation_id": turn.conversation_id, "legacy_id": turn.legacy_id,
            "mode": turn.mode,
            "input_mode": turn.input_mode, "content": message.content, "state": turn.state,
            "replayed": replayed}


def admit(db, session_id, owner, generation, item_id, transcript, settings):
    """Server-only; callers receive finals exclusively from the provider adapter."""
    content = normalize(transcript)
    if not content:
        raise sessions.RealtimeError("realtime_transcript_empty", 422)
    key = admission_key(session_id, item_id)
    try:
        row = sessions._get(db, session_id)
        actor_id = row.actor_user_id

        def factory(live):
            conversation = Conversation(user_id=live.actor_user_id, legacy_id=live.legacy_id,
                                        mode=live.mode, title="New chat")
            db.add(conversation)
            return conversation

        cid = sessions.bind_once(db, session_id, actor_id, factory, settings, owner=owner, generation=generation)
        conversation = db.get(Conversation, cid)
        payload = SimpleNamespace(content=content, input_mode="realtime_voice", client_turn_id=key)
        replay = lifecycle.accept_turn(db, conversation, payload, atomic_admission=True)
        if replay is None:
            message = Message(conversation=conversation, role=MessageRole.USER, content=content)
            db.add(message)
            lifecycle.link_user(db, message)
            if conversation.mode == "legacy":
                from app.api.routes.legacy_conversations import _profile
                from app.services.visitor_identity import capture_from_message
                capture_from_message(db, _profile(db, conversation.legacy_id, actor_id),
                                     conversation.legacy_id, actor_id, content)
            if conversation.title in {"New chat", "New conversation"}:
                if conversation.mode == "rya":
                    from app.api.routes.conversations import derive_conversation_title
                    conversation.title = derive_conversation_title(content)
                else:
                    from app.api.routes.legacy_conversations import _title
                    conversation.title = _title(content)
            conversation.updated_at = sessions.now()
            turn = db.get(ConversationTurn, db.info["active_turn_id"])
        else:
            turn = db.get(ConversationTurn, replay["turn_id"])
        db.flush()
        result = receipt(db, turn, replayed=replay is not None)
        db.commit()  # binding + L14 pending/CAS + user link, all or none
        return result
    except HTTPException as exc:
        db.rollback()
        raise sessions.RealtimeError("realtime_turn_busy" if exc.detail["code"] == "turn_in_progress"
                                     else "realtime_transcript_conflict", 409) from None
    except BaseException:
        db.rollback()
        raise


def reconcile(db, session_id, owner, generation, settings):
    row = sessions.owned(db, session_id, owner, generation, settings)
    turns = db.scalars(select(ConversationTurn).where(
        ConversationTurn.actor_user_id == row.actor_user_id,
        ConversationTurn.conversation_id == row.conversation_id,
        ConversationTurn.input_mode == "realtime_voice",
        ConversationTurn.client_turn_id.startswith(key_prefix(session_id)),
        ConversationTurn.user_message_id.is_not(None)).order_by(ConversationTurn.id.desc()).limit(MAX_ITEMS)).all()
    results = [receipt(db, t, replayed=True) for t in reversed(turns)]
    db.rollback()
    return results


async def prepare_admitted(db, session_id, owner, generation, turn_id, settings, memory_provider):
    """L14 handoff for the owned claim; no completion, persistence or write tools.

    Phase C acceptance exercises this handoff. The connection itself retains
    the active turn for the forthcoming Phase D response consumer.
    """
    row = sessions.owned(db, session_id, owner, generation, settings)
    turn = db.get(ConversationTurn, turn_id)
    if (turn is None or turn.conversation_id != row.conversation_id or turn.actor_user_id != row.actor_user_id
            or not (turn.client_turn_id or "").startswith(key_prefix(session_id))
            or turn.input_mode != "realtime_voice" or turn.state != "streaming" or not turn.claim_token):
        raise sessions.RealtimeError("realtime_access_changed")
    db.info.update(active_turn_id=turn.id, turn_claim_token=turn.claim_token)
    conversation, legacy = db.get(Conversation, turn.conversation_id), db.get(Legacy, turn.legacy_id)
    message = db.get(Message, turn.user_message_id)
    actor = TurnActorContext.from_authorized(conversation, legacy, message, actor_id=row.actor_user_id,
                                             role=row.role, input_mode="realtime_voice")
    try:
        prepared = await prepare_turn(db, actor, conversation, legacy, message, memory_provider)
        db.rollback()  # Preparation never commits domain changes.
        sessions.owned(db, session_id, owner, generation, settings)
        return prepared
    finally:
        db.rollback()


def release_unanswered(db, session_id):
    """Called under the session actor lock when its connection is fenced.

    This uses L14's existing interrupted terminal state, with no assistant row
    or interruption transcript. A crashed connection can never hold a chat busy
    indefinitely. User receipts survive; claims are never stolen or replayed.
    """
    db.execute(update(ConversationTurn).where(
        ConversationTurn.client_turn_id.startswith(key_prefix(session_id)),
        ConversationTurn.input_mode == "realtime_voice",
        ConversationTurn.state.in_(["pending", "streaming"]),
    ).values(state="interrupted", safe_error_code="cancelled", finished_at=sessions.now(), updated_at=sessions.now()))

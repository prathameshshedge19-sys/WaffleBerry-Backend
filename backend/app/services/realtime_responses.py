"""Live output gate. No domain effects; terminal writes use the L14 owned claim.

Only the socket controller constructs PlaybackProof. Browser input cannot name
arbitrary turns or provide assistant text. The actor lock serializes completion,
interruption, revocation and disconnect, including across PostgreSQL workers.
"""
import base64
import secrets
import time
from dataclasses import dataclass, field

from sqlalchemy import select, update

from app.models.conversation import Conversation, Message, MessageRole
from app.models.turn import ConversationTurn
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services import turn_lifecycle as lifecycle, turn_observability as obs

MAX_BUFFER_SAMPLES = 24000 * 20
MAX_RESPONSE_SAMPLES = 24000 * 120
MAX_FRAME_BYTES = 48000


def locked_turn(db, session_id, owner, connection, turn_id, claim, settings):
    row = sessions._get(db, session_id)
    sessions.lock_actor(db, row.actor_user_id)
    row = sessions._get(db, session_id)
    if (row.state != "connected" or row.lease_owner != owner or row.connection_generation != connection
            or sessions.now() >= sessions.utc(row.lease_expires_at)):
        raise sessions.RealtimeError("realtime_access_changed")
    sessions.reauthorize(db, row, settings)
    db.execute(update(Conversation).where(Conversation.id == row.conversation_id)
               .values(id=Conversation.id, updated_at=Conversation.updated_at))
    turn = db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id)
                     .execution_options(populate_existing=True))
    if (turn is None or turn.claim_token != claim or turn.input_mode != "realtime_voice"
            or turn.actor_user_id != row.actor_user_id or turn.conversation_id != row.conversation_id
            or turn.legacy_id != row.legacy_id or turn.mode != row.mode
            or not (turn.client_turn_id or "").startswith(transcripts.key_prefix(session_id))):
        raise sessions.RealtimeError("realtime_access_changed")
    db.info.update(active_turn_id=turn.id, turn_claim_token=claim)
    return turn


@dataclass(frozen=True, repr=False)
class PlaybackProof:
    transcript: str
    response_id: str
    sequence: int
    samples: int


def terminate(db, session_id, owner, connection, turn_id, claim, settings, proof=None, *, current=None):
    """One transaction for fresh authorization, assistant insertion and L14 CAS."""
    try:
        turn = locked_turn(db, session_id, owner, connection, turn_id, claim, settings)
        if turn.state != "streaming":
            db.rollback()
            return None
        assistant = None
        if proof is not None:
            if not isinstance(proof, PlaybackProof) or not proof.response_id or proof.sequence < 0 or proof.samples <= 0:
                raise ValueError("Invalid internal playback proof")
            content = transcripts.normalize(proof.transcript)
            if not content:
                raise ValueError("Empty assistant transcript")
            assistant = Message(conversation_id=turn.conversation_id, role=MessageRole.ASSISTANT, content=content)
            db.add(assistant)
            conversation = db.get(Conversation, turn.conversation_id)
            conversation.updated_at = sessions.now()
        lifecycle.finish_turn(db, assistant, state="completed" if assistant else "interrupted",
                              error_code=None if assistant else "cancelled")
        if assistant is not None and current is not None and turn.mode == "legacy":
            from app.api.routes.legacy_conversations import _persist_sources
            _persist_sources(db, assistant, current)
        result = dict(type="assistant_completed" if assistant else "assistant_interrupted", turn_id=turn.id,
                      conversation_id=turn.conversation_id, message_id=assistant.id if assistant else None)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


@dataclass(repr=False)
class Output:
    session_id: str
    connection: int
    turn_id: int
    claim: str
    response_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    sequence: int = -1
    samples: int = 0
    played: int = 0
    played_sequence: int = -1
    # Outstanding sequence -> cumulative samples is bounded by duration AND count.
    outstanding: dict = field(default_factory=dict)
    transcript: str = field(default="", repr=False)
    final: str | None = field(default=None, repr=False)
    audio_done: bool = False
    provider_done: bool = False
    seal: str | None = field(default=None, repr=False)
    retired: bool = False
    playback_started: bool = False
    item: tuple | None = None
    parts: dict = field(default_factory=dict, repr=False)
    audio_index: int = -1
    brain: object | None = field(default=None, repr=False)

    def binding(self):
        return dict(session_id=self.session_id, generation=self.connection, turn_id=self.turn_id,
                    active_generation_id=self.claim, response_id=self.response_id)

    def matches(self, value):
        return not self.retired and all(value.get(k) == v for k, v in self.binding().items())

    def telemetry(self, event, **kwargs):
        with obs.bound(obs.TurnObservation(session_id=self.session_id, conversation_turn_id=self.turn_id,
                                            generation_attempt_id=self.claim)):
            obs.emit(event, **kwargs)

    def provider(self, event):
        """Returns approved browser events only. All late output is fenced."""
        p = event.payload
        if self.retired or event.generation != self.connection:
            self.telemetry("realtime_stale_discard")
            return []
        if event.kind == "response_created":
            response = p.get("response", {})
            if response.get("metadata", {}).get("generation_id") != self.claim:
                self.telemetry("realtime_stale_discard")
                return []
            rid = transcripts.item_identity(response.get("id"))
            if self.response_id and rid != self.response_id:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            self.response_id = rid
            return [{"type": "assistant_started", **self.binding()}]
        rid = p.get("response", {}).get("id") if event.kind == "response_done" else p.get("response_id")
        if not self.response_id or rid != self.response_id:
            self.telemetry("realtime_stale_discard")
            return []
        if event.kind in {"function_delta", "function_done"}:
            raise sessions.RealtimeError("realtime_provider_failed", 502)
        if event.kind in {"audio", "audio_done", "output_transcript_delta", "output_transcript_done"}:
            identity = transcripts.item_identity(p.get("item_id"))
            index = p.get("output_index", 0)
            if p.get("content_index") != 0 or type(index) is not int or not 0 <= index < 8:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            part = self.parts.get(index)
            if part is None:
                if self.provider_done or any(value["id"] == identity for value in self.parts.values()):
                    raise sessions.RealtimeError("realtime_provider_failed", 502)
                part = self.parts[index] = dict(id=identity, transcript="", final=None, audio_done=False, samples=0)
            elif part["id"] != identity:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            self.item = (identity, 0)
        result = []
        if event.kind == "audio":
            if part["audio_done"] or self.provider_done or index < self.audio_index or index > self.audio_index + 1:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            self.audio_index = index
            delta = p.get("delta")
            if not isinstance(delta, str) or len(delta) > MAX_FRAME_BYTES * 2:
                raise sessions.RealtimeError("realtime_queue_overrun", 429)
            try:
                pcm = base64.b64decode(delta, validate=True)
            except ValueError:
                raise sessions.RealtimeError("realtime_provider_failed", 502) from None
            if not pcm or len(pcm) % 2 or len(pcm) > MAX_FRAME_BYTES:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            self.samples += len(pcm) // 2
            part["samples"] += len(pcm) // 2
            self.sequence += 1
            if (self.samples - self.played > MAX_BUFFER_SAMPLES or self.samples > MAX_RESPONSE_SAMPLES
                    or len(self.outstanding) >= 512):
                self.telemetry("realtime_queue_overrun")
                raise sessions.RealtimeError("realtime_queue_overrun", 429)
            self.outstanding[self.sequence] = self.samples
            if self.sequence == 0:
                self.telemetry("realtime_first_audio", duration_ms=(time.monotonic() - self.created_at) * 1000)
            result.append(dict(type="assistant_audio", **self.binding(), sequence=self.sequence, pcm=delta))
        elif event.kind == "audio_done":
            part["audio_done"] = True
        elif event.kind == "output_transcript_delta":
            delta = p.get("delta")
            if not isinstance(delta, str) or sum(len(v["transcript"]) + 1 for v in self.parts.values()) + len(delta) > transcripts.MAX_TRANSCRIPT:
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            if part["final"] is None:
                part["transcript"] += delta
        elif event.kind == "output_transcript_done":
            final = transcripts.normalize(p.get("transcript"))
            if (not final or sum(len(v["final"] or "") + 1 for i, v in self.parts.items() if i != index) + len(final) > transcripts.MAX_TRANSCRIPT
                    or (part["final"] is not None and final != part["final"])):
                raise sessions.RealtimeError("realtime_provider_failed", 502)
            part["final"] = final
        elif event.kind == "response_done":
            if p.get("response", {}).get("status") != "completed":
                return [{"type": "generation_failed"}]
            self.provider_done = True
        # Text for item N+1 may arrive before audio for item N finishes.
        # Preserve provider output order and require every spoken item in the
        # same response to finish before issuing one full-playback proof.
        ordered = [self.parts[i] for i in sorted(self.parts)]
        self.audio_done = bool(ordered) and all(v["audio_done"] and v["samples"] for v in ordered)
        self.final = " ".join(v["final"] for v in ordered) if ordered and all(v["final"] for v in ordered) else None
        if self.provider_done and (not self.audio_done or not self.final or sorted(self.parts) != list(range(len(self.parts)))):
            return [{"type": "generation_failed"}]
        if self.provider_done and self.audio_done and self.final and self.samples and self.seal is None:
            self.seal = secrets.token_urlsafe(32)
            result.append(dict(type="assistant_audio_end", **self.binding(), sequence=self.sequence,
                               samples=self.samples, seal=self.seal))
        return result

    def acknowledge(self, value):
        if not self.matches(value):
            self.telemetry("realtime_stale_discard")
            return None
        sequence, samples = value["sequence"], value["samples"]
        if value["type"] == "playback_started":
            if sequence != -1 or samples != 0 or not self.samples:
                raise sessions.RealtimeError("realtime_protocol_error", 400)
            if not self.playback_started:
                self.playback_started = True
                self.telemetry("realtime_first_playback", duration_ms=(time.monotonic() - self.created_at) * 1000)
            return None
        if sequence == self.played_sequence and samples == self.played:
            pass  # repeated progress/drain is harmless
        elif sequence < self.played_sequence:
            return None
        elif self.outstanding.get(sequence) != samples:
            raise sessions.RealtimeError("realtime_protocol_error", 400)
        if value["type"] == "playback_drained":
            if (not self.provider_done or not self.audio_done or not self.final or not self.seal
                    or value.get("seal") != self.seal or sequence != self.sequence or samples != self.samples):
                raise sessions.RealtimeError("realtime_protocol_error", 400)
            self.telemetry("realtime_playback_ack")
            return PlaybackProof(self.final, self.response_id, sequence, samples)
        self.played_sequence, self.played = sequence, samples
        self.outstanding = {k: v for k, v in self.outstanding.items() if k > sequence}
        self.telemetry("realtime_playback_progress", values={"playback_queue_samples": self.samples - samples})
        return None

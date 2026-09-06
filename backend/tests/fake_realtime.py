"""Deterministic Phase B/C/D protocol harness. Never imported by application code."""
import asyncio

from app.services.realtime_provider import ProviderEvent


class FakeRealtimeProvider:
    def __init__(self, depth=16):
        self.events = asyncio.Queue(maxsize=depth)
        self.connected = False
        self.closed = False
        self.cancelled = False
        self.audio_bytes = 0
        self.generation = 0
        self.voice = None
        self.requests = []
        self.cancelled_responses = []
        self.plans = []
        self.continuations = []

    async def connect(self, voice, generation):
        self.voice, self.generation, self.connected = voice, generation, True

    def emit(self, kind, payload=None, *, generation=None):
        self.events.put_nowait(ProviderEvent(kind, self.generation if generation is None else generation, payload or {}))

    async def receive(self):
        value = await self.events.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def append_audio(self, pcm):
        self.audio_bytes += len(pcm)

    async def plan_response(self, brain, generation_id, *, turn_id=None, session_id=None):
        self.plans.append((brain, generation_id))
        identity = "plan_" + generation_id
        metadata = {"generation_id": generation_id, "phase": "tools"}
        self.emit("response_created", {"response": {"id": identity, "metadata": metadata}})
        import json
        from app.services.web_search import minimize_search_query
        calls = [dict(type="function_call", call_id=name + "_" + generation_id, name=name,
                      arguments=json.dumps({"query": minimize_search_query(brain.prepared.actor.content)}
                                           if name == "get_current_information" else {"query": brain.prepared.actor.content}
                                           if name == "retrieve_legacy_memories" else {})) for name in sorted(brain.required_tools)]
        self.emit("response_done", {"response": {"id": identity, "metadata": metadata, "status": "completed", "output": calls}})

    async def create_response(self, prepared, generation_id, *, turn_id=None, session_id=None, continuation=()):
        self.requests.append((prepared, generation_id))
        self.continuations.append(list(continuation))

    async def cancel(self, response_id=None):
        self.cancelled = True
        self.cancelled_responses.append(response_id)

    async def close(self):
        self.closed = True

    async def drain_cancelled(self):
        pass

    def speech(self):
        self.emit("speech_started", {"item_id": "probe_input"})
        self.emit("input_transcript_delta", {"item_id": "probe_input", "delta": "Disposable"})
        self.emit("speech_stopped", {"item_id": "probe_input"})
        self.emit("input_committed", {"item_id": "probe_input"})
        self.emit("input_transcript_done", {"item_id": "probe_input", "transcript": "Disposable test."})

    def response(self, cancelled=False):
        self.emit("response_created", {"response": {"id": "probe_response"}})
        self.emit("audio", {"response_id": "probe_response", "delta": "AAA="})
        self.emit("output_transcript_delta", {"delta": "Disposable"})
        self.emit("output_transcript_done", {"transcript": "Disposable test."})
        self.emit("response_done", {"response": {"id": "probe_response", "status": "cancelled" if cancelled else "completed",
                  "usage": {"input_tokens": 12, "output_tokens": 8, "input_token_details": {"audio_tokens": 5}, "output_token_details": {"audio_tokens": 6}}}})

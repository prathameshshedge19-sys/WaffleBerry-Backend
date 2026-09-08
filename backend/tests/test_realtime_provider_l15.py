import asyncio
import json

import pytest

from app.config import get_settings
from app.services.realtime_provider import RealOpenAIRealtimeProvider
from app.services.realtime_sessions import RealtimeError


@pytest.mark.parametrize("rejected",[None,"voice","autonomous","tools","format"])
def test_exact_provider_configuration_and_cleanup(monkeypatch,rejected):
    import websockets.asyncio.client
    settings=get_settings()
    monkeypatch.setattr(settings,"openai_api_key","disposable-test-key")
    class Socket:
        def __init__(self): self.sent=[];self.closed=False
        async def send(self,value): self.sent.append(json.loads(value))
        async def close(self): self.closed=True
        async def recv(self):
            session=json.loads(json.dumps(self.sent[0]["session"]))
            if rejected=="voice": session["audio"]["output"]["voice"]="alloy"
            if rejected=="autonomous": session["audio"]["input"]["turn_detection"]["create_response"]=True
            if rejected=="tools": session["tools"]=[{"name":"unsafe"}]
            if rejected=="format": session["audio"]["input"]["format"]={"type":"audio/pcmu"}
            return json.dumps({"type":"session.updated","session":session})
    socket=Socket()
    async def connect(url,**kwargs):
        assert url=="wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
        assert kwargs["additional_headers"]["Authorization"]=="Bearer disposable-test-key"
        assert kwargs["max_queue"]==settings.realtime_queue_depth
        return socket
    monkeypatch.setattr(websockets.asyncio.client,"connect",connect)
    async def run():
        provider=RealOpenAIRealtimeProvider(settings)
        if rejected:
            with pytest.raises(RealtimeError): await provider.connect("cedar",3)
            assert socket.closed
        else:
            await provider.connect("cedar",3)
            await provider.append_audio(bytes(960))
            assert socket.sent[-1]["type"]=="input_audio_buffer.append"
            with pytest.raises(RealtimeError): await provider.append_audio(bytes(settings.realtime_frame_bytes+2))
            await provider.close()
    asyncio.run(run())


def test_terminal_usage_is_once_only_and_cancelled(monkeypatch):
    from app.services import usage_accounting as usage
    captured=[]
    class Sink:
        def record(self,event): captured.append(event)
    monkeypatch.setattr(usage,"sink",Sink())
    raw={"type":"response.done","response":{"id":"disposable-response","status":"cancelled",
          "usage":{"input_tokens":3,"output_tokens":2,"output_token_details":{"audio_tokens":1,"reasoning_tokens":1}}}}
    class Socket:
        async def recv(self): return json.dumps(raw)
    async def run():
        provider=RealOpenAIRealtimeProvider()
        provider.socket=Socket()
        await provider.receive();await provider.receive()
    asyncio.run(run())
    assert len(captured)==1 and captured[0].outcome=="interrupted"
    assert captured[0].usage.reasoning_tokens.value==1
    assert captured[0].usage.model=="gpt-realtime-2.1"
    assert captured[0].usage.cached_input_tokens.status=="unavailable"


def test_l14_context_generation_dedup_and_cancel_usage_correlation(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.services.rya import ChatTurn, RYA_SYSTEM_PROMPT
    from app.services import usage_accounting as usage
    captured=[]
    class Sink:
        def record(self,event):captured.append(event)
    monkeypatch.setattr(usage,"sink",Sink())
    class Socket:
        def __init__(self):self.sent=[];self.events=[]
        async def send(self,value):self.sent.append(json.loads(value))
        async def recv(self):return json.dumps(self.events.pop(0))
    async def run():
        provider=RealOpenAIRealtimeProvider();socket=provider.socket=Socket()
        claim,sid=str(uuid4()),str(uuid4())
        prepared=SimpleNamespace(actor=SimpleNamespace(mode="rya"),turns=[ChatTurn("system","Grounded scope"),ChatTurn("assistant","Previously heard"),ChatTurn("user","Disposable input")])
        await provider.create_response(prepared,claim,turn_id=7,session_id=sid)
        response=socket.sent[0]["response"]
        assert response["conversation"]=="none" and response["metadata"]=={"generation_id":claim}
        assert response["instructions"].startswith(RYA_SYSTEM_PROMPT.replace("Rya", "Ree-yah"))
        assert response["tools"]==[] and response["tool_choice"]=="none"
        assert "Grounded scope" in response["instructions"]
        assert response["input"][0]["content"][0]=={"type":"output_text","text":"Previously heard"}
        with pytest.raises(RealtimeError):await provider.create_response(prepared,claim)
        assert len(socket.sent)==1
        socket.events=[{"type":"response.created","response":{"id":"resp","metadata":{"generation_id":claim}}},
                       {"type":"response.done","response":{"id":"resp","status":"cancelled","usage":{"input_tokens":4,"output_tokens":3}}}]
        await provider.receive();await provider.cancel("resp");await provider.cancel("resp")
        assert len(socket.sent)==2
        await provider.drain_cancelled()
        assert len(captured)==1 and captured[0].outcome=="interrupted"
        assert captured[0].correlation["generation_attempt_id"]==claim
        assert captured[0].correlation["session_id"]==sid and captured[0].correlation["conversation_turn_id"]==7
        await provider.cancel("resp");assert len(socket.sent)==2
    asyncio.run(run())


def test_cancel_race_ignores_only_its_own_benign_provider_error():
    class Socket:
        def __init__(self):self.sent=[];self.events=[]
        async def send(self,value):self.sent.append(json.loads(value))
        async def recv(self):return json.dumps(self.events.pop(0))
    async def run():
        provider=RealOpenAIRealtimeProvider();socket=provider.socket=Socket()
        provider._active_responses.add("resp")
        await provider.cancel("resp")
        identity=socket.sent[0]["event_id"]
        socket.events=[{"type":"error","error":{"code":"response_cancel_not_active","event_id":identity}},
                       {"type":"error","error":{"code":"response_cancel_not_active","event_id":"foreign"}}]
        assert await provider.receive() is None
        assert (await provider.receive()).kind=="error"
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["rya", "legacy"])
@pytest.mark.parametrize("phase", ["audio", "tools"])
def test_pronunciation_is_explicit_in_spoken_requests_without_changing_identity(mode, phase):
    from types import SimpleNamespace
    from app.services.rya import ChatTurn, RYA_SYSTEM_PROMPT
    from app.services.realtime_provider import RYA_AUDIO_INTRODUCTION

    class Socket:
        sent = []
        async def send(self, value):
            self.sent.append(json.loads(value))

    async def run():
        provider = RealOpenAIRealtimeProvider()
        provider.socket = Socket()
        prepared = SimpleNamespace(actor=SimpleNamespace(mode=mode), turns=[
            ChatTurn("system", "Keep the authorized speaker identity. Canonical human names: Rya and Raya."),
            ChatTurn("user", "Rya, what is your name? My aunt is named Raya."),
            ChatTurn("assistant", "My name is Raya pronounced Riya."),
        ])
        original_turns = list(prepared.turns)
        continuation = [{"type": "function_call_output", "call_id": "synthetic", "output": "Human name: Rya"}]
        await provider._response(prepared, "pronunciation-test", 1, None, phase=phase, continuation=continuation)
        response = provider.socket.sent[-1]["response"]
        assert response["input"][0]["content"][0]["text"] == prepared.turns[1].content
        assert response["input"][1]["content"][0]["text"] == prepared.turns[2].content
        assert response["input"][2] == continuation[0]
        assert prepared.turns == original_turns
        assert prepared.turns[0].content in response["instructions"]
        expected_policy = RYA_SYSTEM_PROMPT.replace("Rya", "Ree-yah") if phase == "audio" else RYA_SYSTEM_PROMPT
        assert (expected_policy in response["instructions"]) == (mode == "rya")
        assert (RYA_AUDIO_INTRODUCTION in response["instructions"]) == (mode == "rya" and phase == "audio")
        if phase == "audio":
            assert response["output_modalities"] == ["audio"]
            assert "Ree-yah: REE-yah, exactly two syllables" in response["instructions"]
            assert "long ee as in see" in response["instructions"]
            assert "audible consonant y glide" in response["instructions"]
            assert "Do not volunteer pronunciation or spelling explanations" in response["instructions"]
            assert "does not rename any person" in response["instructions"]
            if mode == "rya":
                assert response["instructions"].startswith("You are Ree-yah,")
                assert '"My name is Ree-yah."' in response["instructions"]
                assert 'do not add "pronounced"' in response["instructions"]
                assert "Answer in the user's conversational language" in response["instructions"]
            else:
                assert "My name is Ree-yah" not in response["instructions"]
        else:
            assert response["output_modalities"] == ["text"]
            assert "REE-yah" not in response["instructions"]
            if mode == "rya":
                assert response["instructions"].startswith("You are Rya,")
        assert RYA_SYSTEM_PROMPT.startswith("You are Rya,")
    asyncio.run(run())

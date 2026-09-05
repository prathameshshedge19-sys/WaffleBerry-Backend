from sqlalchemy import func, select

from app.models.conversation import Message
from app.models.memory import Memory
from app.models.progress import BuilderActivity
from tests.conftest import register_user


def headers(auth):
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_transcription_requires_authentication_and_rejects_unsupported_audio(test_context):
    client, _sessions, codes, _provider = test_context
    assert client.post("/api/v1/voice/transcribe", files={"audio": ("voice.webm", b"audio", "audio/webm")}).status_code == 401
    auth = register_user(client, codes, email="voice-format@example.com")
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"audio": ("voice.txt", b"not audio", "text/plain")},
        headers=headers(auth),
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_audio"


def test_valid_multilingual_transcription_returns_text_only_and_has_no_side_effects(test_context):
    client, sessions, codes, provider = test_context
    auth = register_user(client, codes, email="voice-stt@example.com")
    provider.voice_provider.transcription = "माझ्या आईला jasmine ची फुले आवडायची."
    with sessions() as db:
        before = (
            db.scalar(select(func.count(Message.id))),
            db.scalar(select(func.count(Memory.id))),
            db.scalar(select(func.count(BuilderActivity.id))),
        )
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"audio": ("voice.webm", b"browser-audio", "audio/webm")},
        data={"duration_ms": "2400"},
        headers=headers(auth),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "माझ्या आईला jasmine ची फुले आवडायची."}
    with sessions() as db:
        after = (
            db.scalar(select(func.count(Message.id))),
            db.scalar(select(func.count(Memory.id))),
            db.scalar(select(func.count(BuilderActivity.id))),
        )
    assert after == before


def test_transcription_size_and_duration_limits_are_enforced(test_context, monkeypatch):
    client, _sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="voice-limit@example.com")
    response = client.post(
        "/api/v1/voice/transcribe",
        files={"audio": ("voice.webm", b"audio", "audio/webm")},
        data={"duration_ms": "400000"},
        headers=headers(auth),
    )
    assert response.status_code == 413
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "voice_max_upload_bytes", 4)
    oversized = client.post(
        "/api/v1/voice/transcribe",
        files={"audio": ("voice.webm", b"12345", "audio/webm")},
        data={"duration_ms": "1000"},
        headers=headers(auth),
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "audio_too_large"


def test_voice_preference_defaults_persists_and_rejects_unknown_voice(test_context):
    client, _sessions, codes, _provider = test_context
    auth = register_user(client, codes, email="voice-setting@example.com")
    assert client.get("/api/v1/voice/settings", headers=headers(auth)).json() == {"voice": "marin"}
    assert client.put("/api/v1/voice/settings", json={"voice": "cedar"}, headers=headers(auth)).json() == {"voice": "cedar"}
    assert client.get("/api/v1/voice/settings", headers=headers(auth)).json() == {"voice": "cedar"}
    assert client.put("/api/v1/voice/settings", json={"voice": "alloy"}, headers=headers(auth)).status_code == 422


def test_speech_requires_owned_assistant_message_normalizes_rya_and_caches(test_context):
    client, _sessions, codes, provider = test_context
    auth = register_user(client, codes, email="voice-tts@example.com")
    conversation = client.post("/api/v1/conversations", json={"title": "Voice"}, headers=headers(auth)).json()
    result = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "Who are you?", "input_mode": "voice"},
        headers=headers(auth),
    ).json()
    message_id = result["rya_message"]["id"]
    first = client.post("/api/v1/voice/synthesize", json={"message_id": message_id, "voice": "marin"}, headers=headers(auth))
    second = client.post("/api/v1/voice/synthesize", json={"message_id": message_id, "voice": "marin"}, headers=headers(auth))
    cedar = client.post("/api/v1/voice/synthesize", json={"message_id": message_id, "voice": "cedar"}, headers=headers(auth))
    assert first.status_code == second.status_code == cedar.status_code == 200
    assert first.headers["x-voice-cache"] == "miss"
    assert second.headers["x-voice-cache"] == "hit"
    assert len(provider.voice_provider.synthesis_calls) == 2
    assert provider.voice_provider.synthesis_calls[0] == ("I am Riya. I can help you think this through.", "marin")
    other = register_user(client, codes, email="voice-other@example.com")
    assert client.post("/api/v1/voice/synthesize", json={"message_id": message_id}, headers=headers(other)).status_code == 404


def test_fixed_voice_preview_is_cached_per_voice(test_context):
    client, _sessions, codes, provider = test_context
    auth = register_user(client, codes, email="voice-preview@example.com")
    for _ in range(2):
        response = client.post("/api/v1/voice/preview", json={"voice": "marin"}, headers=headers(auth))
        assert response.status_code == 200
    assert provider.voice_provider.synthesis_calls == [("Hi, I'm Riya. I'm here to listen.", "marin")]

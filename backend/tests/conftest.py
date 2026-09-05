import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-with-sufficient-length-123456"
os.environ["LEGARYA_DEBUG"] = "true"
os.environ["AI_MODEL"] = "test-model"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, build_engine, get_db
from app.main import app
from app.services.email import email_sender
from app.services.rya import ChatTurn, get_rya_provider
from app.services.memory import CanonicalEdit, MemoryAnalysis, get_memory_provider
from app.services.legacy_persona import get_legacy_persona_provider
from app.services.web_search import WebSearchError, WebSearchResult, WebSource, get_web_search_provider
from app.services.voice import get_voice_provider, speech_cache


class FakeRyaProvider:
    def __init__(self):
        self.calls: list[list[ChatTurn]] = []
        self.stream_chunks = ["I am ", "Rya. ", "I can help you think this through."]
        self.stream_error_after: int | None = None

    async def respond(self, messages):
        self.calls.append(list(messages))
        return "I am Rya. I can help you think this through."

    async def stream(self, messages):
        self.calls.append(list(messages))
        for index, chunk in enumerate(self.stream_chunks, start=1):
            yield chunk
            if self.stream_error_after == index:
                raise RuntimeError("provider stream failed")


class FakeMemoryProvider:
    model = "fake-memory-model"
    embedding_model = "fake-multilingual-embedding"
    embedding_version = "test-v1"
    embedding_dimensions = 4

    def __init__(self):
        self.analyses: dict[str, MemoryAnalysis] = {}
        self.canonical_edits: dict[str, CanonicalEdit] = {}
        self.analysis_calls: list[tuple[int, str]] = []
        self.embedding_calls: list[list[str]] = []

    async def analyze(self, legacy, source_text, existing_memories=()):
        self.analysis_calls.append((legacy.id, source_text))
        return self.analyses.get(
            source_text,
            MemoryAnalysis(source_language="english", normalized_query=source_text, memories=[]),
        )

    async def canonicalize_edit(self, legacy, source_text):
        return self.canonical_edits.get(
            source_text,
            CanonicalEdit(canonical_text=source_text, source_language="english", entities=[]),
        )

    async def embed(self, texts):
        values = list(texts)
        self.embedding_calls.append(values)
        return [self._vector(value) for value in values]

    @staticmethod
    def _vector(value):
        text = value.casefold()
        groups = (
            ("jasmine", "flower", "phula", "mogry", "फूल", "फुल"),
            ("music", "musik", "sangeet", "song"),
            ("football", "sport"),
            ("pune", "mumbai", "studied", "education", "college", "husband", "rajesh"),
        )
        vector = [1.0 if any(term in text for term in group) else 0.0 for group in groups]
        if not any(vector):
            vector[sum(ord(char) for char in text) % len(vector)] = 1.0
        return vector


class FakeLegacyPersonaProvider:
    def __init__(self):
        self.calls: list[list[ChatTurn]] = []
        self.responses: dict[str, str] = {}

    def _answer(self, messages):
        user_content = next((turn.content for turn in reversed(messages) if turn.role == "user"), "")
        return self.responses.get(user_content, "I can answer from what has been preserved about me.")

    async def respond(self, messages):
        self.calls.append(list(messages))
        return self._answer(messages)

    async def stream(self, messages):
        self.calls.append(list(messages))
        answer = self._answer(messages)
        midpoint = max(1, len(answer) // 2)
        yield answer[:midpoint]
        yield answer[midpoint:]


class FakeWebSearchProvider:
    def __init__(self):
        self.calls: list[str] = []
        self.results: dict[str, WebSearchResult] = {}
        self.failures: set[str] = set()

    async def search(self, query):
        self.calls.append(query)
        if query in self.failures:
            raise WebSearchError("web_search_test_failure")
        return self.results.get(query, WebSearchResult(
            digest="A current, source-grounded update is available.",
            sources=(WebSource(title="Current source", domain="example.com", url="https://example.com/current"),),
        ))


class FakeVoiceProvider:
    def __init__(self):
        self.transcription = "My mother loved jasmine flowers."
        self.transcription_calls = []
        self.synthesis_calls = []

    async def transcribe(self, audio, filename, content_type):
        self.transcription_calls.append((audio, filename, content_type))
        return self.transcription

    async def synthesize(self, text, voice):
        self.synthesis_calls.append((text, voice))
        return b"ID3-fake-mp3"


@pytest.fixture
def test_context(monkeypatch):
    get_settings.cache_clear()
    engine = build_engine("sqlite://")
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    codes: dict[tuple[str, str], str] = {}
    invite_links: dict[tuple[str, str], str] = {}
    provider = FakeRyaProvider()
    memory_provider = FakeMemoryProvider()
    persona_provider = FakeLegacyPersonaProvider()
    web_provider = FakeWebSearchProvider()
    voice_provider = FakeVoiceProvider()
    provider.memory_provider = memory_provider
    provider.persona_provider = persona_provider
    provider.web_provider = web_provider
    provider.invite_links = invite_links
    provider.voice_provider = voice_provider
    speech_cache._audio.clear()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    def capture_code(*, recipient, code, purpose):
        codes[(recipient, purpose)] = code

    monkeypatch.setattr(email_sender, "send_code", capture_code)
    monkeypatch.setattr(email_sender, "send_invitation", lambda **values: invite_links.__setitem__((values["recipient"], values["role"]), values["invite_url"]))
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_rya_provider] = lambda: provider
    app.dependency_overrides[get_memory_provider] = lambda: memory_provider
    app.dependency_overrides[get_legacy_persona_provider] = lambda: persona_provider
    app.dependency_overrides[get_web_search_provider] = lambda: web_provider
    app.dependency_overrides[get_voice_provider] = lambda: voice_provider
    with TestClient(app) as client:
        yield client, TestingSession, codes, provider
    app.dependency_overrides.clear()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(connection)
    engine.dispose()


def register_user(client, codes, email="one@example.com", name="One User", password="strong-pass-123"):
    response = client.post("/api/v1/auth/register", json={"full_name": name, "email": email, "accepted_terms": True})
    assert response.status_code == 202, response.text
    code = codes[(email, "registration")]
    verification = client.post("/api/v1/auth/verify-email", json={"email": email, "otp": code})
    assert verification.status_code == 200, verification.text
    completed = client.post("/api/v1/auth/complete-registration", json={"verification_token": verification.json()["authorization"], "password": password})
    assert completed.status_code == 201, completed.text
    return completed.json()

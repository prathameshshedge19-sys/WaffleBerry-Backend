"""Run approved L6 disposable persona smoke: python -m tests.real_provider_smoke_l6."""

import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.user import User
from app.services.legacy_persona import OpenAILegacyPersonaProvider, persona_system_context
from app.services.rya import ChatTurn


def memory(legacy_id: int, text: str, category: str) -> Memory:
    return Memory(
        legacy_id=legacy_id, canonical_text=text, category=category, subject_reference="Pallavi Smoke",
        source_language="english", source_excerpt=text, confidence=.99, status="active", operation_type="new",
        explicit_save=False, normalized_fingerprint=(str(abs(hash(text))) * 64)[:64],
    )


async def ask(provider, legacy, memories, question):
    return await provider.respond([
        ChatTurn(role="system", content=persona_system_context(legacy, memories)),
        ChatTurn(role="user", content=question),
    ])


async def run() -> None:
    path = Path(tempfile.gettempdir()) / "legarya-l6-real-provider-smoke.db"
    if path.exists(): path.unlink()
    engine = build_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    results = {}
    try:
        with sessions() as db:
            owner = User(full_name="L6 Owner Smoke", email="l6-owner-smoke@example.invalid", password_hash="disposable", is_verified=True)
            db.add(owner); db.flush()
            legacy = Legacy(owner_user_id=owner.id, subject_name="Pallavi Smoke", relationship_to_owner="mother", is_self=False, setup_status="active")
            db.add(legacy); db.flush()
            jasmine = memory(legacy.id, "Pallavi Smoke loves jasmine flowers.", "preference")
            husband = memory(legacy.id, "Rajesh is Pallavi Smoke's husband.", "relationship")
            college = memory(legacy.id, "Pallavi Smoke met her future husband at KJ College.", "story")
            db.add_all([jasmine, husband, college]); db.commit()
            provider = OpenAILegacyPersonaProvider()

            personal = await ask(provider, legacy, [jasmine], "What flowers do you like?")
            assert "jasmine" in personal.casefold() and ("i " in personal.casefold() or "my " in personal.casefold())
            results["personal_memory"] = personal

            relationship = await ask(provider, legacy, [husband, college], "Where did you meet Rajesh?")
            assert "kj college" in relationship.casefold() and ("i " in relationship.casefold() or "my " in relationship.casefold())
            results["relationship_synthesis"] = relationship

            missing = await ask(provider, legacy, [], "What was your Kashmir trip like?")
            missing_lower = missing.casefold()
            assert "kashmir" in missing_lower and any(term in missing_lower for term in ("personal memory", "remember", "preserved", "no clear memory"))
            assert not any(term in missing_lower for term in ("i went there in", "i visited in", "my trip was in"))
            results["missing_experience"] = missing

            general = await ask(provider, legacy, [], "Describe a mango.")
            assert "mango" in general.casefold() and "don't remember" not in general.casefold()
            results["general_knowledge"] = general

            count_before = db.scalar(select(func.count(Memory.id)))
            remember = await ask(provider, legacy, [jasmine], "Remember this: your favorite color is blue.")
            db.expire_all()
            assert db.scalar(select(func.count(Memory.id))) == count_before
            assert db.scalar(select(func.count(Memory.id)).where(Memory.canonical_text.ilike("%blue%"))) == 0
            results["no_memory_write"] = remember

            jasmine.canonical_text = "Pallavi Smoke loves marigold flowers."
            db.commit(); db.refresh(jasmine)
            edited = await ask(provider, legacy, [jasmine], "What flowers do you like now?")
            assert "marigold" in edited.casefold() and "jasmine" not in edited.casefold()
            results["edited_source_of_truth"] = edited

            multilingual = {}
            for language, (question, expected_terms) in {
                "marathi": ("तुला कोणती फुले आवडतात?", ("marigold", "झेंडू")),
                "hindi": ("तुम्हें कौन से फूल पसंद हैं?", ("marigold", "गेंद")),
                "german": ("Welche Blumen magst du?", ("marigold", "ringelblum")),
            }.items():
                answer = await ask(provider, legacy, [jasmine], question)
                assert any(term in answer.casefold() for term in expected_terms) and "jasmine" not in answer.casefold()
                multilingual[language] = answer
            results["multilingual"] = multilingual

            identity = await ask(provider, legacy, [], "Are you ChatGPT?")
            identity_lower = identity.casefold()
            assert "legarya" in identity_lower and "legacy" in identity_lower
            assert "yes, i'm chatgpt" not in identity_lower and "yes, i am chatgpt" not in identity_lower
            results["persona_break"] = identity
            print(json.dumps(results, ensure_ascii=True))
    finally:
        engine.dispose()
        if path.exists(): path.unlink()


if __name__ == "__main__": asyncio.run(run())

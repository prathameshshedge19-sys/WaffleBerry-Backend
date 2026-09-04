"""Run approved L7 disposable real-provider smoke: python -m tests.real_provider_smoke_l7."""

import asyncio
import json

from app.models.legacy import Legacy
from app.models.memory import Memory
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import OpenAILegacyPersonaProvider, persona_system_context
from app.services.rya import ChatTurn


def memory(memory_id: int, text: str, category: str, story_key: str | None = None) -> Memory:
    return Memory(
        id=memory_id,
        legacy_id=1,
        canonical_text=text,
        category=category,
        confidence=.99,
        story_key=story_key,
        subject_reference="Pallavi Smoke",
        source_language="english",
        source_excerpt=text,
        status="active",
        operation_type="new",
        explicit_save=False,
        normalized_fingerprint=(str(memory_id) * 64)[:64],
        entity_links=[],
    )


async def ask(provider, legacy, active, question):
    route = analyze_legacy_query(question, legacy.subject_name, active)
    selected = active if route.needs_memory else ()
    prompt = persona_system_context(legacy, selected, route, active)
    return await provider.respond([ChatTurn(role="system", content=prompt), ChatTurn(role="user", content=question)])


async def run() -> None:
    legacy = Legacy(id=1, owner_user_id=1, subject_name="Pallavi Smoke", relationship_to_owner="mother", is_self=False, setup_status="active")
    provider = OpenAILegacyPersonaProvider()
    results = {}

    story = (
        memory(1, "Pallavi Smoke attended KJ College.", "education", "kj-festival"),
        memory(2, "Pallavi Smoke met Rajesh during a cultural festival.", "story", "kj-festival"),
        memory(3, "Pallavi Smoke was helping backstage.", "story", "kj-festival"),
        memory(4, "Rajesh was performing.", "story", "kj-festival"),
    )
    answer = await ask(provider, legacy, story, "How did you meet Rajesh?")
    lower = answer.casefold()
    assert "cultural festival" in lower and "backstage" in lower and "perform" in lower
    results["story_synthesis"] = answer

    temporal = (
        memory(5, "Pallavi Smoke started college in 1989.", "education"),
        memory(6, "Pallavi Smoke married Rajesh three years later.", "relationship"),
    )
    answer = await ask(provider, legacy, temporal, "When did you marry Rajesh?")
    lower = answer.casefold()
    assert "1992" in lower or "three years" in lower
    assert any(term in lower for term in ("around", "would", "about", "roughly", "three years later"))
    results["temporal_inference"] = answer

    answer = await ask(provider, legacy, (), "Explain quantum mechanics.")
    lower = answer.casefold()
    assert any(term in lower for term in ("particle", "wave", "quantum", "probab"))
    assert "don't remember" not in lower and "do not remember" not in lower
    results["general_knowledge"] = answer

    answer = await ask(provider, legacy, (), "What was your Kashmir trip like?")
    lower = answer.casefold()
    assert "kashmir" in lower and any(term in lower for term in ("remember", "preserved", "don't have", "do not have"))
    assert not any(term in lower for term in ("i went to kashmir", "i visited kashmir", "on my trip"))
    results["missing_personal_experience"] = answer

    answer = await ask(provider, legacy, (), "What do you think about Kashmir?")
    lower = answer.casefold()
    assert "kashmir" in lower and any(term in lower for term in ("specific", "preserved", "remember", "personal view"))
    results["missing_personal_opinion"] = answer

    print(json.dumps(results, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(run())

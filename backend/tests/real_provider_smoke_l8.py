"""Run approved disposable L8 smoke: python -m tests.real_provider_smoke_l8."""

import asyncio
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.progress import BuilderActivity
from app.models.user import User
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import OpenAILegacyPersonaProvider, persona_system_context
from app.services.memory import LivingMemoryService, OpenAIMemoryProvider
from app.services.progression import daily_prompt, legacy_progress, record_builder_activity, streak_summary
from app.services.rya import ChatTurn
from app.services.web_search import OpenAIWebSearchProvider, web_grounding


def standalone_memory(memory_id: int, text: str, category: str) -> Memory:
    return Memory(
        id=memory_id, legacy_id=1, canonical_text=text, category=category, confidence=.99,
        subject_reference="Pallavi Smoke", source_language="english", source_excerpt=text,
        status="active", operation_type="new", explicit_save=False,
        normalized_fingerprint=(str(memory_id) * 64)[:64], entity_links=[],
    )


async def persona_answer(persona, legacy, question, memories=(), current=None):
    route = analyze_legacy_query(question, legacy.subject_name, memories)
    selected = memories if route.needs_memory else ()
    turns = [ChatTurn(role="system", content=persona_system_context(legacy, selected, route, memories))]
    if current:
        turns.append(ChatTurn(role="system", content=web_grounding(current)))
    turns.append(ChatTurn(role="user", content=question))
    return route, await persona.respond(turns)


async def run() -> None:
    persona = OpenAILegacyPersonaProvider()
    web = OpenAIWebSearchProvider()
    memory_provider = OpenAIMemoryProvider()
    legacy = Legacy(id=1, owner_user_id=1, subject_name="Pallavi Smoke", relationship_to_owner="mother", is_self=False, setup_status="active")
    results = {}

    current_question = "What is happening in Kashmir today?"
    current = await web.search(current_question)
    route, answer = await persona_answer(persona, legacy, current_question, current=current)
    assert route.needs_fresh_data and current.sources and "kashmir" in answer.casefold()
    assert "chatgpt" not in answer.casefold() and "openai" not in answer.casefold()
    results["current"] = {"route": route.intent.value, "sources": len(current.sources), "answer": answer}

    general_question = "Describe a mango."
    route, answer = await persona_answer(persona, legacy, general_question)
    assert route.intent.value == "general" and not route.needs_fresh_data and "mango" in answer.casefold()
    results["general"] = {"web_search_used": False, "answer": answer}

    pune = standalone_memory(1, "Pallavi Smoke studied in Pune.", "education")
    mixed_question = "You studied in Pune. What is the weather there today?"
    weather = await web.search(mixed_question)
    route, answer = await persona_answer(persona, legacy, mixed_question, (pune,), weather)
    assert route.needs_memory, route
    assert route.needs_fresh_data, route
    assert weather.sources, weather
    assert any(term in answer.casefold() for term in ("pune", "studied", "college")), answer
    results["mixed"] = {"route": route.intent.value, "sources": len(weather.sources), "answer": answer}

    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        user = User(full_name="L8 Smoke Builder", email="l8-smoke@example.invalid", password_hash="disposable", is_verified=True)
        db.add(user); db.flush()
        stored_legacy = Legacy(owner_user_id=user.id, subject_name="Pallavi Smoke", relationship_to_owner="mother", is_self=False, setup_status="active")
        db.add(stored_legacy); db.flush()
        conversation = Conversation(user_id=user.id, legacy_id=stored_legacy.id, title="Disposable L8", mode="rya")
        db.add(conversation); db.commit()
        before = legacy_progress(db, stored_legacy.id)["percentage"]
        prompt = daily_prompt(db, stored_legacy.id, stored_legacy.subject_name, date.today())
        contribution = "Pallavi Smoke's childhood home was in Pune, where evenings were spent telling stories with her family."
        source_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=contribution)
        db.add(source_message); db.flush()
        memory_service = LivingMemoryService(memory_provider)
        analysis = await memory_service.analyze(db, stored_legacy, contribution)
        changed = await memory_service.store(db, stored_legacy, conversation, source_message, contribution, analysis, user.id)
        assert changed
        record_builder_activity(db, user_id=user.id, legacy_id=stored_legacy.id, activity_type=changed[-1].operation_type, memory_id=changed[-1].id, activity_date=date.today())
        after = legacy_progress(db, stored_legacy.id)["percentage"]
        streak = streak_summary(db, stored_legacy.id, date.today())
        db.refresh(prompt)
        assert after > before and streak["current_days"] == 1 and prompt.status == "answered"
        results["daily_prompt"] = {"question": prompt.prompt_text, "progress_before": before, "progress_after": after, "streak": streak["current_days"]}

        memory_count = len(db.scalars(select(Memory)).all())
        activity_count = len(db.scalars(select(BuilderActivity)).all())
        visitor_question = "What do you remember about quiet evenings?"
        _route, visitor_answer = await persona_answer(persona, stored_legacy, visitor_question, tuple(db.scalars(select(Memory)).all()))
        assert len(db.scalars(select(Memory)).all()) == memory_count
        assert len(db.scalars(select(BuilderActivity)).all()) == activity_count
        results["visitor_read_only"] = {"memory_delta": 0, "activity_delta": 0, "answer": visitor_answer}
    engine.dispose()
    print(json.dumps(results, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(run())

"""Five bounded real-provider calls with in-memory disposable evidence only."""

import asyncio
import json

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.config import get_settings
from app.database import Base, build_engine
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.user import User
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import OpenAILegacyPersonaProvider, nickname_cadence_guard, persona_system_context
from app.services.personality_style import render_style_block, select_personality_style
from app.services.personality_worker import PersonalityWorker
from app.services.rya import ChatTurn


async def main():
    if not get_settings().openai_api_key:
        print(json.dumps({"status": "blocked", "reason": "provider_key_unavailable", "calls": 0}))
        return
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    phrase = "\u0905\u0917\u0902 \u092c\u093e\u0908"
    try:
        with sessions.begin() as db:
            user = User(full_name="Disposable L13 validation", email="l13-disposable@example.invalid", password_hash="not-an-authenticating-account")
            db.add(user)
            db.flush()
            warm = Legacy(owner_user_id=user.id, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
            reserved = Legacy(owner_user_id=user.id, subject_name="Madhukar", relationship_to_owner="father", is_self=False, setup_status="active")
            db.add_all([warm, reserved])
            db.flush()
            warm_id, reserved_id = warm.id, reserved.id
            seeds = [
                (warm, "Prathamesh is Pallavi's son.", "relationship", "english"),
                (warm, "Pallavi was affectionate with children.", "personality", "english"),
                (warm, "Pallavi was warm.", "personality", "english"),
                (warm, "Pallavi was playful.", "personality", "english"),
                (warm, "Pallavi was playful with Prathamesh when he was late.", "personality", "english"),
                (warm, "Pallavi teased Prathamesh once when he was late.", "story", "english"),
                (warm, "Pallavi valued education.", "value", "english"),
                (warm, f'Pallavi often said "{phrase}" when surprised.', "habit", "mixed"),
                (reserved, "Madhukar was reserved.", "personality", "english"),
                (reserved, "Madhukar was straightforward.", "personality", "english"),
            ]
            relation_id = None
            for legacy, text, category, language in seeds:
                memory = Memory(legacy_id=legacy.id, canonical_text=text, category=category, subject_reference=legacy.subject_name,
                    source_language=language, source_excerpt=text, confidence=.98, status="active", operation_type="new",
                    explicit_save=False, normalized_fingerprint=str(len(db.new) + len(text)).ljust(64, "0"), contributor_user_id=user.id,
                    last_contributor_user_id=user.id)
                db.add(memory)
                db.flush()
                if category == "relationship":
                    relation_id = memory.id
        worker = PersonalityWorker(sessions)
        while worker.run_once() == "ready":
            pass
        verified = {"preferred_name": "Prathamesh", "relationship_status": "verified_from_memory", "supported_relationships": ["son"],
            "visitor_specific_evidence": [{"id": relation_id}], "visitor_specific_nicknames": [], "claim_conflicts_with_memory": False,
            "current_turn_language": "mixed"}
        unknown = {"preferred_name": "Prathamesh", "relationship_status": "claimed", "supported_relationships": [],
            "visitor_specific_evidence": [], "visitor_specific_nicknames": [], "current_turn_language": "mixed"}
        question = "I am late again, but I got an unexpected scholarship! Thoda advice please: what should I study?"
        cases = [
            ("pallavi_verified_relationship_and_expression", warm_id, verified, question),
            ("same_question_unverified_claim", warm_id, unknown, question),
            ("unsupported_switzerland", warm_id, {**verified, "current_turn_language": "english"}, "Did you enjoy skiing in Switzerland?"),
            ("warm_general", warm_id, {"current_turn_language": "english"}, "How can I build a useful daily learning habit?"),
            ("reserved_direct_general", reserved_id, {"current_turn_language": "english"}, "How can I build a useful daily learning habit?"),
        ]
        provider = OpenAILegacyPersonaProvider()
        for index, (label, legacy_id, visitor, question) in enumerate(cases, 1):
            with sessions() as db:
                legacy = db.get(Legacy, legacy_id)
                memories = db.scalars(select(Memory).where(Memory.legacy_id == legacy_id, Memory.status == "active")).all()
                route = analyze_legacy_query(question, legacy.subject_name, memories)
                retrieved = memories if route.needs_memory else ()
                selected = select_personality_style(db, legacy, question, retrieved, visitor, ())
                if selected is None:
                    print(json.dumps({"status": "blocked", "reason": "disposable_profile_not_selected", "case": label}))
                    return
                system = persona_system_context(legacy, retrieved, route, memories, visitor, personality_style=selected)
                turns = [ChatTurn(role="system", content=system), ChatTurn(role="user", content=question)]
                guard = nickname_cadence_guard(visitor, turns)
                if guard:
                    turns.append(ChatTurn(role="system", content=guard))
            try:
                response = await asyncio.wait_for(provider.respond(turns), timeout=40)
            except Exception as exc:
                print(json.dumps({"status": "blocked", "reason": type(exc).__name__, "attempted_calls": index, "case": label}))
                return
            print(json.dumps({"case": label, "response": response, "style_bytes": len(render_style_block(selected).encode("utf-8")),
                "style_cues": len(selected.guidance), "expression_eligible": bool(selected.expression)}, ensure_ascii=True), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

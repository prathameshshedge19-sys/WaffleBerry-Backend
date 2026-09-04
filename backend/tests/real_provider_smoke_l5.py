"""Run the approved L5 disposable real-provider smoke: python tests/real_provider_smoke_l5.py."""

import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.collaboration import CollaboratorStatus
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryRevision, MemoryStatus
from app.models.user import User
from app.services.authorization import can_build_legacy
from app.services.collaboration import join_legacy, rotate_code
from app.services.memory import LivingMemoryService, OpenAIMemoryProvider, memory_grounding
from app.services.rya import ChatTurn, OpenAIRyaProvider


async def run() -> None:
    database_path = Path(tempfile.gettempdir()) / "legarya-l5-real-provider-smoke.db"
    if database_path.exists():
        database_path.unlink()
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with sessions() as db:
            owner = User(full_name="Prathamesh Smoke", email="l5-owner-smoke@example.invalid", password_hash="disposable", is_verified=True)
            arya = User(full_name="Arya Smoke", email="l5-arya-smoke@example.invalid", password_hash="disposable", is_verified=True)
            db.add_all([owner, arya]); db.flush()
            legacy = Legacy(owner_user_id=owner.id, subject_name="Pallavi Smoke", relationship_to_owner="mother", is_self=False, setup_status="active")
            db.add(legacy); db.commit(); db.refresh(legacy)
            code = rotate_code(db, legacy)
            role, membership = join_legacy(db, legacy, arya.id)
            assert role == "collaborator" and membership and can_build_legacy(db, arya.id, legacy)

            memory_provider = OpenAIMemoryProvider()
            memory_service = LivingMemoryService(memory_provider)
            rya_provider = OpenAIRyaProvider()
            collaborator_chat = Conversation(user_id=arya.id, legacy_id=legacy.id, title="Cooking memories", mode="rya")
            db.add(collaborator_chat); db.flush()
            contribution = "She always used to sing old Hindi songs while cooking."
            source = Message(conversation_id=collaborator_chat.id, role=MessageRole.USER, content=contribution)
            db.add(source); db.flush()
            analysis = await memory_service.analyze(db, legacy, contribution)
            saved = await memory_service.store(db, legacy, collaborator_chat, source, contribution, analysis, arya.id)
            active = memory_service.active_memories(db, legacy.id)
            assert saved and active and active[0].contributor_user_id == arya.id

            retrieved = await memory_service.retrieve(db, legacy.id, "What do we know about what she did while cooking?")
            answer = await rya_provider.respond([
                ChatTurn(role="system", content=memory_grounding(retrieved) or ""),
                ChatTurn(role="user", content="What do we know about what she did while cooking?"),
            ])
            assert answer.strip() and any(word in answer.casefold() for word in ("sing", "song", "hindi", "cook"))

            original_id = active[0].id
            correction = "Correction: she sang old Marathi songs while cooking, not Hindi songs."
            correction_message = Message(conversation_id=collaborator_chat.id, role=MessageRole.USER, content=correction)
            db.add(correction_message); db.flush()
            correction_analysis = await memory_service.analyze(db, legacy, correction)
            await memory_service.store(db, legacy, collaborator_chat, correction_message, correction, correction_analysis, arya.id)
            db.expire_all()
            original = db.get(Memory, original_id)
            revisions = db.scalars(select(MemoryRevision).where(MemoryRevision.changed_by_user_id == arya.id)).all()
            current = db.scalars(select(Memory).where(Memory.legacy_id == legacy.id, Memory.status == MemoryStatus.ACTIVE.value)).all()
            assert original.status == MemoryStatus.SUPERSEDED.value
            assert revisions and any("Marathi" in item.canonical_text for item in current)

            membership.status = CollaboratorStatus.REVOKED.value
            db.commit()
            assert not can_build_legacy(db, arya.id, legacy)
            assert db.scalars(select(Memory).where(Memory.legacy_id == legacy.id)).all()
            print(json.dumps({
                "owner_collaborator_join": True,
                "collaborator_contribution": True,
                "owner_retrieval": True,
                "correction_with_provenance": True,
                "revocation_preserved_memories": True,
                "provider_answer": answer,
                "code_format_valid": code.startswith("COL-") and len(code) == 13,
            }, ensure_ascii=False))
    finally:
        engine.dispose()
        if database_path.exists():
            database_path.unlink()


if __name__ == "__main__":
    asyncio.run(run())

"""Per-effect transactions: a mutation and its receipt always commit together.

Assistant completion, memory application and activity are three transactions.
Missing receipts expose unfinished work; duplicate HTTP requests never resume it.
An internal recovery caller may safely repeat finalization with validated context.
"""
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.services import turn_observability as obs, usage_accounting as usage
from app.models.legacy import Legacy
from app.models.turn import ConversationTurn, TurnEffect
from app.services.conversation_turns import TurnCompletionResult
from app.services.memory import MemoryProviderError
from app.services.progression import legacy_progress, local_date, record_builder_activity, streak_summary


def lock_turn(db, turn_id, actor, completion):
    # An UPDATE obtains the write lock on both PostgreSQL and SQLite. Terminal
    # fields are unchanged; identity predicates are checked again at the write.
    changed = db.execute(update(ConversationTurn).where(
        ConversationTurn.id == turn_id, ConversationTurn.state == "completed",
        ConversationTurn.mode == "rya", ConversationTurn.actor_user_id == actor.actor_id,
        ConversationTurn.legacy_id == actor.legacy_id, ConversationTurn.conversation_id == actor.conversation_id,
        ConversationTurn.user_message_id == completion.user_message.id,
        ConversationTurn.assistant_message_id == completion.assistant_message.id,
    ).values(updated_at=ConversationTurn.updated_at)).rowcount
    if changed != 1 or not actor.capabilities.apply_builder_memory:
        raise ValueError("Effects require a completed, authorized builder turn")


async def apply_effects(db, turn_id, prepared, completion, *, include_progress):
    actor = prepared.actor
    completion.validate(actor)
    with obs.stage("memory_effect"):
        lock_turn(db, turn_id, actor, completion)
        memory_receipt = db.get(TurnEffect, (turn_id, "memory"))
        if memory_receipt is None:
            try:
                changed = await prepared.memory_service.store(
                    db, completion.legacy, completion.conversation, completion.user_message,
                    actor.content, prepared.memory_analysis, actor.actor_id, commit=False)
            except (MemoryProviderError, IntegrityError):
                # Preserve the existing optional-memory failure fallback. A completed
                # skipped receipt prevents a replay from changing that decision.
                db.rollback()
                lock_turn(db, turn_id, actor, completion)
                memory_receipt = db.get(TurnEffect, (turn_id, "memory"))
                changed = []
            if memory_receipt is None:
                memory_receipt = TurnEffect(turn_id=turn_id, kind="memory", result={
                    "count": len(changed), "memory_id": changed[-1].id if changed else None,
                    "operation": changed[-1].operation_type if changed else None,
                    "date": local_date(actor.timezone_name).isoformat() if changed or include_progress else None,
                })
                db.add(memory_receipt)
                db.commit()
    memory = memory_receipt.result

    with obs.stage("activity_effect"):
        lock_turn(db, turn_id, actor, completion)
        activity_receipt = db.get(TurnEffect, (turn_id, "activity"))
        if activity_receipt is None:
            first = False
            if memory["count"]:
                # Serialize different turns contributing to the same Legacy/day as
                # well as retries of this turn. SQLite already owns the write lock.
                db.scalar(select(Legacy).where(Legacy.id == actor.legacy_id).with_for_update())
                activity = record_builder_activity(db, user_id=actor.actor_id, legacy_id=actor.legacy_id,
                    activity_type=memory["operation"], memory_id=memory["memory_id"],
                    activity_date=date.fromisoformat(memory["date"]), commit=False)
                first = activity.was_first_today
            activity_receipt = TurnEffect(turn_id=turn_id, kind="activity", result={"first_today": first})
            db.add(activity_receipt)
            db.commit()
        else:
            db.rollback()  # release the no-op lock without a redundant commit
    today = date.fromisoformat(memory["date"]) if memory["date"] else local_date(actor.timezone_name) if include_progress else None
    return TurnCompletionResult(
        memories_saved=memory["count"],
        progress=legacy_progress(db, actor.legacy_id) if include_progress else None,
        streak=streak_summary(db, actor.legacy_id, today) if include_progress else None,
        today_just_completed=activity_receipt.result["first_today"],
    )

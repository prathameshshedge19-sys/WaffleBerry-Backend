"""Best-effort, non-critical automatic learning from completed Chat turns."""

import asyncio
import logging
from time import perf_counter

from app.config import get_settings
from app.db import SessionLocal
from app.dependencies.ai import get_memory_storage_pipeline


logger = logging.getLogger(__name__)

_TRIVIAL_TURNS = {
    "hello", "hi", "hey", "okay", "ok", "thanks", "thank you", "stop",
    "wait", "wait a second", "can you hear me", "can you repeat that",
    "you're breaking up", "the call isn't working", "switch to marathi",
    "speak louder", "i'm sleepy", "i'm tired today", "i'm really tired today",
    "it's raining", "i'm hungry right now", "the internet is slow",
    "i'm hungry", "the website is slow", "i don't like this voice",
}


def _chat_metric(stage: str, **counts: object) -> None:
    """Emit content-free Chat learning diagnostics."""
    suffix = " ".join(f"{key}={value}" for key, value in counts.items())
    logger.info("MEMORY_LEARNING source=chat stage=%s %s", stage, suffix)


def should_attempt_learning(user_text: str) -> bool:
    """Cheap conservative gate; structured extraction remains authoritative."""
    normalized = " ".join(user_text.casefold().strip(" .!?').\"").split())
    return 8 <= len(user_text.strip()) <= 4000 and normalized not in _TRIVIAL_TURNS


async def learn_conversation_safely(*, user_id: int, legacy_id: int, conversation_id: int) -> None:
    """Run the canonical pipeline in an independent DB session; never affect Chat."""
    if not get_settings().auto_memory_learning_enabled:
        return
    started = perf_counter()
    _chat_metric("started", candidate_count=0, saved_count=0)
    db = SessionLocal()
    try:
        report = await get_memory_storage_pipeline().process_conversation(
            db, user_id=user_id, legacy_id=legacy_id,
            conversation_id=conversation_id, metadata={"auto_learned": True},
        )
        duration = max(0, round((perf_counter() - started) * 1000))
        _chat_metric("extracted", candidate_count=report.candidates_extracted,
                     saved_count=0, duration_ms=duration)
        _chat_metric("validated", candidate_count=report.candidates_extracted,
                     saved_count=0, duration_ms=duration)
        stage = "saved" if report.memories_created else (
            "duplicate" if report.duplicates_skipped or report.possible_duplicates_skipped
            else "discarded"
        )
        _chat_metric(
            stage, candidate_count=report.candidates_extracted,
            saved_count=report.memories_created,
            duplicate_count=report.duplicates_skipped + report.possible_duplicates_skipped,
            duration_ms=duration,
        )
        logger.info(
            "MEMORY_LEARNING status=completed attempt_count=1 saved_count=%s "
            "duplicate_count=%s discarded_count=%s conflicted_count=%s error_count=%s "
            "learning_attempt_ms=%s",
            report.memories_created, report.duplicates_skipped + report.possible_duplicates_skipped,
            report.invalid_candidates_skipped + report.insufficient_candidates_skipped,
            report.contradictions_persisted, len(report.errors),
            max(0, round((perf_counter() - started) * 1000)),
        )
    except Exception:
        logger.exception(
            "MEMORY_LEARNING status=error attempt_count=1 saved_count=0 "
            "duplicate_count=0 discarded_count=0 conflicted_count=0 error_count=1"
        )
    finally:
        db.close()


def schedule_conversation_learning(
    *, user_id: int, legacy_id: int | None, conversation_id: int, user_text: str,
) -> None:
    """Schedule only owner-scoped persisted conversations without delaying a response."""
    if (legacy_id is None or not get_settings().auto_memory_learning_enabled
            or not should_attempt_learning(user_text)):
        return
    try:
        asyncio.create_task(learn_conversation_safely(
            user_id=user_id, legacy_id=legacy_id, conversation_id=conversation_id,
        ))
        _chat_metric("scheduled", candidate_count=0, saved_count=0)
    except Exception:
        logger.exception(
            "MEMORY_LEARNING status=schedule_error attempt_count=1 saved_count=0 error_count=1"
        )


async def learn_live_call_turn_safely(
    *, user_id: int, legacy_id: int, session_safe_id: str,
    turn_id: int, user_text: str,
) -> None:
    if not get_settings().auto_memory_learning_enabled:
        return
    db = SessionLocal()
    try:
        report = await get_memory_storage_pipeline().process_live_call_turn(
            db, user_id=user_id, legacy_id=legacy_id,
            session_safe_id=session_safe_id, turn_id=turn_id, user_text=user_text,
        )
        logger.info(
            "MEMORY_LEARNING status=completed source=live_call attempt_count=1 "
            "saved_count=%s duplicate_count=%s discarded_count=%s conflicted_count=%s error_count=%s",
            report.memories_created, report.duplicates_skipped + report.possible_duplicates_skipped,
            report.invalid_candidates_skipped + report.insufficient_candidates_skipped,
            report.contradictions_persisted, len(report.errors),
        )
    except Exception:
        logger.exception(
            "MEMORY_LEARNING status=error source=live_call attempt_count=1 saved_count=0 error_count=1"
        )
    finally:
        db.close()


def schedule_live_call_learning(**values) -> None:
    if (get_settings().auto_memory_learning_enabled
            and should_attempt_learning(str(values.get("user_text", "")))):
        asyncio.create_task(learn_live_call_turn_safely(**values))

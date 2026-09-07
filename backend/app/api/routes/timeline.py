from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.memory import Memory, MemoryEntity
from app.models.media_intelligence import SourceEvidence
from app.models.timeline import LifeEvent, LifeEventEntity, LifeEventEvidence, LifeEventMemory, TimelineEvidenceState, TimelineLifecycleState, TimelineOrigin, TimelineReviewState, TimelineLinkState
from app.models.user import User
from app.schemas.timeline import TimelineEventCreate, TimelineEventPatch, TimelineEventResponse, TimelineEvidenceCreate, TimelineReviewRequest
from app.services.authorization import legacy_role, require_legacy, require_persona_legacy
from app.services.timeline import TimelineService, _admission_key, serialize_event

router = APIRouter(prefix="/timeline", tags=["Legacy timeline"])


def _event(db, event_id: str, legacy_id: int, user_id: int, *, persona=False) -> LifeEvent:
    (require_persona_legacy if persona else require_legacy)(db, user_id, legacy_id)
    event = db.scalar(select(LifeEvent).where(LifeEvent.id == event_id, LifeEvent.legacy_id == legacy_id))
    if event is None: raise HTTPException(status_code=404, detail="Timeline event not found.")
    return event


def _owner(db, user_id, legacy_id):
    legacy = require_legacy(db, user_id, legacy_id)
    if legacy_role(db, user_id, legacy) != "owner": raise HTTPException(status_code=403, detail="Only the Legacy owner can perform this timeline action.")
    return legacy


def _attach_memories(db, event, memory_ids, user_id):
    for memory_id in dict.fromkeys(memory_ids):
        memory = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.legacy_id == event.legacy_id, Memory.status == "active"))
        if memory is None: raise HTTPException(status_code=404, detail="Memory not found.")
        if db.scalar(select(LifeEventMemory).where(LifeEventMemory.legacy_id == event.legacy_id, LifeEventMemory.event_id == event.id, LifeEventMemory.memory_id == memory.id, LifeEventMemory.link_state == "active")) is None:
            db.add(LifeEventMemory(legacy_id=event.legacy_id, event_id=event.id, memory_id=memory.id, link_role="additional_support", link_state="active", linked_by_user_id=user_id, source_memory_updated_at=memory.updated_at))


@router.get("", response_model=list[TimelineEventResponse])
def list_timeline(legacy_id: int = Query(..., ge=1), start: date | None = None, end: date | None = None, event_type: str | None = Query(default=None, max_length=40), limit: int = Query(default=50, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [serialize_event(db, event) for event in TimelineService(db).list_events(legacy_id, start=start, end=end, event_type=event_type, limit=limit)]


@router.get("/gaps")
def timeline_gaps(legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_legacy(db, user.id, legacy_id)
    events = TimelineService(db).list_events(legacy_id, limit=100)
    years = {event.date_start.year for event in events if event.date_start}
    return {"legacy_id": legacy_id, "event_count": len(events), "known_years": sorted(years), "note": "Gaps are prompts for conversation, not errors."}


@router.get("/{event_id}", response_model=TimelineEventResponse)
def get_timeline_event(event_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return serialize_event(db, _event(db, event_id, legacy_id, user.id))


@router.post("/events", response_model=TimelineEventResponse, status_code=status.HTTP_201_CREATED)
def create_timeline_event(payload: TimelineEventCreate, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(db, user.id, legacy_id)
    event = LifeEvent(id=__import__("uuid").uuid4().__str__(), legacy_id=legacy_id, admission_key=_admission_key(payload.title, payload.event_type, payload.place_label) + ":" + __import__("uuid").uuid4().hex[:12], title=payload.title, description=payload.description, event_type=payload.event_type, date_start=payload.date_start, date_end=payload.date_end, sort_date=payload.date_start, date_precision=payload.date_precision, is_approximate=payload.is_approximate, date_label=payload.date_label, sequence_hint=payload.sequence_hint, place_label=payload.place_label, origin=TimelineOrigin.HUMAN_CREATED.value, review_state=TimelineReviewState.APPROVED.value if payload.memory_ids else TimelineReviewState.NEEDS_REVIEW.value, lifecycle_state=TimelineLifecycleState.ACTIVE.value, created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(event); db.flush(); _attach_memories(db, event, payload.memory_ids, user.id)
    for entity_id in dict.fromkeys(payload.entity_ids):
        entity = db.scalar(select(MemoryEntity).where(MemoryEntity.id == entity_id, MemoryEntity.legacy_id == legacy_id))
        if entity is None: raise HTTPException(status_code=404, detail="Entity not found.")
        db.add(LifeEventEntity(legacy_id=legacy_id, event_id=event.id, entity_id=entity.id, role="mentioned"))
    db.commit(); db.refresh(event); return serialize_event(db, event)


@router.patch("/{event_id}", response_model=TimelineEventResponse)
def patch_timeline_event(payload: TimelineEventPatch, event_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = _owner(db, user.id, legacy_id) and _event(db, event_id, legacy_id, user.id)
    values = payload.model_dump(exclude_unset=True)
    if "date_start" in values: event.sort_date = values["date_start"]
    for key, value in values.items(): setattr(event, key, value)
    event.updated_by_user_id = user.id; db.commit(); db.refresh(event); return serialize_event(db, event)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeline_event(event_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(db, user.id, legacy_id); event = _event(db, event_id, legacy_id, user.id)
    event.lifecycle_state = TimelineLifecycleState.DELETED.value; event.review_state = TimelineReviewState.ARCHIVED.value; event.updated_by_user_id = user.id; db.commit(); return Response(status_code=204)


@router.post("/{event_id}/review", response_model=TimelineEventResponse)
def review_timeline_event(payload: TimelineReviewRequest, event_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(db, user.id, legacy_id); event = _event(db, event_id, legacy_id, user.id)
    if payload.action == "approve": event.review_state = TimelineReviewState.APPROVED.value
    elif payload.action == "resolve": event.review_state = TimelineReviewState.APPROVED.value; event.conflict_json = None; event.conflict_resolved_at = datetime.now(timezone.utc)
    else: event.review_state = TimelineReviewState.ARCHIVED.value; event.lifecycle_state = TimelineLifecycleState.DELETED.value
    event.updated_by_user_id = user.id; db.commit(); db.refresh(event); return serialize_event(db, event)


@router.post("/{event_id}/evidence", response_model=TimelineEventResponse)
def attach_timeline_evidence(payload: TimelineEvidenceCreate, event_id: str, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owner(db, user.id, legacy_id); event = _event(db, event_id, legacy_id, user.id)
    evidence = db.scalar(select(SourceEvidence).where(SourceEvidence.id == payload.evidence_id, SourceEvidence.legacy_id == legacy_id, SourceEvidence.removed_at.is_(None)))
    if evidence is None: raise HTTPException(status_code=404, detail="Evidence not found.")
    if db.scalar(select(LifeEventEvidence).where(LifeEventEvidence.legacy_id == legacy_id, LifeEventEvidence.event_id == event.id, LifeEventEvidence.evidence_id == evidence.id)) is None:
        db.add(LifeEventEvidence(legacy_id=legacy_id, event_id=event.id, evidence_id=evidence.id, link_state=TimelineEvidenceState.AVAILABLE.value, linked_by_user_id=user.id))
    event.origin = TimelineOrigin.SOURCE_SUPPORTED.value; event.updated_by_user_id = user.id; db.commit(); db.refresh(event); return serialize_event(db, event)

"""Deterministic, conservative L17 timeline structuring and retrieval."""

import hashlib
import re
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryEntityLink, MemoryStatus
from app.models.media_intelligence import SourceEvidence, MemorySourceLink, SupportState
from app.models.timeline import LifeEvent, LifeEventEntity, LifeEventEvidence, LifeEventMemory, TimelineEvidenceState, TimelineLifecycleState, TimelineLinkState, TimelineOrigin, TimelinePrecision, TimelineReviewState

YEAR = r"(?:18|19|20)\d{2}"
EVENT_PATTERNS = (
    ("move", re.compile(r"\b(?:moved|shifted|relocated|settled)\s+(?:to|in)\s+(.+?)(?=\s+(?:in|around)\s+" + YEAR + r"|[.!?]|$)", re.I), "Moved to {place}"),
    ("education", re.compile(r"\b(?:graduated|graduated from)\s+(?:from\s+)?(.+?)(?=\s+(?:in|around)\s+" + YEAR + r"|$)", re.I), "Graduated from {place}"),
    ("career", re.compile(r"\b(?:became|started working as|worked as)\s+(?:a\s+)?(.+?)(?=\s+(?:in|around)\s+" + YEAR + r"|$)", re.I), "Became {place}"),
)


def _bounds(year: int, month: int | None = None, day: int | None = None):
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    if day is None:
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    value = date(year, month, day)
    return value, value


def parse_date_semantics(text: str) -> dict:
    if re.search(r"\b(?:during|in her|his|their)\s+(?:college|childhood|school years|early career)\b", text, re.I):
        label = re.search(r"\b(?:during|in her|his|their)\s+([\w ]+?(?:college|childhood|school years|early career))\b", text, re.I)
        return {"date_start": None, "date_end": None, "sort_date": None, "precision": TimelinePrecision.LIFE_PERIOD.value, "is_approximate": False, "date_label": label.group(0) if label else "life period"}
    around = bool(re.search(r"\b(?:around|about|approximately|approx\.?|circa)\b", text, re.I))
    full = re.search(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(" + YEAR + r")\b", text, re.I)
    if full:
        month = datetime.strptime(full.group(2)[:3], "%b").month
        start, end = _bounds(int(full.group(3)), month, int(full.group(1)))
        return {"date_start": start, "date_end": end, "sort_date": start, "precision": TimelinePrecision.DAY.value, "is_approximate": around, "date_label": ("around " if around else "") + full.group(0)}
    month = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(" + YEAR + r")\b", text, re.I)
    if month:
        value = datetime.strptime(month.group(1)[:3], "%b").month; start, end = _bounds(int(month.group(2)), value)
        return {"date_start": start, "date_end": end, "sort_date": start, "precision": TimelinePrecision.MONTH.value, "is_approximate": around, "date_label": ("around " if around else "") + month.group(0)}
    year = re.search(r"\b(" + YEAR + r")\b", text)
    if year:
        start, end = _bounds(int(year.group(1)))
        return {"date_start": start, "date_end": end, "sort_date": start, "precision": TimelinePrecision.YEAR.value, "is_approximate": around, "date_label": ("around " if around else "") + year.group(1)}
    return {"date_start": None, "date_end": None, "sort_date": None, "precision": TimelinePrecision.UNKNOWN.value, "is_approximate": False, "date_label": None}


def _fingerprint(title: str, event_type: str, semantics: dict, place: str | None) -> str:
    raw = "|".join([title.casefold().strip(), event_type, semantics.get("date_label") or "", place.casefold().strip() if place else ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _admission_key(title: str, event_type: str, place: str | None) -> str:
    raw = "|".join((event_type.casefold().strip(), title.casefold().strip(), (place or "").casefold().strip()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TimelineService:
    def __init__(self, db: Session):
        self.db = db

    def _event_data(self, memory: Memory) -> dict | None:
        text = memory.canonical_text.strip()
        semantics = parse_date_semantics(text)
        for event_type, pattern, title_pattern in EVENT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            place = match.group(1).strip(" .,")
            if event_type == "move":
                title = title_pattern.format(place=place)
                label = place
            elif event_type == "education":
                label = place; title = title_pattern.format(place=place)
            else:
                label = None; title = title_pattern.format(place=place)
            return {"title": title[:255], "description": text[:2000], "event_type": event_type, "place_label": label, **semantics}
        return None

    def structure_memory(self, memory: Memory, *, actor_id: int | None = None, commit: bool = False) -> LifeEvent | None:
        """Materialize only explicit, deterministic chronology from an active memory."""
        if memory.status != MemoryStatus.ACTIVE.value or memory.legacy_id is None:
            return None
        data = self._event_data(memory)
        if data is None:
            return None
        existing_links = self.db.scalars(select(LifeEventMemory).where(LifeEventMemory.legacy_id == memory.legacy_id, LifeEventMemory.memory_id == memory.id)).all()
        if existing_links:
            event = self.db.scalar(select(LifeEvent).where(LifeEvent.legacy_id == memory.legacy_id, LifeEvent.id == existing_links[0].event_id))
            if event:
                self._refresh_conflict(event)
            return event
        admission_key = _admission_key(data["title"], data["event_type"], data.get("place_label"))
        compatible = self.db.scalars(select(LifeEvent).where(LifeEvent.legacy_id == memory.legacy_id, LifeEvent.lifecycle_state == TimelineLifecycleState.ACTIVE.value, LifeEvent.event_type == data["event_type"])).all()
        event = next((item for item in compatible if item.title.casefold() == data["title"].casefold() and self._compatible_dates(item, data)), None)
        if event is None:
            event = next((item for item in compatible if item.title.casefold() == data["title"].casefold()), None)
        if event is None:
            event = LifeEvent(id=str(uuid.uuid4()), legacy_id=memory.legacy_id, admission_key=admission_key, title=data["title"], description=data["description"], event_type=data["event_type"], date_start=data["date_start"], date_end=data["date_end"], sort_date=data["sort_date"], date_precision=data["precision"], is_approximate=data["is_approximate"], date_label=data["date_label"], place_label=data.get("place_label"), confidence=memory.confidence, origin=TimelineOrigin.CANONICAL_STRUCTURED.value, review_state=TimelineReviewState.APPROVED.value, created_by_user_id=actor_id, updated_by_user_id=actor_id)
            try:
                with self.db.begin_nested():
                    self.db.add(event); self.db.flush()
            except IntegrityError:
                event = self.db.scalar(select(LifeEvent).where(LifeEvent.legacy_id == memory.legacy_id, LifeEvent.admission_key == admission_key))
                if event is None:
                    raise
        link = LifeEventMemory(legacy_id=memory.legacy_id, event_id=event.id, memory_id=memory.id, link_role="primary_support", link_state=TimelineLinkState.ACTIVE.value, linked_by_user_id=actor_id, source_memory_updated_at=memory.updated_at)
        try:
            with self.db.begin_nested():
                self.db.add(link); self.db.flush()
        except IntegrityError:
            self.db.expire_all()
        if event.date_start is not None and data["date_start"] is not None and not self._compatible_dates(event, data):
            event.review_state = TimelineReviewState.CONFLICT.value
            event.conflict_resolved_at = None
            event.conflict_json = {"field": "date", "alternatives": [{"label": event.date_label, "memory_ids": []}, {"label": data["date_label"], "memory_ids": [memory.id]}]}
        for link in memory.entity_links:
            if link.entity.legacy_id != memory.legacy_id:
                continue
            role = "place" if link.entity.entity_type == "place" else ("person_involved" if link.entity.entity_type == "person" else "organization")
            if self.db.scalar(select(LifeEventEntity).where(LifeEventEntity.legacy_id == memory.legacy_id, LifeEventEntity.event_id == event.id, LifeEventEntity.entity_id == link.entity_id, LifeEventEntity.role == role)) is None:
                self.db.add(LifeEventEntity(legacy_id=memory.legacy_id, event_id=event.id, entity_id=link.entity_id, role=role))
        self.db.flush()
        self._refresh_conflict(event)
        if commit: self.db.commit()
        return event

    @staticmethod
    def _compatible_dates(event: LifeEvent, data: dict) -> bool:
        if event.date_start is None or data["date_start"] is None:
            return event.date_start is None and data["date_start"] is None
        return event.date_start <= data["date_end"] and data["date_start"] <= event.date_end

    def _refresh_conflict(self, event: LifeEvent):
        if event.review_state == TimelineReviewState.APPROVED.value and event.conflict_resolved_at is not None:
            return
        links = self.db.scalars(select(LifeEventMemory).where(LifeEventMemory.legacy_id == event.legacy_id, LifeEventMemory.event_id == event.id, LifeEventMemory.link_state == TimelineLinkState.ACTIVE.value)).all()
        dates = {}
        for link in links:
            memory = self.db.scalar(select(Memory).where(Memory.id == link.memory_id, Memory.legacy_id == event.legacy_id))
            if memory and memory.status == MemoryStatus.ACTIVE.value:
                semantics = parse_date_semantics(memory.canonical_text)
                if semantics["date_label"]: dates.setdefault(semantics["date_label"], []).append(memory.id)
        if len(dates) > 1:
            event.review_state = TimelineReviewState.CONFLICT.value
            event.conflict_json = {"field": "date", "alternatives": [{"label": key, "memory_ids": value} for key, value in dates.items()]}
        elif event.review_state == TimelineReviewState.CONFLICT.value:
            event.review_state = TimelineReviewState.APPROVED.value; event.conflict_json = None

    def reconcile_memory(self, legacy_id: int, memory_id: int, *, actor_id: int | None = None):
        memory = self.db.scalar(select(Memory).where(Memory.id == memory_id, Memory.legacy_id == legacy_id))
        links = self.db.scalars(select(LifeEventMemory).where(LifeEventMemory.legacy_id == legacy_id, LifeEventMemory.memory_id == memory_id, LifeEventMemory.link_state == TimelineLinkState.ACTIVE.value)).all()
        if memory is None or memory.status != MemoryStatus.ACTIVE.value:
            for link in links: link.link_state = TimelineLinkState.STALE.value
            for link in links:
                event = self.db.scalar(select(LifeEvent).where(LifeEvent.legacy_id == legacy_id, LifeEvent.id == link.event_id))
                if event: self._refresh_conflict(event)
            return
        self.structure_memory(memory, actor_id=actor_id)

    def rebuild(self, legacy_id: int):
        memories = self.db.scalars(select(Memory).where(Memory.legacy_id == legacy_id, Memory.status == MemoryStatus.ACTIVE.value).order_by(Memory.id)).all()
        for memory in memories: self.reconcile_memory(legacy_id, memory.id)
        self.db.flush()

    def list_events(self, legacy_id: int, *, start: date | None = None, end: date | None = None, event_type: str | None = None, entity_id: int | None = None, limit: int = 50):
        query = select(LifeEvent).where(LifeEvent.legacy_id == legacy_id, LifeEvent.lifecycle_state == TimelineLifecycleState.ACTIVE.value, LifeEvent.review_state.in_([TimelineReviewState.APPROVED.value, TimelineReviewState.CONFLICT.value]))
        if event_type: query = query.where(LifeEvent.event_type == event_type)
        if start: query = query.where((LifeEvent.date_end.is_(None)) | (LifeEvent.date_end >= start))
        if end: query = query.where((LifeEvent.date_start.is_(None)) | (LifeEvent.date_start <= end))
        if entity_id: query = query.join(LifeEventEntity, (LifeEventEntity.event_id == LifeEvent.id) & (LifeEventEntity.legacy_id == legacy_id)).where(LifeEventEntity.entity_id == entity_id)
        return list(self.db.scalars(query.order_by(LifeEvent.sort_date.is_(None), LifeEvent.sort_date, LifeEvent.sequence_hint.is_(None), LifeEvent.sequence_hint, LifeEvent.created_at, LifeEvent.id).limit(min(max(limit, 1), 100))).all())

    def retrieve(self, legacy_id: int, query: str, limit: int = 8) -> list[LifeEvent]:
        years = [int(value) for value in re.findall(YEAR, query)]
        start = date(years[0], 1, 1) if years else None; end = date(years[0], 12, 31) if years else None
        lowered = query.casefold()
        event_type = "education" if any(word in lowered for word in ("college", "school", "study", "graduat")) else ("career" if any(word in lowered for word in ("teacher", "teaching", "career", "work")) else None)
        events = self.list_events(legacy_id, start=start, end=end, event_type=event_type, limit=100)
        if not events: events = self.list_events(legacy_id, limit=100)
        cues = [token for token in re.findall(r"[\w']+", lowered) if len(token) > 3]
        events.sort(key=lambda event: (0 if any(cue in ((event.title + " " + (event.description or "")).casefold()) for cue in cues) else 1, event.sort_date or date.max, event.id))
        return events[:min(max(limit, 1), 20)]


def serialize_event(db: Session, event: LifeEvent) -> dict:
    memories = db.scalars(select(LifeEventMemory).where(LifeEventMemory.legacy_id == event.legacy_id, LifeEventMemory.event_id == event.id)).all()
    evidence = db.scalars(select(LifeEventEvidence).where(LifeEventEvidence.legacy_id == event.legacy_id, LifeEventEvidence.event_id == event.id)).all()
    active_memory_ids = []
    for link in memories:
        memory = db.scalar(select(Memory).where(Memory.id == link.memory_id, Memory.legacy_id == event.legacy_id))
        if memory and memory.status == MemoryStatus.ACTIVE.value and link.link_state == TimelineLinkState.ACTIVE.value: active_memory_ids.append(memory.id)
    return {"id": event.id, "legacy_id": event.legacy_id, "title": event.title, "description": event.description, "event_type": event.event_type, "date_start": event.date_start, "date_end": event.date_end, "date_precision": event.date_precision, "is_approximate": event.is_approximate, "date_label": event.date_label, "sequence_hint": event.sequence_hint, "place_label": event.place_label, "confidence": event.confidence, "origin": event.origin, "review_state": event.review_state, "lifecycle_state": event.lifecycle_state, "conflict": event.conflict_json, "memory_count": len(active_memory_ids), "source_count": sum(1 for item in evidence if item.link_state == TimelineEvidenceState.AVAILABLE.value and db.scalar(select(SourceEvidence.id).where(SourceEvidence.id == item.evidence_id, SourceEvidence.legacy_id == event.legacy_id, SourceEvidence.removed_at.is_(None))) is not None), "memory_ids": active_memory_ids, "created_at": event.created_at, "updated_at": event.updated_at}

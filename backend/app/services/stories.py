"""Bounded L18 story engine. Story text is never written to canonical systems."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol, Sequence
from uuid import uuid4

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import event, select, update, inspect
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryStatus
from app.models.media_intelligence import MemorySourceLink, SourceEvidence, SupportState
from app.models.story import Story, StoryChapter, StoryLifecycle, StoryPerspective, StoryScope, StoryStaleness, StorySupportKind, StorySupportLink, StorySupportState, StoryVersion, StoryVersionStatus, StoryVisibility
from app.models.timeline import LifeEvent, LifeEventMemory, TimelineLinkState, TimelineReviewState
from app.services.personality_style import render_style_block, select_personality_style
from app.services.timeline import TimelineService

MAX_EVENTS = 24
MAX_MEMORIES = 32
MAX_CHAPTERS = 8
MAX_CHAPTER_TEXT = 12000
MAX_PROVIDER_RETRIES = 2


class OutlineChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    memory_ids: list[int] = Field(default_factory=list, max_length=8)
    event_ids: list[str] = Field(default_factory=list, max_length=8)


class StoryOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapters: list[OutlineChapter] = Field(min_length=1, max_length=MAX_CHAPTERS)


class StoryChapterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    narrative_text: str = Field(min_length=1, max_length=MAX_CHAPTER_TEXT)
    memory_ids: list[int] = Field(default_factory=list, max_length=8)
    event_ids: list[str] = Field(default_factory=list, max_length=8)


class StoryProvider(Protocol):
    model: str
    async def outline(self, legacy: Legacy, scope: str, perspective: str, facts: Sequence[dict]) -> StoryOutline: ...
    async def chapter(self, legacy: Legacy, perspective: str, chapter: OutlineChapter, facts: Sequence[dict], style: str = "") -> StoryChapterDraft: ...


def _subject(legacy: Legacy) -> str:
    return legacy.subject_name or "the Legacy subject"


def _first_person(perspective: str) -> bool:
    return perspective == StoryPerspective.LEGACY_FIRST_PERSON.value


def _fact_rows(db: Session, legacy_id: int, scope: str) -> tuple[list[LifeEvent], list[Memory]]:
    events = TimelineService(db).list_events(legacy_id, limit=MAX_EVENTS)
    memories = list(db.scalars(select(Memory).where(Memory.legacy_id == legacy_id, Memory.status == MemoryStatus.ACTIVE.value).order_by(Memory.id).limit(MAX_MEMORIES)).all())
    if scope != StoryScope.FULL_BIOGRAPHY.value:
        terms = {scope}
        if scope == StoryScope.PLACE.value: terms |= {"move", "place"}
        if scope == StoryScope.EVENT.value: terms |= {"life_event", "event"}
        memories = [m for m in memories if m.category in terms or any(t in m.canonical_text.casefold() for t in terms)]
        events = [e for e in events if e.event_type in terms or any(t in (e.title + " " + (e.description or "")).casefold() for t in terms)]
    return events, memories


def _facts(db: Session, legacy: Legacy, scope: str) -> list[dict]:
    events, memories = _fact_rows(db, legacy.id, scope)
    rows = []
    for event in events:
        links = db.scalars(select(LifeEventMemory).where(LifeEventMemory.legacy_id == legacy.id, LifeEventMemory.event_id == event.id, LifeEventMemory.link_state == TimelineLinkState.ACTIVE.value)).all()
        rows.append({"kind": "timeline_event", "id": event.id, "title": event.title, "text": event.description or event.title, "date_label": event.date_label, "review_state": event.review_state, "memory_ids": [link.memory_id for link in links]})
    for memory in memories:
        rows.append({"kind": "memory", "id": memory.id, "text": memory.canonical_text, "category": memory.category})
    return rows[:MAX_MEMORIES + MAX_EVENTS]


def _clean(value: str) -> str:
    return " ".join(value.split()).strip()


def _year_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b(?:18|19|20)\d{2}\b", text))


def audit_chapter(draft: StoryChapterDraft, perspective: str, facts: Sequence[dict]) -> dict:
    """Deterministic safety floor; unsupported prose is rejected, not confidence-lowered."""
    text = _clean(draft.narrative_text)
    source = " ".join(str(item.get("text", "")) for item in facts)
    source_years = _year_tokens(source) | {str(item.get("date_label")) for item in facts if item.get("date_label")}
    output_years = _year_tokens(text)
    unsupported_years = sorted(year for year in output_years if year not in source_years)
    quoted = [item for pair in re.findall(r"(?:[\"“]([^\"”]+)[\"”]|'([^']+)')", text) for item in pair if item]
    quote_supported = all(quote.casefold() in source.casefold() for quote in quoted)
    causal = bool(re.search(r"\b(because|therefore|so that|which led to)\b", text, re.I))
    causal_supported = bool(re.search(r"\b(because|therefore|so that|which led to)\b", source, re.I))
    absolute = bool(re.search(r"\b(always|never|only thing that mattered|the most important)\b", text, re.I))
    first = bool(re.search(r"\bI\b|\bmy\b|\bwe\b", text))
    third = bool(re.search(r"\bI\b|\bmy\b|\bwe\b", text))
    perspective_ok = first if _first_person(perspective) else not third
    reasons = []
    if unsupported_years: reasons.append("unsupported_date")
    if quoted and not quote_supported: reasons.append("unsupported_quote")
    if causal and not causal_supported: reasons.append("unsupported_causality")
    if absolute: reasons.append("unsupported_absolute_claim")
    if not perspective_ok: reasons.append("perspective_mismatch")
    if not text: reasons.append("empty_text")
    return {"accepted": not reasons, "reasons": reasons, "claim_classes": {"direct": "server_checked", "safe_implication": "provider_and_server_checked", "uncertain_conflict": "preserved_by_timeline", "unsupported": "rejected"}}


class DeterministicStoryProvider:
    model = "deterministic-story-v1"

    async def outline(self, legacy: Legacy, scope: str, perspective: str, facts: Sequence[dict]) -> StoryOutline:
        event_facts = [item for item in facts if item["kind"] == "timeline_event"]
        if not event_facts:
            event_facts = facts[:MAX_CHAPTERS]
        chapters = []
        for index, item in enumerate(event_facts[:MAX_CHAPTERS]):
            title = item.get("title") or ("Memories" if index == 0 else "More memories")
            chapters.append(OutlineChapter(title=_clean(title)[:255], memory_ids=(list(item.get("memory_ids", [])) if item["kind"] == "timeline_event" else [int(item["id"])] )[:8], event_ids=[item["id"]] if item["kind"] == "timeline_event" else []))
        return StoryOutline(chapters=chapters or [OutlineChapter(title="Preserved memories", memory_ids=[int(item["id"]) for item in facts if item["kind"] == "memory"][:8])])

    async def chapter(self, legacy: Legacy, perspective: str, chapter: OutlineChapter, facts: Sequence[dict], style: str = "") -> StoryChapterDraft:
        selected = [item for item in facts if item.get("id") in set(chapter.memory_ids + chapter.event_ids)]
        selected = selected[:8]
        subject = _subject(legacy)
        pieces = []
        for item in selected:
            sentence = _clean(item.get("text", ""))
            if not sentence: continue
            if _first_person(perspective):
                sentence = re.sub(rf"^{re.escape(subject)}\s+", "I ", sentence, flags=re.I)
                sentence = re.sub(r"\b[Pp]allavi moved\b", "I moved", sentence)
            pieces.append(sentence.rstrip(".!?") + ".")
        text = " ".join(pieces) or ("I remember parts of this period." if _first_person(perspective) else f"{subject}'s preserved memories for this period are limited.")
        return StoryChapterDraft(title=chapter.title, narrative_text=text[:MAX_CHAPTER_TEXT], memory_ids=chapter.memory_ids, event_ids=chapter.event_ids)


class OpenAIStoryProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openai_api_key: raise RuntimeError("story_provider_configuration")
        self.model = self.settings.ai_model
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def _json(self, instructions: str, input_text: str, name: str, schema: dict, model_type):
        response = await self.client.responses.create(model=self.model, instructions=instructions, input=input_text, reasoning={"effort": self.settings.ai_reasoning_effort}, text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}, store=False)
        return model_type.model_validate_json(response.output_text)

    async def outline(self, legacy, scope, perspective, facts):
        schema = {"type":"object","additionalProperties":False,"properties":{"chapters":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"object","additionalProperties":False,"properties":{"title":{"type":"string","minLength":1,"maxLength":255},"memory_ids":{"type":"array","maxItems":8,"items":{"type":"integer"}},"event_ids":{"type":"array","maxItems":8,"items":{"type":"string"}}},"required":["title","memory_ids","event_ids"]}}},"required":["chapters"]}
        instructions = "Create only an outline from DATA. Do not invent life stages, events, dates or relationships. IDs are opaque and may only be selected from the supplied data. Evidence is optional. Return a bounded outline."
        return await self._json(instructions, json.dumps({"subject": _subject(legacy), "scope": scope, "perspective": perspective, "facts": facts}, ensure_ascii=False), "legarya_l18_outline", schema, StoryOutline)

    async def chapter(self, legacy, perspective, chapter, facts, style=""):
        schema = {"type":"object","additionalProperties":False,"properties":{"title":{"type":"string","minLength":1,"maxLength":255},"narrative_text":{"type":"string","minLength":1,"maxLength":MAX_CHAPTER_TEXT},"memory_ids":{"type":"array","maxItems":8,"items":{"type":"integer"}},"event_ids":{"type":"array","maxItems":8,"items":{"type":"string"}}},"required":["title","narrative_text","memory_ids","event_ids"]}
        voice = "first person as the Legacy subject" if _first_person(perspective) else "third person about the Legacy subject"
        instructions = f"Write one bounded natural chapter in {voice}. Use only DATA. Preserve uncertainty and conflicts. Never invent motives, causality, feelings, dialogue, dates or relationships. Quotation marks require exact supported wording. Rya is not the subject. Style affects wording only. Return selected support IDs exactly."
        return await self._json(instructions, json.dumps({"subject": _subject(legacy), "perspective": perspective, "chapter": chapter.model_dump(), "facts": facts, "style": style[:2000]}, ensure_ascii=False), "legarya_l18_chapter", schema, StoryChapterDraft)


def get_story_provider() -> StoryProvider:
    return OpenAIStoryProvider()


def _serialize_support(db: Session, link: StorySupportLink) -> dict:
    return {"id": link.id, "kind": link.support_kind, "state": link.support_state, "memory_id": link.memory_id, "life_event_id": link.life_event_id, "evidence_id": link.evidence_id}


def serialize_story(db: Session, story: Story, *, include_text: bool = True) -> dict:
    version = db.scalar(select(StoryVersion).where(StoryVersion.legacy_id == story.legacy_id, StoryVersion.id == story.current_version_id)) if story.current_version_id else None
    chapters = []
    if version:
        for chapter in db.scalars(select(StoryChapter).where(StoryChapter.legacy_id == story.legacy_id, StoryChapter.story_version_id == version.id).order_by(StoryChapter.ordinal, StoryChapter.id)).all():
            item = {"id": chapter.id, "title": chapter.title, "ordinal": chapter.ordinal, "narrative_text": chapter.narrative_text if include_text else "", "generation_status": chapter.generation_status, "human_edited": chapter.human_edited, "audit_summary": chapter.audit_summary, "support": [_serialize_support(db, link) for link in db.scalars(select(StorySupportLink).where(StorySupportLink.legacy_id == story.legacy_id, StorySupportLink.chapter_id == chapter.id)).all()]}
            chapters.append(item)
    return {"id": story.id, "legacy_id": story.legacy_id, "title": story.title, "scope": story.scope, "narrative_perspective": story.narrative_perspective, "visibility": story.visibility, "lifecycle_state": story.lifecycle_state, "staleness_state": story.staleness_state, "staleness_reason": story.staleness_reason, "current_version_id": story.current_version_id, "created_at": story.created_at, "updated_at": story.updated_at, "current_version": ({"id": version.id, "version_number": version.version_number, "status": version.status, "human_edited": version.human_edited, "audit_summary": version.audit_summary, "created_at": version.created_at, "chapters": chapters} if version else None)}


def _support_rows(db: Session, story: Story, version: StoryVersion, chapter: StoryChapter, facts: Sequence[dict]):
    seen = set()
    for item in facts:
        kind, value = item.get("kind"), item.get("id")
        key = (kind, value)
        if key in seen: continue
        seen.add(key)
        kwargs = {"id": str(uuid4()), "legacy_id": story.legacy_id, "story_version_id": version.id, "chapter_id": chapter.id, "support_kind": {"memory":"memory", "timeline_event":"timeline_event"}[kind], "support_state": StorySupportState.AVAILABLE.value}
        if kind == "memory": kwargs["memory_id"] = value
        else: kwargs["life_event_id"] = value
        db.add(StorySupportLink(**kwargs))
    for item in facts:
        if item.get("kind") != "memory": continue
        links = db.scalars(select(MemorySourceLink).where(MemorySourceLink.legacy_id == story.legacy_id, MemorySourceLink.memory_id == item["id"], MemorySourceLink.support_state == SupportState.APPROVED.value, MemorySourceLink.removed_at.is_(None))).all()
        for link in links[:4]:
            evidence = db.scalar(select(SourceEvidence).where(SourceEvidence.legacy_id == story.legacy_id, SourceEvidence.id == link.evidence_id, SourceEvidence.removed_at.is_(None)))
            if evidence is not None:
                db.add(StorySupportLink(id=str(uuid4()), legacy_id=story.legacy_id, story_version_id=version.id, chapter_id=chapter.id, support_kind=StorySupportKind.SOURCE_EVIDENCE.value, evidence_id=evidence.id, support_state=StorySupportState.AVAILABLE.value, source_updated_at=evidence.created_at))


class StoryEngine:
    def __init__(self, db: Session, provider: StoryProvider | None = None):
        self.db, self.provider = db, provider or DeterministicStoryProvider()

    def create(self, legacy: Legacy, user_id: int, title: str, scope: str, perspective: str) -> Story:
        story = Story(id=str(uuid4()), legacy_id=legacy.id, title=_clean(title), scope=scope, narrative_perspective=perspective, visibility=StoryVisibility.DRAFT.value, lifecycle_state=StoryLifecycle.ACTIVE.value, staleness_state=StoryStaleness.CURRENT.value, created_by_user_id=user_id)
        self.db.add(story); self.db.flush(); return story

    async def generate(self, story: Story, legacy: Legacy, user_id: int, request_key: str) -> Story:
        # Serialize version allocation/current-pointer decisions per Story. The lock
        # is held only for this bounded request and makes duplicate keys deterministic.
        story = self.db.scalar(select(Story).where(Story.legacy_id == story.legacy_id, Story.id == story.id).with_for_update()) or story
        base_version_id = story.current_version_id
        existing = self.db.scalar(select(StoryVersion).where(StoryVersion.legacy_id == story.legacy_id, StoryVersion.story_id == story.id, StoryVersion.generation_request_key == request_key))
        if existing:
            story.current_version_id = existing.id if existing.status in {StoryVersionStatus.READY.value, StoryVersionStatus.ACCEPTED.value} else story.current_version_id
            if existing.status in {StoryVersionStatus.FAILED.value, StoryVersionStatus.AUDIT_FAILED.value}:
                raise ValueError("audit_failed" if existing.status == StoryVersionStatus.AUDIT_FAILED.value else "generation_failed")
            return story
        facts = _facts(self.db, legacy, story.scope)
        memory_objects = list(self.db.scalars(select(Memory).where(Memory.legacy_id == story.legacy_id, Memory.status == MemoryStatus.ACTIVE.value).limit(MAX_MEMORIES)).all())
        selected_style = select_personality_style(self.db, legacy, story.scope, memory_objects, {}, (), history_order="chronological")
        style_block = render_style_block(selected_style) if selected_style else ""
        number = (self.db.scalar(select(StoryVersion.version_number).where(StoryVersion.legacy_id == story.legacy_id, StoryVersion.story_id == story.id).order_by(StoryVersion.version_number.desc()).limit(1)) or 0) + 1
        version = StoryVersion(id=str(uuid4()), legacy_id=story.legacy_id, story_id=story.id, version_number=number, status=StoryVersionStatus.GENERATING.value, generation_request_key=request_key, provider_model=getattr(self.provider, "model", None), input_snapshot={"memory_ids": [x["id"] for x in facts if x["kind"] == "memory"], "event_ids": [x["id"] for x in facts if x["kind"] == "timeline_event"]}, created_by_user_id=user_id, generation_attempts=1)
        self.db.add(version); self.db.flush()
        try:
            outline = await self.provider.outline(legacy, story.scope, story.narrative_perspective, facts)
            allowed_memory = {x["id"] for x in facts if x["kind"] == "memory"}; allowed_events = {x["id"] for x in facts if x["kind"] == "timeline_event"}
            for index, item in enumerate(outline.chapters[:MAX_CHAPTERS]):
                item.memory_ids = [x for x in item.memory_ids if x in allowed_memory][:8]; item.event_ids = [x for x in item.event_ids if x in allowed_events][:8]
                selected = [x for x in facts if x["id"] in set(item.memory_ids + item.event_ids)]
                draft = await self.provider.chapter(legacy, story.narrative_perspective, item, selected, style_block)
                audit = audit_chapter(draft, story.narrative_perspective, selected)
                if not audit["accepted"]: raise ValueError("audit_failed:" + ",".join(audit["reasons"]))
                chapter = StoryChapter(id=str(uuid4()), legacy_id=story.legacy_id, story_version_id=version.id, title=_clean(draft.title), ordinal=index, narrative_text=_clean(draft.narrative_text), generation_status=StoryVersionStatus.READY.value, audit_summary=audit)
                self.db.add(chapter); self.db.flush(); _support_rows(self.db, story, version, chapter, selected)
            version.status = StoryVersionStatus.READY.value; version.audit_summary = {"accepted": True, "chapters": len(outline.chapters[:MAX_CHAPTERS])}
            # A newer owner edit/version must win even if this provider request was stale.
            current_now = self.db.scalar(select(Story.current_version_id).where(Story.legacy_id == story.legacy_id, Story.id == story.id))
            if current_now not in {base_version_id, None, version.id}:
                version.status = StoryVersionStatus.SUPERSEDED.value
            else:
                story.current_version_id = version.id; story.staleness_state = StoryStaleness.CURRENT.value; story.staleness_reason = None
            self.db.commit(); self.db.refresh(story); return story
        except Exception as exc:
            self.db.rollback()
            failed = StoryVersion(id=version.id, legacy_id=story.legacy_id, story_id=story.id, version_number=number, status=StoryVersionStatus.AUDIT_FAILED.value if str(exc).startswith("audit_failed:") else StoryVersionStatus.FAILED.value, generation_request_key=request_key, provider_model=getattr(self.provider, "model", None), policy_version="l18-grounded-v1", input_snapshot={"memory_ids": [x["id"] for x in facts if x["kind"] == "memory"], "event_ids": [x["id"] for x in facts if x["kind"] == "timeline_event"]}, audit_summary={"accepted": False}, failure_code="audit_failed" if str(exc).startswith("audit_failed:") else "generation_failed", generation_attempts=1, created_by_user_id=user_id)
            self.db.add(failed); self.db.commit()
            raise

    def edit_chapter(self, story: Story, chapter_id: str, user_id: int, title: str, text: str) -> Story:
        story = self.db.scalar(select(Story).where(Story.legacy_id == story.legacy_id, Story.id == story.id).with_for_update()) or story
        current = self.db.scalar(select(StoryVersion).where(StoryVersion.legacy_id == story.legacy_id, StoryVersion.id == story.current_version_id))
        if current is None: raise ValueError("story_has_no_version")
        source = self.db.scalar(select(StoryChapter).where(StoryChapter.legacy_id == story.legacy_id, StoryChapter.id == chapter_id, StoryChapter.story_version_id == current.id))
        if source is None: raise ValueError("chapter_not_found")
        number = (self.db.scalar(select(StoryVersion.version_number).where(StoryVersion.legacy_id == story.legacy_id, StoryVersion.story_id == story.id).order_by(StoryVersion.version_number.desc()).limit(1)) or 0) + 1
        version = StoryVersion(id=str(uuid4()), legacy_id=story.legacy_id, story_id=story.id, version_number=number, status=StoryVersionStatus.READY.value, human_edited=True, created_by_user_id=user_id, audit_summary={"accepted": True, "human_edited": True})
        self.db.add(version); self.db.flush()
        for item in self.db.scalars(select(StoryChapter).where(StoryChapter.story_version_id == current.id).order_by(StoryChapter.ordinal)).all():
            chapter = StoryChapter(id=str(uuid4()), legacy_id=story.legacy_id, story_version_id=version.id, title=title if item.id == chapter_id else item.title, ordinal=item.ordinal, narrative_text=text if item.id == chapter_id else item.narrative_text, generation_status=StoryVersionStatus.READY.value, human_edited=item.id == chapter_id or item.human_edited, audit_summary=item.audit_summary)
            self.db.add(chapter); self.db.flush()
            for link in self.db.scalars(select(StorySupportLink).where(StorySupportLink.chapter_id == item.id, StorySupportLink.legacy_id == story.legacy_id)).all():
                self.db.add(StorySupportLink(id=str(uuid4()), legacy_id=story.legacy_id, story_version_id=version.id, chapter_id=chapter.id, support_kind=link.support_kind, memory_id=link.memory_id, life_event_id=link.life_event_id, evidence_id=link.evidence_id, support_state=link.support_state, source_updated_at=link.source_updated_at))
        story.current_version_id = version.id; story.staleness_state = StoryStaleness.CURRENT.value; self.db.commit(); self.db.refresh(story); return story


def mark_story_support_stale(session, *, legacy_id: int, memory_ids=(), event_ids=(), evidence_ids=(), reason="support_changed"):
    """Called by the session hook after canonical/L16/L17 changes; never changes text."""
    conditions = []
    if memory_ids: conditions.append(StorySupportLink.memory_id.in_(list(memory_ids)))
    if event_ids: conditions.append(StorySupportLink.life_event_id.in_(list(event_ids)))
    if evidence_ids: conditions.append(StorySupportLink.evidence_id.in_(list(evidence_ids)))
    if not conditions: return
    connection = session.connection()
    version_ids = connection.execute(select(StorySupportLink.story_version_id).where(StorySupportLink.legacy_id == legacy_id, *conditions)).scalars().all()
    if not version_ids: return
    story_ids = connection.execute(select(StoryVersion.story_id).where(StoryVersion.legacy_id == legacy_id, StoryVersion.id.in_(version_ids))).scalars().all()
    connection.execute(update(Story).where(Story.legacy_id == legacy_id, Story.id.in_(story_ids)).values(staleness_state=StoryStaleness.STALE.value, staleness_reason=reason))
    if evidence_ids:
        connection.execute(update(StorySupportLink).where(StorySupportLink.legacy_id == legacy_id, StorySupportLink.evidence_id.in_(list(evidence_ids))).values(support_state=StorySupportState.UNAVAILABLE.value))


def _collect_support_changes(session, *_args):
    pending = session.info.setdefault("l18_support_changes", {"memory": set(), "event": set(), "evidence": set()})
    for item in set(session.new) | set(session.dirty) | set(session.deleted):
        state = inspect(item)
        if isinstance(item, Memory) and (item in session.new or item in session.deleted or state.modified):
            pending["memory"].add((item.legacy_id, item.id))
        elif isinstance(item, LifeEvent) and (item in session.new or item in session.deleted or state.modified):
            pending["event"].add((item.legacy_id, item.id))
        elif isinstance(item, SourceEvidence) and (item in session.new or item in session.deleted or state.modified):
            pending["evidence"].add((item.legacy_id, item.id))


def _apply_support_changes(session, *_args):
    pending = session.info.pop("l18_support_changes", None)
    if not pending: return
    for legacy_id in {value[0] for values in pending.values() for value in values if value[0] is not None}:
        mark_story_support_stale(session, legacy_id=legacy_id, memory_ids=[value[1] for value in pending["memory"] if value[0] == legacy_id], event_ids=[value[1] for value in pending["event"] if value[0] == legacy_id], evidence_ids=[value[1] for value in pending["evidence"] if value[0] == legacy_id], reason="support_changed")


def _clear_support_changes(session, *_args):
    session.info.pop("l18_support_changes", None)


if not event.contains(Session, "before_flush", _collect_support_changes):
    event.listen(Session, "before_flush", _collect_support_changes)
    event.listen(Session, "after_flush_postexec", _apply_support_changes)
    event.listen(Session, "after_rollback", _clear_support_changes)
    event.listen(Session, "after_soft_rollback", _clear_support_changes)

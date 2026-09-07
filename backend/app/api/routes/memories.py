from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.memory import Memory, MemoryEntityLink, MemoryRevision, MemoryStatus
from app.models.user import User
from app.schemas.memory import MemoryResponse, MemoryRevisionResponse, MemoryUpdate
from app.services.authorization import legacy_role, require_legacy
from app.services.memory import LivingMemoryService, MemoryProvider, MemoryProviderError, get_memory_provider, serialize_memory
from app.services.progression import local_date, record_builder_activity
from app.services.media_provenance import memory_provenance


router = APIRouter(prefix="/memories", tags=["Living memory"])


def _accessible_memory(db: Session, memory_id: int, legacy_id: int, user_id: int, require_active: bool = False) -> Memory:
    require_legacy(db, user_id, legacy_id)
    query = select(Memory).options(selectinload(Memory.entity_links).selectinload(MemoryEntityLink.entity), joinedload(Memory.contributor), joinedload(Memory.last_contributor)).execution_options(populate_existing=True).where(Memory.id == memory_id, Memory.legacy_id == legacy_id)
    if require_active: query = query.where(Memory.status == MemoryStatus.ACTIVE)
    memory = db.scalar(query)
    if memory is None: raise HTTPException(status_code=404, detail="Memory not found.")
    return memory


@router.get("", response_model=list[MemoryResponse])
def list_memories(legacy_id: int = Query(..., ge=1), include_inactive: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    query = select(Memory).options(selectinload(Memory.entity_links).selectinload(MemoryEntityLink.entity), joinedload(Memory.contributor), joinedload(Memory.last_contributor)).execution_options(populate_existing=True).where(Memory.legacy_id == legacy_id)
    if not include_inactive: query = query.where(Memory.status == MemoryStatus.ACTIVE)
    memories = db.scalars(query.order_by(Memory.category, Memory.created_at.desc(), Memory.id.desc())).all()
    provenance = memory_provenance(db, legacy, user.id, [memory.id for memory in memories])
    return [{**serialize_memory(memory), "source_provenance": provenance[memory.id]} for memory in memories]


@router.get("/{memory_id}", response_model=MemoryResponse)
def get_memory(memory_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memory = _accessible_memory(db, memory_id, legacy_id, user.id)
    legacy = require_legacy(db, user.id, legacy_id)
    return {**serialize_memory(memory), "source_provenance": memory_provenance(db, legacy, user.id, [memory.id])[memory.id]}


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def edit_memory(payload: MemoryUpdate, memory_id: int, legacy_id: int = Query(..., ge=1), timezone_name: str = Query(default="UTC", alias="timezone", max_length=64), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: MemoryProvider = Depends(get_memory_provider)):
    legacy = require_legacy(db, user.id, legacy_id)
    memory = _accessible_memory(db, memory_id, legacy_id, user.id, require_active=True)
    try:
        memory = await LivingMemoryService(provider).edit(db, legacy, memory, payload.canonical_text, payload.category, user.id)
    except ValueError as exc:
        db.rollback()
        if str(exc) == "duplicate_memory": raise HTTPException(status_code=409, detail="An equivalent active memory already exists.") from None
        if str(exc) == "memory_not_active": raise HTTPException(status_code=409, detail="This memory changed while the edit was being prepared.") from None
        raise
    except MemoryProviderError as exc:
        db.rollback(); raise HTTPException(status_code=503, detail={"code": exc.kind, "message": "Memory could not be updated right now."}) from None
    if getattr(memory, "was_changed", True):
        record_builder_activity(db, user_id=user.id, legacy_id=legacy.id, activity_type="edit", memory_id=memory.id, activity_date=local_date(timezone_name))
    return serialize_memory(_accessible_memory(db, memory.id, legacy_id, user.id))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: int, legacy_id: int = Query(..., ge=1), timezone_name: str = Query(default="UTC", alias="timezone", max_length=64), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    if legacy_role(db, user.id, legacy) != "owner":
        raise HTTPException(status_code=403, detail="Only the Legacy owner can delete memories.")
    memory = _accessible_memory(db, memory_id, legacy_id, user.id, require_active=True)
    LivingMemoryService.delete_memory(db, memory, user.id)
    record_builder_activity(db, user_id=user.id, legacy_id=legacy.id, activity_type="delete", memory_id=memory.id, activity_date=local_date(timezone_name))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{memory_id}/revisions", response_model=list[MemoryRevisionResponse])
def list_revisions(memory_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _accessible_memory(db, memory_id, legacy_id, user.id)
    return db.scalars(select(MemoryRevision).where(MemoryRevision.memory_id == memory_id).order_by(MemoryRevision.changed_at.desc(), MemoryRevision.id.desc())).all()

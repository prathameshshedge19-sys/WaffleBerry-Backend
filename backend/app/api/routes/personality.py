"""Read-only personality management, using existing memory-access authorization."""

from fastapi import APIRouter, Path
from app.api.routes import memories as _memory_routes
from app.schemas.personality_dashboard import PersonalityDashboard
from app.services.personality_dashboard import read_personality_dashboard

router = APIRouter(prefix="/api/v1/legacies", tags=["personality"])


@router.get("/{legacy_id}/personality", response_model=PersonalityDashboard)
def read_personality(legacy_id: int=Path(..., gt=0), include_inactive: bool=False, user: _memory_routes.User=_memory_routes.Depends(_memory_routes.get_current_user), db: _memory_routes.Session=_memory_routes.Depends(_memory_routes.get_db)):
    legacy = _memory_routes.require_legacy(db, user.id, legacy_id)
    return read_personality_dashboard(db, legacy)

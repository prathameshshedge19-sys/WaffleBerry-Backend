from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.progress import DailyPrompt
from app.models.user import User
from app.schemas.progress import BuilderJourneyResponse, DailyPromptResponse
from app.services.authorization import legacy_role, require_legacy
from app.services.progression import daily_prompt, legacy_progress, local_date, skip_daily_prompt, streak_summary


router = APIRouter(prefix="/progress", tags=["Legacy progression"])


@router.get("/{legacy_id}", response_model=BuilderJourneyResponse)
def get_builder_journey(
    legacy_id: int,
    timezone_name: str = Query(default="UTC", alias="timezone", max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    legacy = require_legacy(db, user.id, legacy_id)
    today = local_date(timezone_name)
    return {
        "role": legacy_role(db, user.id, legacy),
        "progress": legacy_progress(db, legacy.id),
        "streak": streak_summary(db, legacy.id, today),
        "daily_prompt": daily_prompt(db, legacy.id, legacy.subject_name, today),
    }


@router.post("/{legacy_id}/daily-prompt/{prompt_id}/skip", response_model=DailyPromptResponse)
def skip_prompt(
    legacy_id: int,
    prompt_id: int,
    timezone_name: str = Query(default="UTC", alias="timezone", max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    legacy = require_legacy(db, user.id, legacy_id)
    prompt = db.get(DailyPrompt, prompt_id)
    if prompt is None or prompt.legacy_id != legacy.id:
        raise HTTPException(status_code=404, detail="Daily question not found.")
    skip_daily_prompt(db, prompt)
    return daily_prompt(db, legacy.id, legacy.subject_name, local_date(timezone_name))

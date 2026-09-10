"""Read-only self-account usage. No plan change, checkout or enforcement API."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.plan_usage import snapshot

router = APIRouter(prefix="/plans", tags=["Plan usage (shadow)"])


@router.get("/usage")
def my_usage(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return snapshot(db, user.id)
    except Exception:
        db.rollback()
        raise HTTPException(503, detail={"code": "usage_temporarily_unavailable",
            "message": "Usage information is temporarily unavailable. Your other features are unchanged."},
            headers={"Cache-Control": "private, no-store"}) from None

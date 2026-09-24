"""Authenticated account-level deletion. No email/user-id deletion endpoint."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.api.dependencies import bearer, get_current_user
from app.api.routes.auth import REFRESH_COOKIE, _refresh_cookie_samesite
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services.account_deletion import reauthenticate, request_account_deletion
from app.services.security import TokenValidationError, decode_token


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default validation errors echo input values.
                raise HTTPException(422, detail="Invalid account-deletion request.") from None
        return private_handler


router = APIRouter(prefix="/account", tags=["account"], route_class=PrivateValidationRoute)


class ReauthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = Field(default=None, min_length=1, max_length=128)
    credential: SecretStr | None = Field(default=None, min_length=20, max_length=8192)


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(pattern="^DELETE$")
    reauth_token: SecretStr = Field(min_length=40, max_length=128)


@router.get("/deletion")
def deletion_information(response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"status": "active", "confirmation": "DELETE", "google_available": bool(user.google_sub),
            "reauth_required": True}


@router.post("/deletion/reauth")
def deletion_reauth(payload: ReauthRequest, response: Response, user: User = Depends(get_current_user),
                    credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    proof = reauthenticate(db, user.id, credentials.credentials,
        password=payload.password.get_secret_value() if payload.password else None,
        credential=payload.credential.get_secret_value() if payload.credential else None)
    response.headers["Cache-Control"] = "no-store"
    return {"reauth_token": proof, "expires_in": 300}


@router.post("/deletion", status_code=202)
def delete_account(payload: DeleteRequest, response: Response,
                   credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    # Only this endpoint accepts an old access JWT while DELETING, and only
    # with the exact already-consumed proof/session pair for an idempotent retry.
    try:
        user_id = decode_token(credentials.credentials if credentials else "", "access")["user_id"]
    except TokenValidationError:
        raise HTTPException(401, detail="Could not validate credentials.") from None
    request_account_deletion(db, user_id, proof=payload.reauth_token.get_secret_value(),
                             access_token=credentials.credentials)
    db.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth", secure=not get_settings().legarya_debug,
                           httponly=True, samesite=_refresh_cookie_samesite())
    response.headers["Cache-Control"] = "no-store"
    return {"status": "deleting", "message": "Your account is unavailable. Permanent deletion is in progress."}

"""Authentication dependencies for protected API endpoints."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.crud.user import UserCRUD
from app.db import get_db
from app.models.user import User
from app.services.token_service import TokenValidationError, decode_access_token


bearer_scheme = HTTPBearer(auto_error=False)


def _credentials_exception() -> HTTPException:
    """Build the generic response used for invalid authentication."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return the database user identified by a valid Bearer token."""
    if not credentials:
        raise _credentials_exception()

    token = credentials.credentials

    try:
        user_id = decode_access_token(token)
    except TokenValidationError:
        raise _credentials_exception() from None

    user = UserCRUD.get_user(db, user_id)
    if not user:
        raise _credentials_exception()

    return user

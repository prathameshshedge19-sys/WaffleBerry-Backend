"""Atomic Google identity linking and WaffleBerry account creation."""
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.user import hash_password
from app.models.user import PlanTier, User
from app.services.google_identity_service import GoogleIdentity


class GoogleAccountConflictError(Exception):
    pass


class GoogleTermsRequiredError(Exception):
    pass


def _by_sub(db: Session, sub: str) -> User | None:
    return db.query(User).filter(User.google_sub == sub).first()


def _by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.strip().casefold()).first()


def _resolved_existing(db: Session, identity: GoogleIdentity) -> User | None:
    sub_user = _by_sub(db, identity.sub)
    email_user = _by_email(db, identity.email)
    if sub_user:
        if email_user is not None and email_user.user_id != sub_user.user_id:
            raise GoogleAccountConflictError
        return sub_user
    if email_user:
        if email_user.google_sub not in (None, identity.sub):
            raise GoogleAccountConflictError
        return email_user
    return None


def _display_name(identity: GoogleIdentity) -> str:
    combined = " ".join(part for part in (identity.given_name, identity.family_name) if part).strip()
    return (identity.name or combined or identity.email.split("@", 1)[0] or "WaffleBerry User")[:255]


def resolve_google_account(db: Session, identity: GoogleIdentity, *, accepted_terms: bool) -> User:
    """Resolve/link/create exactly one user and commit atomically."""
    try:
        user = _resolved_existing(db, identity)
        if user is not None:
            if user.google_sub is None:
                user.google_sub = identity.sub
                if not user.is_verified:
                    user.is_verified = True
                db.flush()
            db.commit()
            db.refresh(user)
            return user
        if not accepted_terms:
            db.rollback()
            raise GoogleTermsRequiredError
        user = User(
            full_name=_display_name(identity), email=identity.email.strip().casefold(),
            password_hash=hash_password(secrets.token_urlsafe(48)),
            is_verified=True, plan=PlanTier.FREE, quota_exempt=False,
            timezone="UTC", google_sub=identity.sub,
        )
        db.add(user)
        db.flush()
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        user = _resolved_existing(db, identity)
        if user is not None and user.google_sub == identity.sub:
            return user
        raise GoogleAccountConflictError from None
    except (GoogleAccountConflictError, GoogleTermsRequiredError):
        db.rollback()
        raise

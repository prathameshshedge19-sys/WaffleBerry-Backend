import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth_challenge import AuthChallenge
from app.services.security import normalize_email


AUTHORIZATION_LIFETIME = timedelta(minutes=15)
MAX_ATTEMPTS = 5


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_challenge(db: Session, *, email: str, purpose: str, full_name: str | None = None) -> str:
    normalized = normalize_email(email)
    existing = db.scalars(select(AuthChallenge).where(AuthChallenge.email == normalized, AuthChallenge.purpose == purpose, AuthChallenge.is_consumed.is_(False))).all()
    for challenge in existing:
        challenge.is_consumed = True
    otp = f"{secrets.randbelow(900000) + 100000}"
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=get_settings().auth_code_expire_minutes
    )
    db.add(AuthChallenge(email=normalized, purpose=purpose, full_name=full_name, otp_hash=_digest(otp), expires_at=expires_at))
    db.commit()
    return otp


def verify_challenge(db: Session, *, email: str, purpose: str, otp: str) -> str | None:
    challenge = db.scalar(select(AuthChallenge).where(AuthChallenge.email == normalize_email(email), AuthChallenge.purpose == purpose).order_by(AuthChallenge.id.desc()))
    now = datetime.now(timezone.utc)
    if not challenge or challenge.is_consumed or _aware(challenge.expires_at) <= now or challenge.attempt_count >= MAX_ATTEMPTS:
        return None
    if not hmac.compare_digest(challenge.otp_hash, _digest(otp)):
        challenge.attempt_count += 1
        if challenge.attempt_count >= MAX_ATTEMPTS:
            challenge.is_consumed = True
        db.commit()
        return None
    authorization = secrets.token_urlsafe(32)
    challenge.is_consumed = True
    challenge.authorization_hash = _digest(authorization)
    challenge.authorization_expires_at = now + AUTHORIZATION_LIFETIME
    db.commit()
    return authorization


def consume_authorization(db: Session, *, authorization: str, purpose: str, email: str | None = None) -> AuthChallenge | None:
    challenge = db.scalar(select(AuthChallenge).where(AuthChallenge.authorization_hash == _digest(authorization), AuthChallenge.purpose == purpose))
    now = datetime.now(timezone.utc)
    if not challenge or challenge.authorization_used or not challenge.authorization_expires_at or _aware(challenge.authorization_expires_at) <= now:
        return None
    if email and challenge.email != normalize_email(email):
        return None
    challenge.authorization_used = True
    return challenge

"""Purpose-bound, rate-limited OTP challenge operations."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.models.auth_challenge import AuthChallenge


EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"
VALID_PURPOSES = {EMAIL_VERIFICATION, PASSWORD_RESET}
OTP_LIFETIME = timedelta(minutes=10)
AUTHORIZATION_LIFETIME = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _digest(value: str) -> str:
    secret = get_settings().jwt_secret_key.encode()
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


class AuthChallengeService:
    @staticmethod
    def issue(db, *, email: str, purpose: str, full_name: str | None = None,
              enforce_cooldown: bool = True) -> tuple[AuthChallenge, str]:
        if purpose not in VALID_PURPOSES:
            raise ValueError("Unsupported OTP purpose")
        email = normalize_email(email)
        latest = (
            db.query(AuthChallenge)
            .filter(AuthChallenge.email == email, AuthChallenge.purpose == purpose)
            .order_by(AuthChallenge.challenge_id.desc())
            .first()
        )
        now = datetime.now(timezone.utc)
        if (enforce_cooldown and latest and latest.created_at and
                now - _utc(latest.created_at) < RESEND_COOLDOWN):
            raise RuntimeError("cooldown")

        db.query(AuthChallenge).filter(
            AuthChallenge.email == email,
            AuthChallenge.purpose == purpose,
            AuthChallenge.is_consumed.is_(False),
        ).update({"is_consumed": True}, synchronize_session=False)

        otp = f"{secrets.randbelow(900000) + 100000}"
        challenge = AuthChallenge(
            email=email,
            full_name=full_name.strip() if full_name else None,
            purpose=purpose,
            otp_hash=_digest(otp),
            expires_at=now + OTP_LIFETIME,
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return challenge, otp

    @staticmethod
    def verify(db, *, email: str, otp: str, purpose: str) -> tuple[str, str | None]:
        challenge = (
            db.query(AuthChallenge)
            .filter(
                AuthChallenge.email == normalize_email(email),
                AuthChallenge.purpose == purpose,
            )
            .order_by(AuthChallenge.challenge_id.desc())
            .first()
        )
        now = datetime.now(timezone.utc)
        if not challenge or challenge.is_consumed:
            return "invalid", None
        if _utc(challenge.expires_at) <= now:
            challenge.is_consumed = True
            db.commit()
            return "expired", None
        if challenge.attempt_count >= MAX_ATTEMPTS:
            challenge.is_consumed = True
            db.commit()
            return "locked", None
        if not hmac.compare_digest(challenge.otp_hash, _digest(otp)):
            challenge.attempt_count += 1
            if challenge.attempt_count >= MAX_ATTEMPTS:
                challenge.is_consumed = True
            db.commit()
            return "invalid", None

        authorization = secrets.token_urlsafe(32)
        challenge.is_consumed = True
        challenge.authorization_hash = _digest(authorization)
        challenge.authorization_expires_at = now + AUTHORIZATION_LIFETIME
        db.commit()
        return "verified", authorization

    @staticmethod
    def consume_authorization(db, *, authorization: str, purpose: str,
                              email: str | None = None) -> AuthChallenge | None:
        challenge = db.query(AuthChallenge).filter(
            AuthChallenge.authorization_hash == _digest(authorization),
            AuthChallenge.purpose == purpose,
        ).first()
        now = datetime.now(timezone.utc)
        if (not challenge or challenge.authorization_used or
                not challenge.authorization_expires_at or
                _utc(challenge.authorization_expires_at) <= now or
                (email and challenge.email != normalize_email(email))):
            return None
        challenge.authorization_used = True
        return challenge

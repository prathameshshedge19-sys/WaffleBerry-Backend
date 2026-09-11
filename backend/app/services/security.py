import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


class TokenValidationError(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (TypeError, ValueError):
        return False


def _fingerprint(user_id: int, password_hash: str) -> str:
    secret = get_settings().jwt_secret_key.encode()
    return hmac.new(secret, f"{user_id}:{password_hash}".encode(), hashlib.sha256).hexdigest()


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "purpose": "access", "iat": now, "exp": now + timedelta(minutes=settings.access_token_expire_minutes)}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, password_hash: str, *, remember_me: bool = False) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire_days = (
        settings.remembered_refresh_token_expire_days
        if remember_me
        else settings.refresh_token_expire_days
    )
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": "refresh",
            "fingerprint": _fingerprint(user_id, password_hash),
            "remember_me": remember_me,
            # Ensure every successful refresh rotates the cookie even when two
            # tokens are issued within the same one-second JWT timestamp tick.
            "jti": secrets.token_urlsafe(24),
            "iat": now,
            "exp": now + timedelta(days=expire_days),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str, purpose: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm], options={"require": ["sub", "purpose", "iat", "exp"]})
        if payload["purpose"] != purpose:
            raise TokenValidationError
        payload["user_id"] = int(payload["sub"])
        return payload
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise TokenValidationError from None


def refresh_token_matches(user_id: int, password_hash: str, fingerprint: str) -> bool:
    return hmac.compare_digest(_fingerprint(user_id, password_hash), fingerprint)

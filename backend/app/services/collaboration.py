import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.legacy import Legacy, LegacySetupStatus


CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
INVALID_CODE_MESSAGE = "That collaborator code isn't valid."


def normalize_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def format_code(normalized: str) -> str:
    payload = normalized[3:] if normalized.startswith("COL") else normalized
    return f"COL-{payload[:4]}-{payload[4:8]}"


def _key_material() -> bytes:
    return get_settings().jwt_secret_key.encode("utf-8")


def code_digest(value: str) -> str:
    return hmac.new(_key_material(), normalize_code(value).encode("ascii"), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_key_material() + b":legarya:l5:collaborator-code").digest())
    return Fernet(key)


def encrypt_code(code: str) -> str:
    return _fernet().encrypt(code.encode("ascii")).decode("ascii")


def decrypt_code(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("ascii")
    except InvalidToken:
        return None


def rotate_code(db: Session, legacy: Legacy) -> str:
    for _ in range(12):
        raw = "COL" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        digest = code_digest(raw)
        if db.scalar(select(Legacy.id).where(Legacy.collaborator_code_digest == digest)) is None:
            code = format_code(raw)
            legacy.collaborator_code_digest = digest
            legacy.collaborator_code_ciphertext = encrypt_code(code)
            legacy.collaborator_code_hint = f"COL-••••-{raw[-4:]}"
            legacy.collaborator_code_enabled = True
            legacy.collaborator_code_rotated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(legacy)
            return code
    raise RuntimeError("Unable to generate a unique collaborator code.")


def legacy_for_code(db: Session, code: str) -> Legacy | None:
    normalized = normalize_code(code)
    if len(normalized) != 11 or not normalized.startswith("COL"):
        return None
    return db.scalar(
        select(Legacy).where(
            Legacy.collaborator_code_digest == code_digest(normalized),
            Legacy.collaborator_code_enabled.is_(True),
            Legacy.setup_status == LegacySetupStatus.ACTIVE.value,
        )
    )


class JoinAttemptLimiter:
    def __init__(self, maximum: int = 8, window_seconds: int = 60):
        self.maximum = maximum
        self.window_seconds = window_seconds
        self._attempts: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        attempts = self._attempts[user_id]
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        if len(attempts) >= self.maximum:
            return False
        attempts.append(now)
        return True

    def clear(self, user_id: int) -> None:
        self._attempts.pop(user_id, None)


join_attempt_limiter = JoinAttemptLimiter()


def join_legacy(db: Session, legacy: Legacy, user_id: int) -> tuple[str, LegacyCollaborator | None]:
    if legacy.owner_user_id == user_id:
        return "owner", None
    membership = db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.legacy_id == legacy.id, LegacyCollaborator.user_id == user_id))
    if membership:
        if membership.status == CollaboratorStatus.REVOKED.value:
            return "revoked", membership
        return "collaborator", membership
    membership = LegacyCollaborator(legacy_id=legacy.id, user_id=user_id, added_by_user_id=legacy.owner_user_id)
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return "collaborator", membership

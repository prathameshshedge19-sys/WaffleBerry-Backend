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
from app.models.legacy import Legacy, LegacySetupStatus
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus
from app.services.collaboration import CODE_ALPHABET, normalize_code


INVALID_VIEWER_CODE_MESSAGE = "That Legacy code isn't valid."


def _key_material() -> bytes:
    return get_settings().jwt_secret_key.encode("utf-8")


def viewer_code_digest(value: str) -> str:
    return hmac.new(_key_material() + b":viewer", normalize_code(value).encode("ascii"), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_key_material() + b":legarya:l6:viewer-code").digest())
    return Fernet(key)


def encrypt_viewer_code(code: str) -> str:
    return _fernet().encrypt(code.encode("ascii")).decode("ascii")


def decrypt_viewer_code(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("ascii")
    except InvalidToken:
        return None


def rotate_viewer_code(db: Session, legacy: Legacy) -> str:
    for _ in range(12):
        raw = "LEG" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        digest = viewer_code_digest(raw)
        if db.scalar(select(Legacy.id).where(Legacy.viewer_code_digest == digest)) is None:
            code = f"LEG-{raw[3:7]}-{raw[7:11]}"
            legacy.viewer_code_digest = digest
            legacy.viewer_code_ciphertext = encrypt_viewer_code(code)
            legacy.viewer_code_hint = f"LEG-••••-{raw[-4:]}"
            legacy.viewer_code_enabled = True
            legacy.viewer_code_rotated_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(legacy)
            return code
    raise RuntimeError("Unable to generate a unique Legacy code.")


def legacy_for_viewer_code(db: Session, code: str) -> Legacy | None:
    normalized = normalize_code(code)
    if len(normalized) != 11 or not normalized.startswith("LEG") or not normalized.isascii():
        return None
    return db.scalar(select(Legacy).where(
        Legacy.viewer_code_digest == viewer_code_digest(normalized),
        Legacy.viewer_code_enabled.is_(True),
        Legacy.setup_status.in_((LegacySetupStatus.COLLECTING_IDENTITY.value, LegacySetupStatus.ACTIVE.value)),
        Legacy.deletion_requested_at.is_(None),
    ))


def grant_viewer_access(db: Session, legacy: Legacy, user_id: int) -> LegacyViewerAccess:
    access = db.scalar(select(LegacyViewerAccess).where(LegacyViewerAccess.legacy_id == legacy.id, LegacyViewerAccess.user_id == user_id))
    if access is None:
        access = LegacyViewerAccess(legacy_id=legacy.id, user_id=user_id, status=ViewerAccessStatus.ACTIVE.value, granted_via_code=True)
        db.add(access); db.commit(); db.refresh(access)
    return access


class ViewerCodeAttemptLimiter:
    def __init__(self, maximum: int = 8, window_seconds: int = 60):
        self.maximum = maximum; self.window_seconds = window_seconds
        self._attempts: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic(); attempts = self._attempts[user_id]
        while attempts and attempts[0] <= now - self.window_seconds: attempts.popleft()
        if len(attempts) >= self.maximum: return False
        attempts.append(now); return True

    def clear(self, user_id: int) -> None:
        self._attempts.pop(user_id, None)


viewer_code_attempt_limiter = ViewerCodeAttemptLimiter()

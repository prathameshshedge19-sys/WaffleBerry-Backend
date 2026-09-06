"""Loopback-only manual microphone acceptance against a disposable local DB.

For a fresh fixture, set L15_LOCAL_TEST_PASSWORD in the launching environment.
Run explicitly from backend. Uses the configured server-side provider key,
but never the configured application database. No deployed configuration edits.
"""
from pathlib import Path
import os
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FOLDER = ROOT.parents[1] / "backups" / "l15-phase-c"
FOLDER.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + (FOLDER / "manual-microphone.sqlite3").as_posix()
os.environ["REALTIME_ENABLED"] = "true"
os.environ["CORS_ORIGINS"] = "http://127.0.0.1:5500,http://localhost:5500"
os.environ["JWT_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["LEGARYA_DEBUG"] = "true"  # Loopback fixture permits its local refresh cookie.

if __name__ == "__main__":
    from alembic import command
    from alembic.config import Config
    from app.database import SessionLocal
    from app.models.user import User
    from app.models.legacy import Legacy
    from app.services.security import hash_password
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with SessionLocal() as db:
        if db.get(User, 1) is None:
            db.add(User(id=1, full_name="Local microphone test", email="l15-mic@example.com",
                        password_hash=hash_password(os.environ["L15_LOCAL_TEST_PASSWORD"]), is_verified=True))
            db.flush()
            db.add(Legacy(id=1, owner_user_id=1, subject_name="Disposable mother", relationship_to_owner="mother",
                          setup_status="active", is_self=False))
            db.commit()
    import uvicorn
    (FOLDER / "manual-server-pid.txt").write_text(str(os.getpid()))
    uvicorn.run("app.main:app", host="127.0.0.1", port=8100, access_log=False)

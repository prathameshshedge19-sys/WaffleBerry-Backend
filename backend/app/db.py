"""Database configuration and session management."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


settings = get_settings()
DATABASE_URL = settings.database_url

engine_options = {}
if DATABASE_URL.startswith("sqlite"):
    engine_options = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }

engine = create_engine(DATABASE_URL, **engine_options)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Ensure declared SQLite foreign keys and cascades are enforced."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


if DATABASE_URL.startswith("sqlite"):
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for ORM models
Base = declarative_base()


def get_db():
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

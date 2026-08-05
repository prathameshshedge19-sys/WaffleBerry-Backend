"""Database configuration and session management."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from pathlib import Path

settings = get_settings()
DATABASE_URL = settings.database_url

print("DATABASE_URL:", DATABASE_URL)
print("Current working directory:", Path.cwd())

engine_options = {}
if DATABASE_URL.startswith("sqlite"):
    engine_options = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }

engine = create_engine(DATABASE_URL, **engine_options)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for ORM models
Base = declarative_base()


def ensure_schema():
    """Apply lightweight local schema upgrades not handled by create_all."""
    if engine.dialect.name != "sqlite":
        return

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("verification")
    }

    if "purpose" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE verification ADD COLUMN purpose "
                    "VARCHAR(50) NOT NULL DEFAULT 'email_verification'"
                )
            )


def get_db():
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

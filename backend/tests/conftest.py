"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db import Base
from app.config import Settings

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
def settings_test():
    """Create test settings with in-memory SQLite."""
    return Settings(
        _env_file=None,
        jwt_secret_key="test-secret-key-for-testing",
        database_url="sqlite:///:memory:",
        debug=True,
        email_provider="console",
    )


@pytest.fixture
def engine(settings_test):
    """Create SQLAlchemy engine for testing."""
    engine = create_engine(
        settings_test.database_url,
        connect_args={"check_same_thread": False} if "sqlite" in settings_test.database_url else {},
    )
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(engine) -> Session:
    """Create a fresh database session for each test."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def db(db_session):
    """Alias for db_session for convenience."""
    return db_session

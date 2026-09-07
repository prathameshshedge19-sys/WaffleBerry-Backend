import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

BACKEND = Path(__file__).parents[1]


def alembic(db_path, *arguments):
    env = dict(os.environ)
    env.update(DATABASE_URL="sqlite:///" + db_path.as_posix(), LEGARYA_DEBUG="true", JWT_SECRET_KEY="l13-disposable-migration-test-secret", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


@pytest.mark.parametrize("from_0013", [False, True])
def test_disposable_fresh_and_existing_0013_upgrade(tmp_path, from_0013):
    path = tmp_path / "l13-migration.db"
    if from_0013:
        alembic(path, "upgrade", "0013_voice_preference")
        assert "0013_voice_preference" in alembic(path, "current")
        engine = sa.create_engine("sqlite:///" + path.as_posix())
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO users (full_name, email, password_hash, is_verified) VALUES ('Migration sentinel', 'l13-migration@example.com', 'not-a-real-password', 0)")
        engine.dispose()
    alembic(path, "upgrade", "head")
    assert "0017_media_sources (head)" in alembic(path, "current")
    heads = alembic(path, "heads")
    assert heads.count("(head)") == 1 and "0017_media_sources" in heads
    engine = sa.create_engine("sqlite:///" + path.as_posix())
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "legacy_personality_profiles" in inspector.get_table_names()
        assert inspector.get_pk_constraint("legacy_personality_profiles")["constrained_columns"] == ["legacy_id"]
        assert inspector.get_foreign_keys("legacy_personality_profiles")[0]["referred_table"] == "legacies"
        if from_0013:
            assert connection.exec_driver_sql("SELECT full_name FROM users WHERE email='l13-migration@example.com'").scalar() == "Migration sentinel"
    engine.dispose()


def test_additive_migration_compiles_postgresql_sql(monkeypatch):
    path = BACKEND / "alembic/versions/0014_legacy_personality.py"
    spec = importlib.util.spec_from_file_location("personality_migration_l13", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(module, "op", Operations(context))
    assert module.down_revision == "0013_voice_preference"
    module.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE legacy_personality_profiles" in sql
    assert "REFERENCES legacies (id) ON DELETE CASCADE" in sql
    assert "DROP " not in sql and "ALTER TABLE users" not in sql

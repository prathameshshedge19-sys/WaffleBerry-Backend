import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration(connection, monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/0012_legacy_conversation_mode.py"
    spec = importlib.util.spec_from_file_location("mode_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
    return module


@pytest.mark.parametrize("old_constraint", [True, False])
def test_upgrade_preserves_builder_and_allows_only_valid_modes(monkeypatch, old_constraint):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        check = ", CONSTRAINT ck_conversations_mode CHECK (mode IN ('rya'))" if old_constraint else ""
        connection.exec_driver_sql(f"CREATE TABLE conversations (id INTEGER PRIMARY KEY, mode VARCHAR(24) NOT NULL{check})")
        connection.exec_driver_sql("INSERT INTO conversations VALUES (1, 'rya')")
        if old_constraint:
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql("INSERT INTO conversations VALUES (2, 'legacy')")
        module = migration(connection, monkeypatch)
        module.upgrade()
        connection.exec_driver_sql("INSERT INTO conversations VALUES (2, 'legacy')")
        assert connection.exec_driver_sql("SELECT mode FROM conversations ORDER BY id").scalars().all() == ["rya", "legacy"]
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("INSERT INTO conversations VALUES (3, 'invalid')")
        with pytest.raises(RuntimeError, match="Legacy conversations exist"):
            module.downgrade()
        assert connection.exec_driver_sql("SELECT count(*) FROM conversations").scalar() == 2
    engine.dispose()

import pytest
import sqlalchemy as sa
from tests.test_personality_migration_l13 import alembic


def test_additive_shadow_migration_preserves_existing_rows(tmp_path):
    path = tmp_path / "plans.db"
    alembic(path, "upgrade", "0022_legacy_deletion")
    engine = sa.create_engine("sqlite:///" + path.as_posix())
    with engine.begin() as db:
        db.exec_driver_sql("INSERT INTO users(id,full_name,email,password_hash) VALUES(1,'Synthetic','synthetic@example.com','test')")
        before = db.exec_driver_sql("SELECT * FROM users").fetchall()
    alembic(path, "upgrade", "head")
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT * FROM users").fetchall() == before
        assert db.exec_driver_sql("SELECT count(*) FROM plan_usage").scalar() == 0
        assert db.exec_driver_sql("SELECT count(*) FROM plan_entitlements").scalar() == 0
        assert db.exec_driver_sql("SELECT count(*) FROM plan_tracking_state").scalar() == 1
        assert db.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(AssertionError, match="preserve plan accounting tables"):
        alembic(path, "downgrade", "0022_legacy_deletion")
    engine.dispose()

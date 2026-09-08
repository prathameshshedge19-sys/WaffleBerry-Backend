import importlib.util
import io

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker

from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.user import User
from tests.test_personality_migration_l13 import alembic, BACKEND


def snapshot(connection):
    names = sa.inspect(connection).get_table_names()
    return {name: connection.exec_driver_sql('SELECT * FROM "' + name + '"').fetchall()
            for name in names if name not in {"alembic_version", "conversation_turns", "turn_effects"}}


@pytest.mark.parametrize("existing", [False, True])
def test_fresh_and_0014_preserve_all_existing_rows(tmp_path, existing):
    path = tmp_path / "turn-migration.db"
    engine = sa.create_engine("sqlite:///" + path.as_posix())
    before = None
    if existing:
        alembic(path, "upgrade", "0014_legacy_personality")
        with sessionmaker(bind=engine).begin() as db:
            db.add(User(id=1, full_name="Historical owner", email="old@example.com", password_hash="test"))
            db.flush()
            # Seed the historical schema without newer model columns.
            db.execute(sa.text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (1, 1, 'Historical subject', 'active')"))
            db.flush()
            db.add(Conversation(id=1, user_id=1, legacy_id=1, mode="rya", title="Historical conversation"))
            db.flush()
            db.add_all([Message(conversation_id=1, role=MessageRole.USER, content="Historical user message"),
                        Message(conversation_id=1, role=MessageRole.ASSISTANT, content="Historical assistant message")])
        with engine.connect() as connection:
            before = snapshot(connection)
    # Pin this historical migration test; L15 separately tests the current head.
    alembic(path, "upgrade", "0015_conversation_turns")
    assert "0015_conversation_turns" in alembic(path, "current")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert {"conversation_turns", "turn_effects"} <= set(inspector.get_table_names())
        assert connection.exec_driver_sql("SELECT count(*) FROM conversation_turns").scalar() == 0
        assert connection.exec_driver_sql("SELECT count(*) FROM turn_effects").scalar() == 0
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert {c["name"] for c in inspector.get_columns("messages")} == {"id", "conversation_id", "role", "content", "created_at"}
        if existing:
            assert snapshot(connection) == before
    alembic(path, "downgrade", "0014_legacy_personality")
    with engine.connect() as connection:
        assert "conversation_turns" not in sa.inspect(connection).get_table_names()
        if existing:
            assert snapshot(connection) == before
    engine.dispose()


def test_migration_compiles_postgresql_constraints(monkeypatch):
    path = BACKEND / "alembic/versions/0015_conversation_turns.py"
    spec = importlib.util.spec_from_file_location("turn_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(module, "op", Operations(context))
    assert module.down_revision == "0014_legacy_personality"
    module.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE conversation_turns" in sql and "CREATE TABLE turn_effects" in sql
    assert "UNIQUE (actor_user_id, conversation_id, client_turn_id)" in sql
    assert "REFERENCES messages (id) ON DELETE CASCADE" in sql
    assert "DROP " not in sql and "ALTER TABLE messages" not in sql

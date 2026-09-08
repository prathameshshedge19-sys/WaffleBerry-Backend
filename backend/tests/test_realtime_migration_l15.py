import importlib.util
import io

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_personality_migration_l13 import alembic, BACKEND


@pytest.mark.parametrize("existing", [False, True])
def test_0016_fresh_upgrade_downgrade_reupgrade_preserves_history(tmp_path, existing):
    path = tmp_path / "realtime.db"
    engine = sa.create_engine("sqlite:///" + path.as_posix())
    if existing:
        alembic(path, "upgrade", "0015_conversation_turns")
        with engine.begin() as c:
            c.exec_driver_sql("INSERT INTO users(id,full_name,email,password_hash) VALUES(1,'Historical','old@example.com','test')")
            c.exec_driver_sql("INSERT INTO legacies(id,owner_user_id,subject_name,setup_status) VALUES(1,1,'Historical','active')")
            c.exec_driver_sql("INSERT INTO conversations(id,user_id,legacy_id,mode,title) VALUES(1,1,1,'rya','Historical')")
            c.exec_driver_sql("INSERT INTO messages(conversation_id,role,content) VALUES(1,'user','Preserve this message')")
        with engine.connect() as c:
            before = snapshot(c)
    alembic(path, "upgrade", "head")
    assert "0022_legacy_deletion (head)" in alembic(path, "current")
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert c.exec_driver_sql("SELECT count(*) FROM realtime_sessions").scalar() == 0
        assert "ticket_hash" in {x["name"] for x in sa.inspect(c).get_columns("realtime_sessions")}
        if existing: assert snapshot(c) == before
    alembic(path,"downgrade","0015_conversation_turns")
    with engine.connect() as c:
        assert "realtime_sessions" not in sa.inspect(c).get_table_names()
        if existing: assert snapshot(c) == before
    alembic(path,"upgrade","head")
    engine.dispose()


def snapshot(connection):
    def rows(name):
        columns = [column["name"] for column in sa.inspect(connection).get_columns(name)
                   if not (name == "legacies" and column["name"] == "deletion_requested_at")]
        return connection.exec_driver_sql('SELECT '+','.join('"'+c+'"' for c in columns)+' FROM "'+name+'"').fetchall()
    return {name: rows(name)
            for name in sa.inspect(connection).get_table_names() if name not in {"alembic_version", "realtime_sessions", "media_sources", "media_artifacts", "media_processing_jobs", "source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links", "life_events", "life_event_memories", "life_event_evidence", "life_event_entities", "stories", "story_versions", "story_chapters", "story_support_links", "visual_companions", "visual_companion_versions", "visual_companion_assets", "visual_generation_jobs"}}


def test_postgresql_migration_ddl():
    spec=importlib.util.spec_from_file_location("migration_l15",BACKEND/"alembic/versions/0016_realtime_sessions.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO()
    context=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})
    with Operations.context(context): module.upgrade()
    ddl=output.getvalue()
    assert "CREATE TABLE realtime_sessions" in ddl
    assert "UNIQUE (active_actor_id)" in ddl and "UNIQUE (ticket_hash)" in ddl
    assert "DROP TABLE" not in ddl and "ALTER TABLE messages" not in ddl

import importlib.util
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers

from app.database import Base, build_engine
from app.models.voice_profile import VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion

ROOT = Path(__file__).resolve().parents[1]


def revision(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = revision("0024_voice_profiles")
synthesis_migration = revision("0025_voice_synthesis_jobs")
live_synthesis_migration = revision("0026_voice_live_synthesis")
tables = [item.__table__ for item in (VoiceProfile, VoiceConsentReceipt, VoiceProfileVersion, VoiceJob, VoiceAsset)]


def test_sqlite_metadata_and_postgresql_ddl_compile():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    configure_mappers()
    assert set(sa.inspect(engine).get_table_names()) >= {item.name for item in tables}
    ddl = []
    mock = sa.create_mock_engine("postgresql://", lambda sql, *a, **kw: ddl.append(str(sql.compile(dialect=mock.dialect))))
    Base.metadata.create_all(mock)
    joined = "\n".join(ddl)
    assert "ALTER TABLE voice_profiles ADD CONSTRAINT fk_voice_profiles_current_scope" in joined
    assert "ALTER TABLE voice_profile_versions ADD CONSTRAINT fk_voice_versions_reference_asset_scope" in joined
    assert "WHERE status <> 'deleted'" in joined


def test_postgresql_migration_ddl_is_self_contained():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE voice_profiles" in sql
    assert sql.index("CREATE TABLE voice_assets") < sql.index("ADD CONSTRAINT fk_voice_versions_reference_asset_scope")
    assert "FOR UPDATE" not in sql
    assert "DROP TABLE voice_profiles" in sql


def reject(conn, statement):
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            conn.execute(statement)


def test_0023_upgrade_0024_downgrade_reupgrade_and_constraints():
    engine = build_engine("sqlite://")
    with engine.connect() as conn:
        tx = conn.begin()
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            history = list(ScriptDirectory(str(ROOT / "alembic")).walk_revisions(base="base", head="0023_plan_shadow_usage"))
            for old in reversed(history):
                old.module.upgrade()
        conn.exec_driver_sql("INSERT INTO users (id,full_name,email,password_hash) VALUES (1,'Synthetic','l21-models@example.invalid','unused')")
        conn.exec_driver_sql("INSERT INTO legacies (id,owner_user_id,subject_name,setup_status) VALUES (1,1,'One','active'),(2,1,'Two','active')")
        with Operations.context(context):
            migration.upgrade()
            synthesis_migration.upgrade()
            live_synthesis_migration.upgrade()
        metadata = sa.MetaData()
        metadata.reflect(conn)
        for expected in tables:
            actual = metadata.tables[expected.name]
            expected_columns = [(c.name, c.type._type_affinity, getattr(c.type, "length", None), c.nullable)
                for c in expected.columns]
            actual_columns = [(c.name, c.type._type_affinity, getattr(c.type, "length", None), c.nullable)
                for c in actual.columns]
            assert actual_columns == expected_columns, expected.name
            def signatures(table):
                values = set()
                for item in table.constraints:
                    if isinstance(item, sa.ForeignKeyConstraint):
                        values.add(("fk", item.name, tuple(item.column_keys),
                            tuple(element.target_fullname for element in item.elements), item.ondelete))
                    elif isinstance(item, sa.UniqueConstraint):
                        values.add(("uq", item.name, tuple(item.columns.keys())))
                    elif isinstance(item, sa.CheckConstraint):
                        values.add(("ck", item.name))
                return values
            assert signatures(actual) == signatures(expected), expected.name
            assert {(i.name, tuple(i.columns.keys()), i.unique) for i in actual.indexes} == {
                (i.name, tuple(i.columns.keys()), i.unique) for i in expected.indexes}, expected.name
        profile = metadata.tables["voice_profiles"]
        consent = metadata.tables["voice_consent_receipts"]
        version = metadata.tables["voice_profile_versions"]
        asset = metadata.tables["voice_assets"]
        job = metadata.tables["voice_jobs"]
        now = datetime.now(timezone.utc)
        for i in (1, 2):
            conn.execute(profile.insert().values(id=f"p{i}", legacy_id=i, status="processing"))
            conn.execute(consent.insert().values(id=f"c{i}", legacy_id=i, voice_profile_id=f"p{i}", actor_user_id=1,
                copy_version="v1", policy_version="v1", authority_basis="self", source_category="self_recording",
                presented_copy_digest="a" * 64, accepted_at=now))
            conn.execute(version.insert().values(id=f"v{i}", legacy_id=i, voice_profile_id=f"p{i}", version_number=1,
                consent_receipt_id=f"c{i}", created_by_user_id=1, request_key="request", request_digest="b" * 64))
            conn.execute(asset.insert().values(id=f"a{i}", legacy_id=i, voice_profile_id=f"p{i}", version_id=f"v{i}",
                kind="reference", state="available", storage_backend="local", object_key=f"legarya/legacies/{i}/voice/ref.wav",
                sha256="c" * 64, byte_count=2, mime_type="audio/wav", sample_rate=24000, channels=1, duration_ms=1000))
            conn.execute(version.update().where(version.c.id == f"v{i}").values(status="ready", reference_asset_id=f"a{i}",
                reference_transcript="synthetic", reference_transcript_digest="d" * 64,
                reference_audio_digest="c" * 64, binding_digest="e" * 64,
                model_manifest_digest="f" * 64, asr_manifest_digest="1" * 64,
                preparation_recipe_revision="recipe-v1"))
        conn.execute(profile.update().where(profile.c.id == "p1").values(status="active", current_version_id="v1"))

        reject(conn, profile.insert().values(id="duplicate", legacy_id=1, status="processing"))
        reject(conn, profile.update().where(profile.c.id == "p1").values(current_version_id="v2"))
        reject(conn, version.insert().values(id="cross-consent", legacy_id=1, voice_profile_id="p1", version_number=2,
            consent_receipt_id="c2", created_by_user_id=1, request_key="two", request_digest="b" * 64))
        reject(conn, asset.insert().values(id="cross-asset", legacy_id=1, voice_profile_id="p1", version_id="v2",
            kind="original", storage_backend="local", object_key="legarya/cross"))
        reject(conn, job.insert().values(id="cross-job", legacy_id=1, voice_profile_id="p1", version_id="v2",
            kind="prepare", operation_generation=1, request_key="cross", request_digest="a" * 64))
        conn.execute(consent.insert().values(id="c3", legacy_id=1, voice_profile_id="p1", actor_user_id=1,
            copy_version="v1", policy_version="v1", authority_basis="self", source_category="self_recording",
            presented_copy_digest="a" * 64, accepted_at=now))
        conn.execute(version.insert().values(id="not-ready", legacy_id=1, voice_profile_id="p1", version_number=2,
            consent_receipt_id="c3", created_by_user_id=1, request_key="not-ready", request_digest="b" * 64))
        reject(conn, profile.update().where(profile.c.id == "p1").values(current_version_id="not-ready"))
        reject(conn, profile.update().where(profile.c.id == "p1").values(status="active", current_version_id=None))
        reject(conn, consent.update().where(consent.c.id == "c1").values(copy_version="changed"))
        conn.execute(consent.update().where(consent.c.id == "c1").values(revoked_at=now))
        reject(conn, consent.update().where(consent.c.id == "c1").values(revoked_at=now))
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []

        with Operations.context(context):
            live_synthesis_migration.downgrade()
            synthesis_migration.downgrade()
            migration.downgrade()
        assert not any(name.startswith("voice_") for name in sa.inspect(conn).get_table_names())
        assert conn.exec_driver_sql("SELECT count(*) FROM users").scalar_one() == 1
        assert conn.exec_driver_sql("SELECT count(*) FROM legacies").scalar_one() == 2
        with Operations.context(context):
            migration.upgrade()
            synthesis_migration.upgrade()
            live_synthesis_migration.upgrade()
        assert {item.name for item in tables} <= set(sa.inspect(conn).get_table_names())
        tx.rollback()
    engine.dispose()

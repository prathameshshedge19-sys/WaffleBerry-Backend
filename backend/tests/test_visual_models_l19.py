"""L19 schema parity, scope enforcement and isolated migration acceptance."""
import importlib.util
import io
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parents[1]

import pytest
from uuid import uuid4
from alembic.script import ScriptDirectory
import sqlalchemy as sa
from sqlalchemy.orm import configure_mappers
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.database import Base, build_engine
import app.models
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob


def revision(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = revision("0021_visual_companions")
tables = [cls.__table__ for cls in (VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob)]
def test_sqlite_metadata_creation():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    configure_mappers()
    assert len(sa.inspect(engine).get_foreign_keys("visual_companions")) == 3
    print("PASS complete SQLite metadata creation and ORM mapper configuration")

def test_postgresql_metadata_ddl():
    ddl = []
    mock = sa.create_mock_engine("postgresql://", lambda sql, *a, **kw: ddl.append(str(sql.compile(dialect=mock.dialect))))
    Base.metadata.create_all(mock)
    assert any("ALTER TABLE visual_companions ADD CONSTRAINT fk_visual_companions_current_scope" in s for s in ddl)
    assert any("ALTER TABLE visual_companions ADD CONSTRAINT fk_visual_companions_desired_scope" in s for s in ddl)
    assert any("WHERE state = 'available'" in s for s in ddl)
    print("PASS PostgreSQL metadata DDL compilation including circular FKs and partial unique")

def test_postgresql_migration_ddl():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert sql.index("CREATE TABLE visual_companion_versions") < sql.index("ADD CONSTRAINT fk_visual_companions_current_scope")
    assert "DROP CONSTRAINT ck_media_sources_purpose" in sql
    print("PASS self-contained PostgreSQL migration upgrade/downgrade DDL")

@pytest.fixture(params=["sqlite", "postgresql"])
def schema_connection(request):
    if request.param == "postgresql":
        url = os.environ.get("L19_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Requires explicit disposable L19 PostgreSQL URL")
        parsed = sa.engine.make_url(url)
        assert parsed.host in {"127.0.0.1", "localhost"}
        assert parsed.database == "l19_test_phase_b"
        engine = build_engine(url)
    else:
        engine = build_engine("sqlite://")
    with engine.connect() as conn:
        transaction = conn.begin()
        if conn.dialect.name == "postgresql":
            assert conn.exec_driver_sql("SELECT current_database()").scalar_one() == "l19_test_phase_b"
            schema = "l19_models_" + uuid4().hex
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        try:
            yield conn
        finally:
            transaction.rollback()
    engine.dispose()


def test_migration_constraints_roundtrip(schema_connection):
    conn = schema_connection
    now = datetime.now(timezone.utc)
    context = MigrationContext.configure(conn)
    # Exercise the real historical chain, including artifact scope added in 0018.
    with Operations.context(context):
        for old in reversed(list(ScriptDirectory(str(ROOT / "alembic")).walk_revisions(base="base", head="0020_legacy_stories"))):
            old.module.upgrade()
    conn.exec_driver_sql("INSERT INTO users (id,full_name,email,password_hash) VALUES (1,'Synthetic','l19-models@example.invalid','test')")
    conn.exec_driver_sql("INSERT INTO legacies (id,owner_user_id,subject_name,setup_status) VALUES (1,1,'Synthetic 1','active'),(2,1,'Synthetic 2','active')")
    source = sa.Table("media_sources", sa.MetaData(), autoload_with=conn)
    for i in (1, 2):
        conn.execute(source.insert().values(id=f"s{i}", legacy_id=i, kind="image", original_filename="synthetic.png", declared_mime_type="image/png", declared_size_bytes=1, upload_request_key=f"r{i}", upload_request_digest="a" * 64, upload_expires_at=now))
    with Operations.context(context):
        migration.upgrade()
    assert conn.exec_driver_sql("SELECT processing_purpose FROM media_sources").scalars().all() == ["source_review", "source_review"]
    print("PASS populated L16 migration defaults")

    inspector = sa.inspect(conn)
    for table in tables:
        reflected = sa.Table(table.name, sa.MetaData(), autoload_with=conn)
        assert [(c.name, c.type._type_affinity, getattr(c.type, "length", None), c.nullable) for c in table.columns] == [(c.name, c.type._type_affinity, getattr(c.type, "length", None), c.nullable) for c in reflected.columns], table.name
        expected_checks = {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)}
        if conn.dialect.name == "postgresql":
            # Compare exact server-normalized expressions, not SQL spelling:
            # PostgreSQL rewrites IN to ANY, casts strings and removes brackets.
            temporary = "l19_expected_" + table.name
            conn.exec_driver_sql(f'CREATE TEMP TABLE "{temporary}" (LIKE "{table.name}") ON COMMIT DROP')
            for name, expression in expected_checks.items():
                conn.exec_driver_sql(f'ALTER TABLE "{temporary}" ADD CONSTRAINT "{name}" CHECK ({expression})')
            expected_checks = {c["name"]: c["sqltext"] for c in sa.inspect(conn).get_check_constraints(temporary)}
        def constraints(t):
            result = set()
            for c in t.constraints:
                if isinstance(c, sa.ForeignKeyConstraint):
                    result.add(("fk", tuple(c.column_keys), tuple(e.target_fullname for e in c.elements), c.ondelete))
                elif isinstance(c, sa.UniqueConstraint):
                    result.add(("uq", c.name, tuple(c.columns.keys())))
                elif isinstance(c, sa.CheckConstraint):
                    result.add(("ck", c.name, expected_checks[c.name] if t is table else str(c.sqltext)))
            return result
        assert constraints(table) == constraints(reflected), (table.name, constraints(table) ^ constraints(reflected))
        def indexes(t):
            return {(i.name, tuple(i.columns.keys()), i.unique, i.dialect_options[conn.dialect.name].get("where") is not None) for i in t.indexes}
        assert indexes(table) == indexes(reflected), table.name
        for c in table.columns:
            if c.server_default is not None:
                actual = reflected.c[c.name].server_default.arg.text.split("::")[0].strip("'()")
                expected = str(c.server_default.arg.compile(dialect=conn.dialect)) if not isinstance(c.server_default.arg, str) else c.server_default.arg
                assert actual.upper() == expected.strip("'()").upper(), (table.name, c.name, actual, expected)
    assert {c["name"] for c in inspector.get_check_constraints("media_sources")} >= {"ck_media_sources_purpose", "ck_media_sources_visual_image"}
    print("PASS model/migration columns, defaults, FK/check/unique constraints and index parity")

    profile, version, asset, job = tables
    artifact = sa.Table("media_artifacts", sa.MetaData(), autoload_with=conn)
    for i in (1, 2):
        conn.execute(profile.insert().values(id=f"p{i}", legacy_id=i))
        conn.execute(artifact.insert().values(id=f"a{i}", legacy_id=i, source_id=f"s{i}", generation=1, kind="original", logical_key="original", storage_backend="local", object_key=f"original/{i}"))
    def version_values(i=1, **extra):
        return dict(id=f"v{i}", legacy_id=i, companion_id=f"p{i}", version_number=1,
                    source_id=f"s{i}", source_generation=2, source_artifact_generation=1, source_artifact_id=f"a{i}",
                    source_sha256="a" * 64, crop_json={"rotation": 0}, crop_digest="b" * 64,
                    confirmed_by_user_id=1, confirmed_at=now, confirmation_copy_version="v1", request_key="request1",
                    request_digest="c" * 64, recipe_version="portrait_2d_v1", provider_name="fake", model_digest="d" * 64,
                    recipe_digest="e" * 64) | extra
    for i in (1, 2):
        conn.execute(version.insert().values(**version_values(i)))
    rejected = 0
    def reject(statement):
        nonlocal rejected
        try:
            with conn.begin_nested():
                conn.execute(statement)
        except IntegrityError:
            rejected += 1
        else:
            raise AssertionError(f"Invalid write succeeded: {statement}")
    reject(profile.insert().values(id="duplicate", legacy_id=1))
    reject(profile.update().where(profile.c.id == "p1").values(revision=0))
    reject(profile.update().where(profile.c.id == "p1").values(enabled=True))
    for pointer in ("current_version_id", "desired_version_id"):
        reject(profile.update().where(profile.c.id == "p1").values(**{pointer: "v2"}))
        conn.execute(profile.update().where(profile.c.id == "p1").values(**{pointer: "v1"}))
    conn.execute(profile.update().where(profile.c.id == "p1").values(enabled=True))
    reject(profile.update().where(profile.c.id == "p1").values(deleted_at=now))
    for changes in (
        {"companion_id": "p2"}, {"source_id": "s2"}, {"source_artifact_id": "a2"},
        {"source_artifact_generation": 2}, {"source_generation": 0}, {"version_number": 0},
        {"version_number": 1}, {"request_key": "request1"}, {"request_key": " "},
        {"state": "active"}, {"approved_at": now}, {"approved_by_user_id": 1}, {"state": "ready"},
    ):
        reject(version.insert().values(**version_values(id="bad", version_number=2, request_key="new") | changes))
    conn.execute(version.update().where(version.c.id == "v1").values(state="ready", bundle_digest="f" * 64, approved_by_user_id=1, approved_at=now))
    av = dict(id="asset1", legacy_id=1, companion_id="p1", version_id="v1", attempt_id="attempt1", logical_role="poster", storage_backend="local", object_key="visual/1", writer_deadline=now)
    conn.execute(asset.insert().values(**av))
    for changes in ({"legacy_id": 2}, {"companion_id": "p2"}, {"version_id": "v2"}, {"logical_role": "video"}, {"state": "bad"}, {"byte_size": -1}, {"byte_size": 2097153}, {"width": 1}, {"width": 1025, "height": 1}, {"state": "available"}):
        reject(asset.insert().values(**(av | {"id": "bad", "attempt_id": "new", "object_key": "visual/new"} | changes)))
    reject(asset.insert().values(**(av | {"id": "bad", "object_key": "different"})))
    reject(asset.insert().values(**(av | {"id": "bad", "attempt_id": "new"})))
    conn.execute(asset.update().where(asset.c.id == "asset1").values(state="available", byte_size=1, sha256="a" * 64, mime_type="image/png"))
    next_asset = av | {"id": "asset2", "attempt_id": "attempt2", "object_key": "visual/2", "state": "available", "byte_size": 1, "sha256": "a" * 64, "mime_type": "image/png"}
    reject(asset.insert().values(**next_asset))
    conn.execute(asset.update().where(asset.c.id == "asset1").values(state="purge_pending"))
    conn.execute(asset.insert().values(**next_asset))
    jv = dict(id="job1", legacy_id=1, companion_id="p1", version_id="v1", kind="prepare")
    conn.execute(job.insert().values(**jv))
    for changes in ({"legacy_id": 2}, {"companion_id": "p2"}, {"version_id": "v2", "legacy_id": 1}, {"kind": "bad"}):
        reject(job.insert().values(**(jv | {"id": "bad", "kind": "purge"} | changes)))
    for changes in ({"attempts": -1}, {"attempts": 3}, {"state": "bad"}, {"lease_token": "token"}, {"lease_expires_at": now}):
        reject(job.update().where(job.c.id == "job1").values(**changes))
    reject(job.insert().values(**(jv | {"id": "duplicate"})))
    conn.execute(job.insert().values(**(jv | {"id": "purge1", "kind": "purge", "attempts": 1000})))
    conn.execute(job.update().where(job.c.id == "job1").values(lease_token="token", lease_expires_at=now, writer_deadline=now, attempts=2))
    reject(sa.text("UPDATE media_sources SET processing_purpose = 'unknown' WHERE id = 's1'"))
    reject(sa.text("UPDATE media_sources SET processing_purpose = 'visual_reference' WHERE id = 's1'"))
    reject(sa.text("DELETE FROM media_sources WHERE id = 's1'"))
    reject(sa.text("DELETE FROM media_artifacts WHERE id = 'a1'"))
    reject(profile.delete().where(profile.c.id == "p1"))
    if conn.dialect.name == "sqlite":
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    print(f"PASS {rejected} invalid-write rejections plus valid pointers, replacement, separate generations and unlimited purge retries")
    with Operations.context(context):
        migration.downgrade()
    assert not any(name.startswith("visual_") for name in sa.inspect(conn).get_table_names())
    assert "processing_purpose" not in {c["name"] for c in sa.inspect(conn).get_columns("media_sources")}
    assert conn.exec_driver_sql("SELECT count(*) FROM media_sources").scalar_one() == 2
    assert conn.exec_driver_sql("SELECT count(*) FROM media_artifacts").scalar_one() == 2
    with Operations.context(context):
        migration.upgrade()
    if conn.dialect.name == "sqlite":
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    print("PASS populated downgrade/re-upgrade; original source/artifact rows preserved")


def test_raw_sql_purpose_and_factual_guards(schema_connection):
    conn = schema_connection
    context = MigrationContext.configure(conn)
    with Operations.context(context):
        for old in reversed(list(ScriptDirectory(str(ROOT / "alembic")).walk_revisions(base="base", head="0020_legacy_stories"))):
            old.module.upgrade()
        migration.upgrade()
    conn.exec_driver_sql("INSERT INTO users (id,full_name,email,password_hash) VALUES (1,'Synthetic','guards@example.invalid','test')")
    conn.exec_driver_sql("INSERT INTO legacies (id,owner_user_id,subject_name,setup_status) VALUES (1,1,'Synthetic','active'),(2,1,'Other','active')")
    now = datetime.now(timezone.utc)
    metadata = sa.MetaData()
    metadata.reflect(conn)
    sources = metadata.tables["media_sources"]
    jobs = metadata.tables["media_processing_jobs"]
    for key, purpose in (("ordinary", "source_review"), ("visual", "visual_reference")):
        conn.execute(sources.insert().values(id=key, legacy_id=1, kind="image", processing_purpose=purpose,
            original_filename="synthetic.png", declared_mime_type="image/png", declared_size_bytes=1,
            upload_request_key=key, upload_request_digest="0" * 64, upload_expires_at=now))
        conn.execute(jobs.insert().values(id=key, legacy_id=1, source_id=key, generation=1,
            kind="extract", pipeline_version="synthetic"))
    conn.execute(Base.metadata.tables["memories"].insert().values(id=1, legacy_id=1,
        canonical_text="Synthetic fixture", category="life_event", source_language="english",
        source_excerpt="Synthetic", confidence=1, status="active", operation_type="explicit_save",
        explicit_save=True, normalized_fingerprint="l19-raw-guard-fixture"))
    rows = {
        "source_evidence": dict(id="e1", legacy_id=1, source_id="ordinary", generation=1,
            job_id="ordinary", stable_key="e1", kind="image_description"),
        "source_memory_candidates": dict(id="c1", legacy_id=1, source_id="ordinary", generation=1,
            job_id="ordinary", stable_key="c1", proposal_json={}),
        "source_candidate_evidence": dict(legacy_id=1, source_id="ordinary", candidate_id="c1", evidence_id="e1"),
        "memory_source_links": dict(id="l1", legacy_id=1, source_id="ordinary", generation=1,
            memory_id=1, candidate_id="c1", evidence_id="e1", approved_text_sha256="0" * 64),
    }
    for name, values in rows.items():
        table = metadata.tables[name]
        conn.execute(table.insert().values(**values))
        # Ordinary insertion AND updates continue to work via Core/raw SQL.
        conn.execute(table.update().values(source_id="ordinary"))
        visual_values = values | {"source_id": "visual"}
        if "id" in visual_values:
            visual_values["id"] += "-visual"
        if "job_id" in visual_values:
            visual_values["job_id"] = "visual"
        for statement in (table.insert().values(**visual_values), table.update().values(source_id="visual")):
            with pytest.raises(IntegrityError, match="visual_reference_has_no_factual_support"):
                with conn.begin_nested():
                    conn.execute(statement)
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 1
    for source_id, changed in (("ordinary", "visual_reference"), ("visual", "source_review")):
        with pytest.raises(IntegrityError, match="processing_purpose_immutable"):
            with conn.begin_nested():
                conn.execute(sources.update().where(sources.c.id == source_id).values(processing_purpose=changed))
    conn.execute(sources.update().where(sources.c.id == "visual").values(processing_purpose="visual_reference"))
    with pytest.raises(IntegrityError, match="ck_media_sources_visual_image"):
        with conn.begin_nested():
            conn.execute(sources.update().where(sources.c.id == "visual").values(kind="audio"))
    # The trigger is scoped, so a nonexistent cross-Legacy tuple is rejected by
    # the existing L16 FK, not mistaken for an authorized visual source.
    with pytest.raises(IntegrityError) as failure:
        with conn.begin_nested():
            conn.execute(metadata.tables["source_evidence"].insert().values(
                **(rows["source_evidence"] | {"id": "foreign", "legacy_id": 2, "source_id": "visual", "job_id": "visual"})))
    assert "visual_reference_has_no_factual_support" not in str(failure.value)
    with Operations.context(context):
        migration.downgrade()
    # Trigger/function cleanup is part of downgrade and guards return on upgrade.
    if conn.dialect.name == "postgresql":
        assert conn.execute(sa.text("SELECT count(*) FROM pg_proc WHERE pronamespace = current_schema()::regnamespace AND proname LIKE 'l19_%'")).scalar_one() == 0
    else:
        assert conn.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_l19_%'").scalar_one() == 0
    with Operations.context(context):
        migration.upgrade()
    with pytest.raises(IntegrityError, match="processing_purpose_immutable"):
        with conn.begin_nested():
            conn.execute(sources.update().where(sources.c.id == "ordinary").values(processing_purpose="visual_reference"))

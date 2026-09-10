"""Opt-in isolated PostgreSQL concurrency; never targets a production database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base
from app.models.plan_usage import PlanUsage, PlanTrackingState
from app.models.user import User
from app.services import plan_usage as plans


@pytest.fixture
def pg(monkeypatch):
    url = os.environ.get("PLAN_TEST_POSTGRES_URL")
    if not url: pytest.skip("Requires a disposable PLAN_TEST_POSTGRES_URL")
    parsed = make_url(url)
    assert parsed.host == "127.0.0.1" and parsed.database == "plans_shadow_test"
    engine = create_engine(url)
    schema = "plans_qa_" + uuid4().hex
    with engine.begin() as db:
        assert db.execute(text("SELECT current_database()")).scalar_one() == "plans_shadow_test"
        assert db.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0023_plan_shadow_usage"
        db.execute(text('CREATE SCHEMA "' + schema + '"'))
    scoped = engine.execution_options(schema_translate_map={None:schema})
    # PostgreSQL AddConstraint compilation mutates constraint creation rules.
    # Use a copy so subsequent SQLite suites keep their inline circular FKs.
    isolated_metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(isolated_metadata)
    isolated_metadata.create_all(scoped)
    factory = sessionmaker(bind=scoped, expire_on_commit=False, autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1, email="synthetic@example.com", full_name="Synthetic", password_hash="test", is_verified=True))
        db.add(PlanTrackingState(name="shadow", started_at=plans.now()-timedelta(days=1)))
    monkeypatch.setattr(get_settings(), "plans_tracking_enabled", True)
    try:
        yield factory
    finally:
        # Exact generated schema in the asserted disposable database only.
        with engine.begin() as db:
            db.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()


def test_parallel_duplicate_receipts_are_once_only(pg):
    def worker(_):
        with pg.begin() as db:
            plans._put(db, key="same-reply", user_id=1, feature="rya_text", day=plans.now().date(), amount=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(12)))
    with pg() as db:
        assert plans.snapshot(db, 1)["daily"]["rya_text"]["used"] == 1


def test_delayed_pending_cannot_regress_completed_receipt(pg):
    with pg.begin() as db:
        plans._put(db, key="settled", user_id=1, feature="rya_text", day=plans.now().date(), amount=1)
    with pg.begin() as db:
        plans._put(db, key="settled", user_id=1, feature="rya_text", day=plans.now().date(), reserved=1, state="pending")
    with pg() as db:
        row = db.get(PlanUsage, "settled")
        assert row.state == "completed" and row.amount == 1 and row.reserved == 0


def test_parallel_distinct_messages_are_not_lost_or_blocked(pg):
    def worker(index):
        with pg.begin() as db:
            plans._put(db, key="reply:"+str(index), user_id=1, feature="rya_text", day=plans.now().date(), amount=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(45)))
    with pg() as db:
        result = plans.snapshot(db, 1)
        assert result["daily"]["rya_text"]["used"] == 45
        assert result["enforcement_enabled"] is False


def test_failed_shadow_savepoint_preserves_existing_product_transaction(pg):
    with pg() as db:
        before = db.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
        db.get(User, 1).full_name = "Changed synthetic name"
        plans._safe(db, lambda session: session.execute(text("SELECT * FROM missing_shadow_table")))
        assert db.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one() == before
        db.commit()
    with pg() as db:
        assert db.get(User, 1).full_name == "Changed synthetic name"

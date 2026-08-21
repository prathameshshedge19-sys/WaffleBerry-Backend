"""Phase 11.5 canonical per-Legacy capacity enforcement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.memory import Legacy, Memory, MemoryReviewStatus, MemoryType
from app.models.user import PlanTier, User
from app.services.memory.review import MemoryReviewQuotaError, MemoryReviewService
from app.services.quota import QuotaFeature, QuotaService, get_plan_limits
from app.services.quota import QuotaDecision
from app.api.v1.memory import approve_memory
from app.schemas.memory import MemoryReviewActionRequest


def add_memory(db, legacy_id, index, status=MemoryReviewStatus.APPROVED):
    memory = Memory(
        legacy_id=legacy_id,
        memory_type=MemoryType.ATOMIC,
        category="life",
        title=f"Capacity memory {index}",
        summary=f"Canonical capacity fixture {index}",
        normalized_fingerprint=f"capacity-{legacy_id}-{index}",
        review_status=status,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(memory)
    return memory


@pytest.mark.parametrize("plan", [PlanTier.FREE, PlanTier.PLUS, PlanTier.PRO])
def test_plan_boundary_next_approval_is_blocked_and_delete_frees_slot(tmp_path, plan):
    engine = create_engine(f"sqlite:///{tmp_path / f'{plan.value}.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        user = User(full_name="Owner", email=f"{plan.value}@example.test",
                    password_hash="hash", plan=plan)
        db.add(user); db.flush()
        legacy = Legacy(owner_user_id=user.user_id, display_name="A", relationship="Parent")
        db.add(legacy); db.flush()
        limit = get_plan_limits(plan).memories_per_legacy
        db.add_all([add_memory(db, legacy.legacy_id, i) for i in range(limit)])
        candidate = add_memory(db, legacy.legacy_id, "candidate", MemoryReviewStatus.CANDIDATE)
        db.commit()

        with pytest.raises(MemoryReviewQuotaError) as caught:
            MemoryReviewService().approve(
                db, user_id=user.user_id, legacy_id=legacy.legacy_id,
                memory_id=candidate.memory_id,
                expected_updated_at=candidate.updated_at,
            )
        decision = caught.value.decision
        assert decision.feature == QuotaFeature.MEMORY
        assert decision.used == limit and decision.resets_at is None
        assert db.get(Memory, candidate.memory_id).review_status == MemoryReviewStatus.CANDIDATE

        db.delete(db.query(Memory).filter_by(
            legacy_id=legacy.legacy_id,
            review_status=MemoryReviewStatus.APPROVED,
        ).first())
        db.commit()
        refreshed = db.get(Memory, candidate.memory_id)
        result = MemoryReviewService().approve(
            db, user_id=user.user_id, legacy_id=legacy.legacy_id,
            memory_id=refreshed.memory_id,
            expected_updated_at=refreshed.updated_at,
        )
        assert result.review_status == MemoryReviewStatus.APPROVED
        assert QuotaService(db).check_memory_capacity(user, legacy.legacy_id).used == limit


def test_capacity_is_per_legacy_and_quota_exempt_is_unlimited(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolation.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        user = User(full_name="Owner", email="owner@example.test", password_hash="hash")
        admin = User(full_name="Admin", email="admin@example.test", password_hash="hash",
                     quota_exempt=True)
        db.add_all([user, admin]); db.flush()
        full = Legacy(owner_user_id=user.user_id, display_name="Full", relationship="Parent")
        open_legacy = Legacy(owner_user_id=user.user_id, display_name="Open", relationship="Friend")
        unlimited = Legacy(owner_user_id=admin.user_id, display_name="Admin", relationship="Self")
        db.add_all([full, open_legacy, unlimited]); db.flush()
        limit = get_plan_limits(PlanTier.FREE).memories_per_legacy
        db.add_all([add_memory(db, full.legacy_id, i) for i in range(limit)])
        db.add_all([add_memory(db, unlimited.legacy_id, f"admin-{i}") for i in range(limit + 1)])
        db.commit()
        assert not QuotaService(db).check_memory_capacity(user, full.legacy_id).allowed
        assert QuotaService(db).check_memory_capacity(user, open_legacy.legacy_id).allowed
        assert QuotaService(db).check_memory_capacity(admin, unlimited.legacy_id).allowed


def test_two_sqlite_approvals_at_last_slot_create_only_memory_100(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as setup:
        user = User(full_name="Owner", email="race@example.test", password_hash="hash")
        setup.add(user); setup.flush()
        legacy = Legacy(owner_user_id=user.user_id, display_name="A", relationship="Parent")
        setup.add(legacy); setup.flush()
        setup.add_all([add_memory(setup, legacy.legacy_id, i) for i in range(99)])
        first = add_memory(setup, legacy.legacy_id, "first", MemoryReviewStatus.CANDIDATE)
        second = add_memory(setup, legacy.legacy_id, "second", MemoryReviewStatus.CANDIDATE)
        setup.commit()
        ids = (user.user_id, legacy.legacy_id, first.memory_id, second.memory_id)
    barrier = Barrier(2)

    def approve(memory_id):
        with Session() as db:
            memory = db.get(Memory, memory_id)
            barrier.wait()
            try:
                MemoryReviewService().approve(
                    db, user_id=ids[0], legacy_id=ids[1], memory_id=memory_id,
                    expected_updated_at=memory.updated_at,
                )
                return True
            except MemoryReviewQuotaError:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, ids[2:]))
    assert sorted(results) == [False, True]
    with Session() as verify:
        user = verify.get(User, ids[0])
        assert QuotaService(verify).check_memory_capacity(user, ids[1]).used == 100


def test_explicit_approval_returns_structured_memory_429():
    decision = QuotaDecision(
        allowed=False, feature=QuotaFeature.MEMORY, plan=PlanTier.FREE,
        limit=100, used=100, remaining=0, resets_at=None, quota_exempt=False,
    )

    class FullService:
        def approve(self, *args, **kwargs):
            raise MemoryReviewQuotaError(decision)

    action = MemoryReviewActionRequest(
        expected_updated_at=datetime.now(timezone.utc)
    )
    with pytest.raises(HTTPException) as caught:
        approve_memory(
            1, 2, action,
            current_user=type("CurrentUser", (), {"user_id": 3})(),
            db=object(), service=FullService(),
        )
    assert caught.value.status_code == 429
    assert caught.value.detail == {
        "error": "quota_exceeded", "feature": "memory", "plan": "free",
        "resets_at": None, "upgrade_available": False,
    }

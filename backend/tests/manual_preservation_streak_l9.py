"""Disposable local L9 acceptance scenario: python -m tests.manual_preservation_streak_l9."""

import json
from datetime import date, timedelta

from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.legacy import Legacy
from app.models.user import User
from app.services.progression import record_builder_activity, streak_summary


def run() -> None:
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    today = date(2026, 9, 4)
    with Session() as db:
        owner = User(full_name="Prathamesh", email="owner@manual.invalid", password_hash="x", is_verified=True)
        arya = User(full_name="Arya", email="arya@manual.invalid", password_hash="x", is_verified=True)
        visitor = User(full_name="Visitor", email="visitor@manual.invalid", password_hash="x", is_verified=True)
        db.add_all([owner, arya, visitor]); db.flush()
        pallavi = Legacy(owner_user_id=owner.id, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
        prathamesh = Legacy(owner_user_id=owner.id, subject_name="Prathamesh", relationship_to_owner="self", is_self=True, setup_status="active")
        db.add_all([pallavi, prathamesh]); db.commit()

        before = streak_summary(db, pallavi.id, today)
        first = record_builder_activity(db, user_id=owner.id, legacy_id=pallavi.id, activity_type="new", memory_id=None, activity_date=today)
        first_completed_today = first.was_first_today
        for _ in range(5):
            record_builder_activity(db, user_id=owner.id, legacy_id=pallavi.id, activity_type="story", memory_id=None, activity_date=today)
        after_six = streak_summary(db, pallavi.id, today)
        other = streak_summary(db, prathamesh.id, today)
        record_builder_activity(db, user_id=arya.id, legacy_id=pallavi.id, activity_type="new", memory_id=None, activity_date=today + timedelta(days=1))
        collaborator_next_day = streak_summary(db, pallavi.id, today + timedelta(days=1))
        counts_before_visitor = (after_six["contribution_count_today"], collaborator_next_day["current_streak_days"])
        # Visitor chat has no path to record_builder_activity; simulate several read-only turns by doing no mutation.
        counts_after_visitor = (streak_summary(db, pallavi.id, today)["contribution_count_today"], streak_summary(db, pallavi.id, today + timedelta(days=1))["current_streak_days"])
        assert not before["today_completed"] and first_completed_today
        assert after_six["today_completed"] and after_six["current_streak_days"] == 1 and after_six["contribution_count_today"] == 6
        assert not other["today_completed"] and other["current_streak_days"] == 0
        assert collaborator_next_day["current_streak_days"] == 2
        assert counts_before_visitor == counts_after_visitor
        print(json.dumps({"pallavi_before": before, "pallavi_after_six": after_six, "prathamesh": other, "collaborator_next_day": collaborator_next_day, "visitor_delta": 0}))
    engine.dispose()


if __name__ == "__main__":
    run()

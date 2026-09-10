"""Operator-only shadow inspection and exact verified testing-account exemption.

Never accepts a client-selected tier and never prints credentials/content.
"""
import argparse
import json
from sqlalchemy import select

from app.database import SessionLocal
from app.models.user import User
from app.models.plan_usage import PlanEntitlement, PlanTrackingState
from app.services.plan_usage import now, reconcile, snapshot

TESTING_EMAIL = "prathameshshedge90@gmail.com"


def grant_testing_exemption(db, expected_user_id):
    user = db.scalar(select(User).where(User.email == TESTING_EMAIL).with_for_update())
    if user is None or not user.is_verified or user.id != expected_user_id:
        raise ValueError("Verified testing account did not match the expected user ID")
    row = db.get(PlanEntitlement, user.id)
    if row is None:
        row = PlanEntitlement(user_id=user.id, plan="free")
        db.add(row)
    row.quota_exempt = True
    row.updated_at = now()
    db.commit()
    return {"user_id": user.id, "plan": row.plan, "quota_exempt": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["verify-testing-account", "grant-testing-exemption", "snapshot", "reconcile", "initialize-enforcement"])
    parser.add_argument("--user-id", type=int)
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.action == "verify-testing-account":
            user = db.scalar(select(User).where(User.email == TESTING_EMAIL))
            result = {"found": user is not None, "verified": bool(user and user.is_verified), "user_id": user.id if user else None}
        elif args.action == "grant-testing-exemption":
            if args.user_id is None: parser.error("--user-id is required")
            result = grant_testing_exemption(db, args.user_id)
        elif args.action == "snapshot":
            if args.user_id is None or db.get(User, args.user_id) is None: parser.error("A valid --user-id is required")
            result = snapshot(db, args.user_id)
        elif args.action == "initialize-enforcement":
            # Idempotent cutover. Never reset a previous allowance on redeploy.
            from app.services.plan_usage import _insert
            statement = _insert(db, PlanTrackingState).values(name="enforcement", started_at=now(), cursor_turn_id=0)
            db.execute(statement.on_conflict_do_nothing(index_elements=[PlanTrackingState.name]))
            db.commit()
            result = {"enforcement_since": db.get(PlanTrackingState, "enforcement").started_at.isoformat()}
        else:
            result = reconcile(db)
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

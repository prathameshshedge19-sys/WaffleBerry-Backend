"""Set one existing local user's entitlement plan without changing usage or exemption."""

import argparse

from app.config import get_settings
from app.crud.user import UserCRUD
from app.db import SessionLocal
from app.models.user import PlanTier


def main() -> None:
    if not get_settings().debug:
        raise SystemExit("This helper is available only when DEBUG=true.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--plan", required=True, choices=[plan.value for plan in PlanTier]
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        user = UserCRUD.get_user_by_email(db, args.email)
        if user is None:
            raise SystemExit("User not found.")
        exemption_before = bool(user.quota_exempt)
        user.plan = PlanTier(args.plan)
        db.commit()
        db.refresh(user)
        if bool(user.quota_exempt) != exemption_before:
            db.rollback()
            raise SystemExit("Safety check failed: quota exemption changed.")
        print(
            f"Set {user.email} to plan={user.plan.value}. "
            f"quota_exempt remains {str(bool(user.quota_exempt)).lower()}."
        )


if __name__ == "__main__":
    main()

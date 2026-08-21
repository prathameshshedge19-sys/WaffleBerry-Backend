"""Set today's Chat usage for local acceptance testing only."""

import argparse

from app.config import get_settings
from app.crud.user import UserCRUD
from app.db import SessionLocal
from app.services.quota import QuotaService, get_plan_limits


def main() -> None:
    if not get_settings().debug:
        raise SystemExit("This helper is available only when DEBUG=true.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--used", required=True, type=int)
    args = parser.parse_args()
    if args.used < 0:
        parser.error("--used cannot be negative")

    with SessionLocal() as db:
        user = UserCRUD.get_user_by_email(db, args.email)
        if user is None:
            raise SystemExit("User not found.")
        if user.quota_exempt:
            raise SystemExit("Choose a normal account; quota-exempt users are unlimited.")
        limit = get_plan_limits(user.plan).chat_daily
        if args.used > limit:
            parser.error(f"--used cannot exceed this user's limit ({limit})")
        row = QuotaService(db).get_daily_usage(user)
        row.chat_turns = args.used
        db.commit()
        print(f"Set today's Chat usage to {args.used}/{limit} for {user.email}.")


if __name__ == "__main__":
    main()

"""Explicit, idempotent development-admin seed command.

Run from the backend directory with ``python -m scripts.seed_dev_admin --prompt``.
"""

import argparse
import getpass
import os

from app.crud.user import UserCRUD
from app.db import SessionLocal


ADMIN_EMAIL = "admin@gmail.com"
PASSWORD_ENV = "WAFFLEBERRY_DEV_ADMIN_PASSWORD"


def seed_dev_admin(db, password: str | None = None):
    user = UserCRUD.get_user_by_email(db, ADMIN_EMAIL)
    created = user is None
    if created:
        if not password:
            raise ValueError(f"A password is required via {PASSWORD_ENV} or secure prompt.")
        user = UserCRUD.create_user(
            db, full_name="WaffleBerry Admin", email=ADMIN_EMAIL, password=password
        )
    user.is_verified = True
    user.quota_exempt = True
    db.commit()
    db.refresh(user)
    return user, created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt", action="store_true", help="Securely prompt instead of reading the environment."
    )
    args = parser.parse_args()
    password = (
        getpass.getpass("Development admin password: ")
        if args.prompt
        else os.environ.get(PASSWORD_ENV)
    )
    with SessionLocal() as db:
        user, created = seed_dev_admin(db, password)
    print(f"Development admin {'created' if created else 'updated'}: {user.email}")


if __name__ == "__main__":
    main()

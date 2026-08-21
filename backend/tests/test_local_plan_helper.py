"""Safety coverage for the DEBUG-only local plan operator helper."""

from types import SimpleNamespace

import pytest

from app.models.user import PlanTier
from scripts import set_local_user_plan as helper


def test_helper_rejects_non_debug(monkeypatch):
    monkeypatch.setattr(helper, "get_settings", lambda: SimpleNamespace(debug=False))
    with pytest.raises(SystemExit, match="DEBUG=true"):
        helper.main()


def test_helper_rejects_unknown_plan_before_database_access(monkeypatch):
    monkeypatch.setattr(helper, "get_settings", lambda: SimpleNamespace(debug=True))
    monkeypatch.setattr("sys.argv", ["set_local_user_plan", "--email", "a@b.com", "--plan", "enterprise"])
    with pytest.raises(SystemExit):
        helper.main()


def test_helper_rejects_unknown_user(monkeypatch):
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): return None

    db = DB()
    monkeypatch.setattr(helper, "get_settings", lambda: SimpleNamespace(debug=True))
    monkeypatch.setattr(helper, "SessionLocal", lambda: db)
    monkeypatch.setattr(helper.UserCRUD, "get_user_by_email", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["set_local_user_plan", "--email", "missing@example.com", "--plan", "plus"])
    with pytest.raises(SystemExit, match="User not found"):
        helper.main()


def test_helper_changes_only_plan(monkeypatch):
    user = SimpleNamespace(email="user@example.com", plan=PlanTier.FREE, quota_exempt=True)

    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def commit(self): pass
        def refresh(self, value): assert value is user

    monkeypatch.setattr(helper, "get_settings", lambda: SimpleNamespace(debug=True))
    monkeypatch.setattr(helper, "SessionLocal", DB)
    monkeypatch.setattr(helper.UserCRUD, "get_user_by_email", lambda *args: user)
    monkeypatch.setattr("sys.argv", ["set_local_user_plan", "--email", user.email, "--plan", "pro"])
    helper.main()
    assert user.plan == PlanTier.PRO
    assert user.quota_exempt is True

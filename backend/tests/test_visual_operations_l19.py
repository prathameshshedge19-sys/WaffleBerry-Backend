"""Phase C operator integration must fail closed without logging private errors."""
import json
from scripts import l19_visual_health_check as cli


def test_operator_health_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "health_snapshot", lambda: {"status": "ok", "pending_purge_count": 0})
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["pending_purge_count"] == 0


def test_operator_health_failed_threshold(monkeypatch, capsys):
    monkeypatch.setattr(cli, "health_snapshot", lambda: {"status": "error", "oldest_purge_age_seconds": 3600})
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["oldest_purge_age_seconds"] == 3600


def test_operator_health_sanitizes_unexpected_error(monkeypatch, capsys):
    def broken():
        raise RuntimeError("private database connection details")
    monkeypatch.setattr(cli, "health_snapshot", broken)
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "private database" not in output
    assert json.loads(output)["code"] == "visual_health_unavailable"


def test_operator_initialization_error_is_sanitized():
    import os
    import subprocess
    import sys
    environment = dict(os.environ, DATABASE_URL="not-a-valid-private-connection", JWT_SECRET_KEY="invalid-private-secret")
    result = subprocess.run([sys.executable, "-m", "scripts.l19_visual_health_check"], env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "visual_health_unavailable"
    assert "private-connection" not in result.stdout + result.stderr
    assert "private-secret" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr

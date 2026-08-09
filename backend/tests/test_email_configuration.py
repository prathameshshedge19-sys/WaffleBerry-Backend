"""Regression checks for reproducible, explicit SMTP configuration."""

from pathlib import Path


def test_fastapi_mail_dependency_is_pinned():
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text()
    assert "fastapi-mail==1.6.5" in requirements.splitlines()


def test_mail_configuration_has_no_eager_int_none_or_case_sensitive_flags():
    source = (Path(__file__).parents[1] / "app" / "services" / "email_service.py").read_text()
    assert 'int(os.getenv("MAIL_PORT"))' not in source
    assert '== "True"' not in source
    assert "Email configuration is incomplete; missing:" in source

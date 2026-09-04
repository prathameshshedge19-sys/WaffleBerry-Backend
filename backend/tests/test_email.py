from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.models.auth_challenge import AuthChallenge
from app.services.challenges import create_challenge
from app.services.email import EmailSender, build_auth_message, render_auth_email


def test_verification_email_has_premium_html_and_plain_text():
    rendered = render_auth_email(code="842163", purpose="registration", expiry_minutes=10)

    assert rendered.subject == "Verify your email | LegaRya"
    assert "842163" in rendered.text
    assert "8 4 2 1 6 3" in rendered.html
    assert "This code expires in 10 minutes." in rendered.text
    assert "This code expires in 10 minutes." in rendered.html
    assert '<span style="color:#F5EEE4;">Lega</span>' in rendered.html
    assert '<span style="color:#FFE2A8;">Rya</span>' in rendered.html
    assert "LegaRya by WaffleBerry" in rendered.text
    assert "<script" not in rendered.html.casefold()
    assert "http://" not in rendered.html and "https://" not in rendered.html


def test_password_reset_email_is_multipart_and_uses_reset_copy():
    message = build_auth_message(
        recipient="person@example.com",
        code="731904",
        purpose="password_reset",
    )
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()

    assert message["Subject"] == "Reset your password | LegaRya"
    assert message.is_multipart()
    assert "Your password reset code is:" in plain
    assert "731904" in plain
    assert "7 3 1 9 0 4" in html
    assert "Use the code below to reset your LegaRya password." in html


def test_challenge_and_email_share_configured_expiry(test_context):
    _client, sessions, _codes, _provider = test_context
    settings = get_settings()
    before = datetime.now(timezone.utc)
    with sessions() as db:
        create_challenge(db, email="expiry@example.com", purpose="registration", full_name="Expiry")
        challenge = db.scalar(select(AuthChallenge).where(AuthChallenge.email == "expiry@example.com"))
        expires_at = challenge.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta_minutes = (expires_at - before).total_seconds() / 60

    rendered = render_auth_email(
        code="842163",
        purpose="registration",
        expiry_minutes=settings.auth_code_expire_minutes,
    )
    assert settings.auth_code_expire_minutes - 0.1 <= delta_minutes <= settings.auth_code_expire_minutes + 0.1
    assert f"expires in {settings.auth_code_expire_minutes} minutes" in rendered.text


def test_email_sender_delivers_multipart_message(monkeypatch):
    sent = []
    configured_settings = get_settings().model_copy(update={
        "mail_server": "smtp.example.test",
        "mail_username": "test-user",
        "mail_password": "test-password",
        "mail_from": "no-reply@example.test",
    })

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self):
            pass

        def login(self, _username, _password):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.services.email.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("app.services.email.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("app.services.email.get_settings", lambda: configured_settings)
    EmailSender().send_code(
        recipient="person@example.com",
        code="842163",
        purpose="registration",
    )

    assert len(sent) == 1
    assert sent[0].is_multipart()
    assert sent[0].get_body(preferencelist=("plain",)) is not None
    assert sent[0].get_body(preferencelist=("html",)) is not None

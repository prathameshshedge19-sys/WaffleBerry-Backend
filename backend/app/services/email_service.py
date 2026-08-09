import asyncio
from html import escape
import logging
from pathlib import Path

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema
from pydantic import EmailStr
from dotenv import load_dotenv
import os


logger = logging.getLogger(__name__)

EMAIL_ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "email"
MASCOT_ASSET = EMAIL_ASSET_DIRECTORY / "waffle-berry-mascot.png"
BERRY_ICON_ASSET = EMAIL_ASSET_DIRECTORY / "berry-icon-1024.png"
MASCOT_CID = "waffleberry-mascot"
BERRY_ICON_CID = "waffleberry-berry-icon"

load_dotenv()


def _mail_connection_config() -> ConnectionConfig:
    """Build validated SMTP configuration with predictable error messages."""
    names = (
        "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_FROM", "MAIL_PORT",
        "MAIL_SERVER", "MAIL_STARTTLS", "MAIL_SSL_TLS",
    )
    values = {name: (os.getenv(name) or "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "Email configuration is incomplete; missing: "
            + ", ".join(missing)
        )
    try:
        port = int(values["MAIL_PORT"])
    except ValueError as exc:
        raise RuntimeError("MAIL_PORT must be an integer.") from exc

    def boolean(name: str) -> bool:
        value = values[name].casefold()
        if value not in {"true", "false"}:
            raise RuntimeError(f"{name} must be true or false.")
        return value == "true"

    starttls = boolean("MAIL_STARTTLS")
    ssl_tls = boolean("MAIL_SSL_TLS")
    if starttls and ssl_tls:
        raise RuntimeError("MAIL_STARTTLS and MAIL_SSL_TLS cannot both be true.")
    return ConnectionConfig(
        MAIL_USERNAME=values["MAIL_USERNAME"],
        MAIL_PASSWORD=values["MAIL_PASSWORD"],
        MAIL_FROM=values["MAIL_FROM"],
        MAIL_PORT=port,
        MAIL_SERVER=values["MAIL_SERVER"],
        MAIL_STARTTLS=starttls,
        MAIL_SSL_TLS=ssl_tls,
        USE_CREDENTIALS=True,
    )


def _inline_brand_attachments() -> list[dict]:
    """Return backend-owned WaffleBerry images as CID inline attachments."""
    return [
        {
            "file": str(MASCOT_ASSET),
            "headers": {
                "Content-ID": f"<{MASCOT_CID}>",
                "Content-Disposition": (
                    'inline; filename="waffle-berry-mascot.png"'
                ),
            },
            "mime_type": "image",
            "mime_subtype": "png",
        },
        {
            "file": str(BERRY_ICON_ASSET),
            "headers": {
                "Content-ID": f"<{BERRY_ICON_CID}>",
                "Content-Disposition": (
                    'inline; filename="berry-icon-1024.png"'
                ),
            },
            "mime_type": "image",
            "mime_subtype": "png",
        },
    ]


def _render_otp_email(otp: str, purpose: str) -> str:
    """Render one conservative, email-client-safe WaffleBerry OTP card."""
    is_password_reset = purpose == "password_reset"
    title = (
        "Reset your WaffleBerry password"
        if is_password_reset
        else "Verify your email"
    )
    introduction = (
        "We received a request to reset the password for your WaffleBerry "
        "account. Use the code below to continue."
        if is_password_reset
        else "Use the verification code below to verify your email address "
        "and continue creating your WaffleBerry account."
    )
    request_notice = (
        "If you did not request a password reset, you can safely ignore "
        "this email."
        if is_password_reset
        else "If you did not create a WaffleBerry account, you can safely "
        "ignore this email."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
</head>
<body style="margin:0; padding:0; background-color:#f5f1eb; color:#2d2530; font-family:Arial, Helvetica, sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; background-color:#f5f1eb;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; max-width:600px; background-color:#ffffff; border:1px solid #eadfe7; border-radius:18px;">
          <tr>
            <td align="center" style="padding:30px 32px 20px; border-bottom:1px solid #f0e7ed;">
              <img src="cid:{MASCOT_CID}" width="64" alt="WaffleBerry mascot" style="display:block; width:64px; height:auto; margin:0 auto 12px; border:0; outline:none; text-decoration:none;">
              <div style="font-size:28px; line-height:34px; font-weight:700; color:#7b285f; letter-spacing:-0.5px;">WaffleBerry</div>
              <div style="padding-top:5px; font-size:13px; line-height:20px; color:#796d77;">Preserving stories, memories, and connection</div>
            </td>
          </tr>
          <tr>
            <td style="padding:34px 38px 12px;">
              <h1 style="margin:0 0 14px; font-size:27px; line-height:35px; font-weight:700; color:#2d2530;">{escape(title)}</h1>
              <p style="margin:0; font-size:16px; line-height:25px; color:#5f545d;">{escape(introduction)}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 38px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%; background-color:#f8f1f6; border:1px solid #e8d5e2; border-radius:14px;">
                <tr>
                  <td align="center" style="padding:25px 20px 23px;">
                    <div style="font-size:13px; line-height:18px; font-weight:700; color:#7b285f; letter-spacing:1.2px; text-transform:uppercase;">Your one-time code</div>
                    <div style="padding:10px 0 8px; font-size:44px; line-height:52px; font-weight:800; color:#4e153f; letter-spacing:9px;">{escape(otp)}</div>
                    <div style="font-size:14px; line-height:20px; color:#796d77;">This code expires in 10 minutes.</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 38px 34px;">
              <p style="margin:0 0 10px; font-size:14px; line-height:22px; color:#5f545d;"><strong style="color:#3d323b;">Keep this code private.</strong> Never share this OTP with anyone. WaffleBerry will never ask you for your OTP or password.</p>
              <p style="margin:0; font-size:14px; line-height:22px; color:#796d77;">{escape(request_notice)}</p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:24px 30px 28px; background-color:#3f1835; border-radius:0 0 18px 18px;">
              <img src="cid:{BERRY_ICON_CID}" width="32" alt="WaffleBerry berry icon" style="display:block; width:32px; height:auto; margin:0 auto 9px; border:0; outline:none; text-decoration:none;">
              <div style="font-size:16px; line-height:23px; font-weight:700; color:#ffffff;">WaffleBerry</div>
              <div style="padding-top:7px; font-size:13px; line-height:20px; color:#e7cddd;">No one is truly gone while their story can still be told.</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

class EmailService:
    @staticmethod
    async def send_otp(
        email: EmailStr,
        otp: str,
        purpose: str = "email_verification"
    ):
        logger.info("[email] Preparing OTP message.")
        is_password_reset = purpose == "password_reset"
        message = MessageSchema(
            subject=(
                "Reset your WaffleBerry password"
                if is_password_reset
                else "Verify your WaffleBerry account"
            ),
            recipients=[email],
            body=_render_otp_email(otp, purpose),
            subtype="html",
            attachments=_inline_brand_attachments(),
        )

        fm = FastMail(_mail_connection_config())
        logger.info("[email] Starting SMTP send.")
        try:
            await asyncio.wait_for(
                fm.send_message(message),
                timeout=30,
            )
        except TimeoutError:
            logger.exception("[email] SMTP send timed out after 30 seconds.")
            raise
        except Exception:
            logger.exception("[email] SMTP send failed.")
            raise

        logger.info("[email] SMTP send completed.")

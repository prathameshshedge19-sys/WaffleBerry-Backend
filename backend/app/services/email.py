import smtplib
import logging
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

from app.config import get_settings


logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    pass


@dataclass(frozen=True)
class RenderedAuthEmail:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class RenderedAccessInvite:
    subject: str
    text: str
    html: str


def render_email_cta(*, label: str, url: str, vertical_padding: int = 28) -> str:
    """Render an intrinsic-width CTA that survives Gmail's conservative CSS support."""
    return f"""<!-- BEGIN LEGARYA EMAIL CTA -->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border-collapse:collapse;">
  <tr>
    <td align="center" style="padding:{vertical_padding}px 12px;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="border-collapse:separate;">
        <tr>
          <td align="center" bgcolor="#E8BD73" style="background-color:#E8BD73;border-radius:7px;">
            <a href="{escape(url, quote=True)}" style="display:inline-block;padding:14px 26px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:20px;font-weight:600;color:#171008;text-decoration:none;border-radius:7px;white-space:nowrap;">{escape(label)}</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
<!-- END LEGARYA EMAIL CTA -->"""


def _present_subject_name(subject_name: str) -> str:
    """Improve all-lower/all-upper presentation without mutating stored identity."""
    value = " ".join(subject_name.split()).strip() or "this Legacy"
    letters = "".join(character for character in value if character.isalpha())
    return value.title() if letters and (letters.islower() or letters.isupper()) else value


def render_access_invitation_email(*, inviter_name: str, subject_name: str, role: str, invite_url: str,
                                   expiry_days: int) -> RenderedAccessInvite:
    if role == "collaborator":
        subject = f"An invitation to {subject_name}'s Legacy | LegaRya"
        text = (f"LegaRya\nRemember You, Always.\n\n{inviter_name} invited you to collaborate on "
                f"{subject_name}'s Legacy.\n\nAccept invitation: {invite_url}\n\n"
                f"This private, single-use link expires in {expiry_days} days.\n")
        cta = render_email_cta(label="Accept invitation", url=invite_url)
        html = f"""<!doctype html><html><body style="margin:0;background:#050504;color:#f5eee4;font-family:Arial,sans-serif">
    <table role="presentation" width="100%"><tr><td align="center" style="padding:40px 16px"><table role="presentation" width="100%" style="max-width:600px;background:#0b0a08;border:1px solid #4b3a25;border-radius:12px"><tr><td style="padding:42px;text-align:center">
    <div style="font:34px Georgia,serif">Lega<span style="color:#ffe2a8">Rya</span></div><p style="color:#9e9282;letter-spacing:2px;font-size:10px">REMEMBER YOU, ALWAYS.</p>
    <h1 style="font:30px Georgia,serif">A private invitation</h1><p style="color:#c7baaa;line-height:1.7">{escape(inviter_name)} invited you to collaborate on <strong>{escape(subject_name)}'s Legacy</strong>.</p>
    {cta}
    <p style="color:#8f8578;font-size:12px;line-height:1.6">This link is private, single-use, and expires in {expiry_days} days. It only works for the invited email address.</p>
    </td></tr></table></td></tr></table></body></html>"""
        return RenderedAccessInvite(subject=subject, text=text, html=html)

    display_name = _present_subject_name(subject_name)
    possessive = f"{display_name}’s Legacy"
    subject = f"A private invitation to {display_name}'s AI Legacy | LegaRya"
    text = (f"LegaRya\nRemember You, Always.\n\nA PRIVATE INVITATION\n\n"
            f"{inviter_name} has invited you to talk with {possessive}.\n\n"
            f"An AI Legacy created from the memories and stories preserved about {display_name}.\n\n"
            f"Talk with {possessive}: {invite_url}\n\n"
            f"This private invitation is single-use, expires in {expiry_days} days, and only works for the invited email address.\n")
    cta = render_email_cta(label=f"Talk with {possessive}", url=invite_url, vertical_padding=18)
    html = f"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(subject)}</title></head>
  <body style="margin:0;padding:0;background-color:#F3EFE7;color:#241C14;font-family:Arial,Helvetica,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">A private invitation to talk with {escape(possessive)}.</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border-collapse:collapse;background-color:#F3EFE7;">
      <tr><td align="center" style="padding:20px 12px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:560px;border-collapse:separate;background-color:#FFFDF8;border:1px solid #D7C39F;border-radius:12px;">
          <tr><td align="center" style="padding:25px 24px 11px;">
            <div style="font-family:Georgia,'Times New Roman',serif;font-size:31px;font-weight:bold;line-height:1;color:#2A2118;"><span>Lega</span><span style="color:#B98231;">Rya</span></div>
            <div style="padding-top:8px;font-family:Arial,Helvetica,sans-serif;font-size:9px;line-height:1.4;letter-spacing:2px;text-transform:uppercase;color:#8B7A66;">REMEMBER YOU, ALWAYS.</div>
          </td></tr>
          <tr><td align="center" style="padding:14px 24px 0;">
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;font-weight:bold;line-height:1.4;letter-spacing:2px;text-transform:uppercase;color:#A06F28;">A private invitation</div>
            <h1 style="margin:11px auto 0;max-width:470px;font-family:Georgia,'Times New Roman',serif;font-size:27px;font-weight:normal;line-height:1.22;color:#241C14;">{escape(inviter_name)} has invited you<br>to talk with <span style="white-space:nowrap;">{escape(possessive)}</span>.</h1>
            <p style="margin:13px auto 0;max-width:430px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.6;color:#6F6253;">An AI Legacy created from the memories and stories preserved about {escape(display_name)}.</p>
          </td></tr>
          <tr><td style="padding:0 16px;">{cta}</td></tr>
          <tr><td style="padding:0 24px;"><div style="border-top:1px solid #E4D7C0;font-size:1px;line-height:1px;">&nbsp;</div></td></tr>
          <tr><td align="center" style="padding:13px 26px 23px;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:1.55;color:#8B7F70;">This private invitation is single-use, expires in {expiry_days} days, and only works for the invited email address.</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
    return RenderedAccessInvite(subject=subject, text=text, html=html)


def build_access_invitation_message(*, recipient: str, inviter_name: str, subject_name: str, role: str,
                                    invite_url: str) -> EmailMessage:
    settings = get_settings()
    rendered = render_access_invitation_email(inviter_name=inviter_name, subject_name=subject_name,
        role=role, invite_url=invite_url, expiry_days=settings.access_invite_expire_days)
    message = EmailMessage()
    message["Subject"] = rendered.subject
    message["From"] = settings.mail_from or "LegaRya <no-reply@legarya.local>"
    message["To"] = recipient
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    return message


_EMAIL_COPY = {
    "registration": {
        "subject": "Verify your email | LegaRya",
        "heading": "Verify your email",
        "body": "Use the verification code below to continue setting up your LegaRya account.",
        "plain_intro": "Your verification code is:",
    },
    "password_reset": {
        "subject": "Reset your password | LegaRya",
        "heading": "Reset your password",
        "body": "Use the code below to reset your LegaRya password.",
        "plain_intro": "Your password reset code is:",
    },
}


def render_auth_email(*, code: str, purpose: str, expiry_minutes: int) -> RenderedAuthEmail:
    """Render compatible plain-text and table-based HTML authentication email bodies."""
    if purpose not in _EMAIL_COPY:
        raise ValueError("Unsupported authentication email purpose.")
    if len(code) != 6 or not code.isdigit():
        raise ValueError("Authentication code must contain exactly six digits.")
    copy = _EMAIL_COPY[purpose]
    expiry_label = f"{expiry_minutes} minute" + ("" if expiry_minutes == 1 else "s")
    spaced_code = " ".join(code)

    text = f"""LegaRya
Remember You, Always.

{copy['heading']}

{copy['plain_intro']}

{code}

This code expires in {expiry_label}.

If you didn't request this code, you can safely ignore this email.

LegaRya by WaffleBerry
Remember You, Always.
"""

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(copy['subject'])}</title>
  </head>
  <body style="margin:0;padding:0;background-color:#050504;color:#F5EEE4;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{escape(copy['body'])}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#050504;">
      <tr>
        <td align="center" style="padding:36px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background-color:#0B0A08;border:1px solid #33291C;border-radius:12px;">
            <tr>
              <td style="padding:40px 42px 16px;text-align:center;">
                <div style="font-family:Georgia,'Times New Roman',serif;font-size:34px;font-weight:bold;line-height:1;color:#F5EEE4;"><span style="color:#F5EEE4;">Lega</span><span style="color:#FFE2A8;">Rya</span></div>
                <div style="padding-top:9px;font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:1.4;letter-spacing:2px;text-transform:uppercase;color:#9E9282;">Remember You, Always.</div>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 42px 0;text-align:center;">
                <h1 style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:31px;font-weight:normal;line-height:1.2;color:#F5EEE4;">{escape(copy['heading'])}</h1>
                <p style="margin:15px auto 0;max-width:440px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.65;color:#B9AD9C;">{escape(copy['body'])}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:30px 42px 12px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#100D09;border:1px solid #5A4529;border-radius:9px;">
                  <tr>
                    <td align="center" style="padding:24px 10px;font-family:Arial,Helvetica,sans-serif;font-size:32px;font-weight:bold;line-height:1;letter-spacing:4px;word-spacing:2px;color:#FFE2A8;">{spaced_code}</td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:10px 42px 0;text-align:center;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.6;color:#F3C982;">This code expires in {expiry_label}.</td>
            </tr>
            <tr>
              <td style="padding:25px 42px 38px;text-align:center;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.65;color:#8F8578;">If you didn't request this code, you can safely ignore this email.</td>
            </tr>
            <tr>
              <td style="border-top:1px solid #2A2218;padding:22px 42px 26px;text-align:center;font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:1.6;color:#786F64;">LegaRya by WaffleBerry<br><span style="color:#9E9282;">Remember You, Always.</span></td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return RenderedAuthEmail(subject=copy["subject"], text=text, html=html)


def build_auth_message(*, recipient: str, code: str, purpose: str) -> EmailMessage:
    settings = get_settings()
    rendered = render_auth_email(
        code=code,
        purpose=purpose,
        expiry_minutes=settings.auth_code_expire_minutes,
    )
    message = EmailMessage()
    message["Subject"] = rendered.subject
    message["From"] = settings.mail_from
    message["To"] = recipient
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    return message


class EmailSender:
    def send_code(self, *, recipient: str, code: str, purpose: str) -> None:
        settings = get_settings()
        if not all((settings.mail_server, settings.mail_username, settings.mail_password, settings.mail_from)):
            if settings.legarya_debug:
                return
            raise EmailDeliveryError("Email delivery is not configured.")
        stage = "message_build"
        try:
            message = build_auth_message(recipient=recipient, code=code, purpose=purpose)
            stage = "smtp_connect"
            smtp_class = smtplib.SMTP_SSL if settings.mail_ssl_tls else smtplib.SMTP
            with smtp_class(settings.mail_server, settings.mail_port, timeout=15) as smtp:
                if settings.mail_starttls:
                    stage = "smtp_starttls"
                    smtp.starttls()
                stage = "smtp_authenticate"
                smtp.login(settings.mail_username, settings.mail_password)
                stage = "smtp_send"
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException, TypeError, ValueError) as exc:
            logger.error(
                "Authentication email delivery failed at stage=%s error_type=%s",
                stage,
                type(exc).__name__,
            )
            raise EmailDeliveryError("Unable to deliver authentication email.") from None

    def send_invitation(self, *, recipient: str, inviter_name: str, subject_name: str, role: str,
                        invite_url: str) -> None:
        settings = get_settings()
        if not all((settings.mail_server, settings.mail_username, settings.mail_password, settings.mail_from)):
            if settings.legarya_debug:
                return
            raise EmailDeliveryError("Email delivery is not configured.")
        message = build_access_invitation_message(recipient=recipient, inviter_name=inviter_name,
            subject_name=subject_name, role=role, invite_url=invite_url)
        try:
            smtp_class = smtplib.SMTP_SSL if settings.mail_ssl_tls else smtplib.SMTP
            with smtp_class(settings.mail_server, settings.mail_port, timeout=15) as smtp:
                if settings.mail_starttls: smtp.starttls()
                smtp.login(settings.mail_username, settings.mail_password); smtp.send_message(message)
        except (OSError, smtplib.SMTPException, TypeError, ValueError):
            logger.error("Access invitation email delivery failed", exc_info=True)
            raise EmailDeliveryError("Unable to deliver access invitation.") from None


email_sender = EmailSender()

"""Send one token-free viewer invitation smoke to the configured controlled mailbox."""
from app.config import get_settings
from app.services.email import email_sender


def main() -> None:
    settings = get_settings()
    recipient = settings.mail_username if settings.mail_username and "@" in settings.mail_username else settings.mail_from
    if not recipient:
        raise RuntimeError("No configured controlled mailbox is available for the invitation smoke.")
    email_sender.send_invitation(
        recipient=recipient,
        inviter_name="Prathamesh Shedge",
        subject_name="pallavi",
        role="viewer",
        invite_url="https://example.invalid/invite.html?token=dummy-viewer-gmail-preview",
    )
    print("viewer: SMTP delivery accepted")


if __name__ == "__main__":
    main()

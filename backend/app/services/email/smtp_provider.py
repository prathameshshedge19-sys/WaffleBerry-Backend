"""Email provider using SMTP."""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import get_settings
from app.services.email.provider import EmailProvider

logger = logging.getLogger(__name__)


class SMTPEmailProvider(EmailProvider):
    """Email provider that uses SMTP to send emails."""

    def __init__(self):
        """Initialize SMTP configuration."""
        self.settings = get_settings()
        self.smtp_host = self.settings.smtp_host
        self.smtp_port = self.settings.smtp_port
        self.smtp_username = self.settings.smtp_username
        self.smtp_password = self.settings.smtp_password
        self.smtp_use_tls = self.settings.smtp_use_tls
        self.smtp_timeout = self.settings.smtp_timeout_seconds
        self.from_address = self.settings.email_from_address
        self.from_name = self.settings.email_from_name

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        """Send email via SMTP."""
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_address}>"
            msg["To"] = to_email

            # Attach text and HTML parts
            if text_content:
                msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            # Send via SMTP
            with smtplib.SMTP(
                self.smtp_host,
                self.smtp_port,
                timeout=self.smtp_timeout,
            ) as server:
                if self.smtp_use_tls:
                    server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

            logger.info(f"Email sent successfully to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

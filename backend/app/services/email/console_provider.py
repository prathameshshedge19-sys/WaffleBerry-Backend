"""Email provider that logs to console for development."""

import logging
from app.services.email.provider import EmailProvider

logger = logging.getLogger(__name__)


class ConsoleEmailProvider(EmailProvider):
    """Development email provider that logs to console instead of sending."""

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        """Log email to console instead of sending."""
        logger.info(
            f"[CONSOLE EMAIL] To: {to_email}\n"
            f"Subject: {subject}\n"
            f"HTML Content:\n{html_content}\n"
            f"Text Content:\n{text_content or '(no text content)'}"
        )
        return True

"""Email service modules."""

from app.services.email.provider import EmailProvider
from app.services.email.console_provider import ConsoleEmailProvider
from app.services.email.smtp_provider import SMTPEmailProvider
from app.services.email.registration_email_service import RegistrationEmailService

__all__ = [
    "EmailProvider",
    "ConsoleEmailProvider",
    "SMTPEmailProvider",
    "RegistrationEmailService",
]

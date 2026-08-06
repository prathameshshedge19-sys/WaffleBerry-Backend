"""Email service for registration OTP emails."""

import logging
from app.config import get_settings
from app.services.email.provider import EmailProvider
from app.services.email.console_provider import ConsoleEmailProvider
from app.services.email.smtp_provider import SMTPEmailProvider

logger = logging.getLogger(__name__)


class RegistrationEmailService:
    """Service for sending registration-related emails."""

    def __init__(self, provider: EmailProvider | None = None):
        """Initialize with email provider."""
        if provider:
            self.provider = provider
        else:
            settings = get_settings()
            if settings.email_provider == "smtp":
                self.provider = SMTPEmailProvider()
            else:
                self.provider = ConsoleEmailProvider()

    async def send_otp_email(
        self,
        to_email: str,
        otp: str,
        otp_ttl_seconds: int,
        full_name: str | None = None,
    ) -> bool:
        """
        Send OTP verification email.
        
        Args:
            to_email: Recipient email address
            otp: The OTP code (never revealed in actual email - just for reference)
            otp_ttl_seconds: How long OTP is valid for (in seconds)
            full_name: Optional recipient name
            
        Returns:
            True if email was sent successfully
        """
        settings = get_settings()
        otp_minutes = otp_ttl_seconds // 60

        # Generate email content
        subject = "Verify your WaffleBerry email"
        
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 500px; margin: 0 auto;">
                    <h2>Verify Your Email</h2>
                    <p>{"Hello " + full_name + "," if full_name else "Hello,"}</p>
                    
                    <p>Use this code to verify your email on WaffleBerry:</p>
                    
                    <div style="background-color: #f0f0f0; padding: 20px; text-align: center; font-size: 32px; letter-spacing: 5px; font-weight: bold; border-radius: 5px;">
                        {otp}
                    </div>
                    
                    <p><strong>This code expires in {otp_minutes} minutes.</strong></p>
                    <p>This code is single-use and cannot be reused after verification.</p>
                    
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    
                    <p style="font-size: 12px; color: #666;">
                        If you didn't request this email, you can safely ignore it.
                    </p>
                    <p style="font-size: 12px; color: #666;">
                        Never share this code with anyone. We will never ask for this code via email.
                    </p>
                </div>
            </body>
        </html>
        """

        text_content = f"""
        Verify Your Email

        {"Hello " + full_name + "," if full_name else "Hello,"}

        Use this code to verify your email on WaffleBerry:

        {otp}

        This code expires in {otp_minutes} minutes.
        This code is single-use and cannot be reused after verification.

        -----

        If you didn't request this email, you can safely ignore it.

        Never share this code with anyone. We will never ask for this code via email.
        """

        return await self.provider.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

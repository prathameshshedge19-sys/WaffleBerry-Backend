import asyncio
import logging

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema
from pydantic import EmailStr
from dotenv import load_dotenv
import os


logger = logging.getLogger(__name__)

load_dotenv()

conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_STARTTLS=os.getenv("MAIL_STARTTLS") == "True",
    MAIL_SSL_TLS=os.getenv("MAIL_SSL_TLS") == "True",
    USE_CREDENTIALS=True,
)

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
            body=f"""
Hello!

Your WaffleBerry {"password reset" if is_password_reset else "verification"} code is:

{otp}

This code expires in 10 minutes.

If you did not request this, you can ignore this email.
            """,
            subtype="plain",
        )

        fm = FastMail(conf)
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

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema
from pydantic import EmailStr
from dotenv import load_dotenv
import os

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
    async def send_otp(email: EmailStr, otp: str):
        message = MessageSchema(
            subject="Verify your WaffleBerry account",
            recipients=[email],
            body=f"""
Hello!

Your WaffleBerry verification code is:

{otp}

This code expires in 10 minutes.

If you did not create an account, you can ignore this email.
            """,
            subtype="plain",
        )

        fm = FastMail(conf)
        await fm.send_message(message)
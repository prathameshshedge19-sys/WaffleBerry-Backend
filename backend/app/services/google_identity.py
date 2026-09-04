from dataclasses import dataclass

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from app.config import get_settings
from app.services.security import normalize_email


class GoogleIdentityError(Exception):
    pass


class GoogleIdentityConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


def verify_google_credential(credential: str) -> GoogleIdentity:
    client_id = (get_settings().google_web_client_id or "").strip()
    if not client_id:
        raise GoogleIdentityConfigurationError
    try:
        claims = id_token.verify_oauth2_token(credential, Request(), client_id)
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"} or claims.get("email_verified") is not True:
            raise ValueError
        sub = str(claims["sub"]).strip()
        email = normalize_email(str(claims["email"]))
        name = str(claims.get("name") or email.split("@", 1)[0]).strip()[:255]
        if not sub or not email:
            raise ValueError
        return GoogleIdentity(sub=sub, email=email, name=name)
    except (GoogleAuthError, KeyError, TypeError, ValueError):
        raise GoogleIdentityError from None

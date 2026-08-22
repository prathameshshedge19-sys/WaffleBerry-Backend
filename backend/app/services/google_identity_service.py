"""Backend-only validation of Google Identity Services ID credentials."""
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.auth.exceptions import GoogleAuthError
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config import get_settings


class GoogleIdentityError(Exception):
    """A Google credential did not establish an acceptable identity."""


class GoogleIdentityConfigurationError(Exception):
    """Google authentication is not configured for this deployment."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None


_email_adapter = TypeAdapter(EmailStr)
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def verify_google_credential(credential: str) -> GoogleIdentity:
    """Verify a GIS ID token and return only trusted normalized claims."""
    client_id = (get_settings().google_web_client_id or "").strip()
    if not client_id:
        raise GoogleIdentityConfigurationError
    try:
        claims = id_token.verify_oauth2_token(credential, Request(), client_id)
        if claims.get("iss") not in _GOOGLE_ISSUERS:
            raise ValueError("issuer")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise ValueError("subject")
        if claims.get("email_verified") is not True:
            raise ValueError("email verification")
        email = str(_email_adapter.validate_python(claims.get("email"))).strip().casefold()
    except (ValueError, TypeError, ValidationError, GoogleAuthError):
        raise GoogleIdentityError from None

    def optional_text(key: str) -> str | None:
        value = claims.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return GoogleIdentity(
        sub=sub.strip(), email=email, name=optional_text("name"),
        given_name=optional_text("given_name"), family_name=optional_text("family_name"),
    )

"""Write safe, non-user authentication email previews to the OS temp directory."""
from pathlib import Path
from tempfile import gettempdir

from app.config import get_settings
from app.services.email import render_auth_email


def main() -> None:
    output = Path(gettempdir()) / "legarya-email-previews"
    output.mkdir(parents=True, exist_ok=True)
    expiry = get_settings().auth_code_expire_minutes
    for purpose, filename in (
        ("registration", "verification.html"),
        ("password_reset", "password-reset.html"),
    ):
        rendered = render_auth_email(
            code="842163",
            purpose=purpose,
            expiry_minutes=expiry,
        )
        (output / filename).write_text(rendered.html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

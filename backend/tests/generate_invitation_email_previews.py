"""Generate safe local invitation previews using dummy recipients and non-routable links."""
from pathlib import Path

from app.services.email import render_access_invitation_email


def main() -> None:
    output = Path(__file__).parent / "previews"
    output.mkdir(exist_ok=True)
    for role in ("collaborator", "viewer"):
        preview = render_access_invitation_email(
            inviter_name="Prathamesh Preview",
            subject_name="Pallavi Preview",
            role=role,
            invite_url=f"https://example.invalid/invite.html?token=dummy-{role}-preview",
            expiry_days=7,
        )
        (output / f"invitation-{role}.html").write_text(preview.html, encoding="utf-8")


if __name__ == "__main__":
    main()

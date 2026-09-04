import re

import pytest

from app.services.email import build_access_invitation_message, render_access_invitation_email, render_email_cta


@pytest.mark.parametrize(("role", "expected_copy", "cta_label"), [
    ("collaborator", "collaborate on", "Accept invitation"),
    ("viewer", "talk with", "Talk with Pallavi’s Legacy"),
])
def test_both_invitation_variants_use_one_table_safe_cta(role, expected_copy, cta_label):
    url = f"https://example.invalid/invite.html?token=preview-{role}&source=email"
    rendered = render_access_invitation_email(inviter_name="Preview Owner", subject_name="Pallavi",
        role=role, invite_url=url, expiry_days=7)
    assert expected_copy in rendered.html
    assert rendered.html.count(cta_label) == 1
    cta = rendered.html.split("<!-- BEGIN LEGARYA EMAIL CTA -->", 1)[1].split("<!-- END LEGARYA EMAIL CTA -->", 1)[0]
    assert cta.count('role="presentation"') == 2
    assert 'align="center"' in cta and 'bgcolor="#E8BD73"' in cta
    assert 'display:inline-block' in cta and 'padding:14px 26px' in cta and 'line-height:20px' in cta
    assert "display:flex" not in cta and "display:grid" not in cta and "position:" not in cta
    assert not re.search(r"(?<!line-)height\s*:", cta)
    assert "<div" not in cta and "<p" not in cta
    assert 'href="https://example.invalid/invite.html?token=preview-' in cta
    assert "&amp;source=email" in cta


def test_shared_cta_escapes_link_and_contains_no_duplicate_label():
    cta = render_email_cta(label="Accept invitation", url='https://example.invalid/?a=1&b=two"unsafe')
    assert cta.count("Accept invitation") == 1
    assert "&amp;b=two&quot;unsafe" in cta


def test_viewer_invitation_is_compact_light_personal_and_ai_transparent():
    rendered = render_access_invitation_email(inviter_name="Prathamesh Shedge", subject_name="pallavi",
        role="viewer", invite_url="https://example.invalid/invite.html?token=viewer", expiry_days=7)
    assert rendered.subject == "A private invitation to Pallavi's AI Legacy | LegaRya"
    assert "Prathamesh Shedge has invited you" in rendered.html
    assert "talk with <span" in rendered.html and "Pallavi’s Legacy" in rendered.html
    assert "An AI Legacy created from the memories and stories preserved about Pallavi." in rendered.html
    assert rendered.html.count("Talk with Pallavi’s Legacy") == 1
    assert "background-color:#FFFDF8" in rendered.html and "border:1px solid #D7C39F" in rendered.html
    assert "padding:20px 12px" in rendered.html and "padding:13px 26px 23px" in rendered.html
    assert "padding:40px 16px" not in rendered.html and "padding:42px" not in rendered.html
    assert "min-height" not in rendered.html and not re.search(r"(?<!line-)(?<!max-)height\s*:\s*\d", rendered.html)
    assert "display:flex" not in rendered.html and "display:grid" not in rendered.html
    assert "position:absolute" not in rendered.html and "vh" not in rendered.html and "clamp(" not in rendered.html
    assert "single-use, expires in 7 days, and only works for the invited email address" in rendered.html
    assert "pallavi’s Legacy" not in rendered.html


def test_mixed_case_subject_identity_is_preserved_without_storage_mutation():
    stored = "Mary-Jane McDonald"
    rendered = render_access_invitation_email(inviter_name="Owner", subject_name=stored, role="viewer",
        invite_url="https://example.invalid/invite", expiry_days=3)
    assert "Mary-Jane McDonald’s Legacy" in rendered.html
    assert stored == "Mary-Jane McDonald"


@pytest.mark.parametrize("role", ["collaborator", "viewer"])
def test_invitation_message_remains_multipart_with_plain_and_html_parts(role):
    url = f"https://example.invalid/invite.html?token=preview-{role}"
    message = build_access_invitation_message(recipient="recipient@example.invalid", inviter_name="Preview Owner",
        subject_name="Pallavi", role=role, invite_url=url)
    assert message.is_multipart()
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    expected = "Accept invitation" if role == "collaborator" else "Talk with Pallavi’s Legacy"
    assert f"{expected}: {url}" in plain
    assert html.count(expected) == 1 and url in html
    assert "Remember You, Always." in plain and "REMEMBER YOU, ALWAYS." in html
    assert "single-use" in plain and "7 days" in html


def test_collaborator_template_copy_and_spacing_remain_approved():
    rendered = render_access_invitation_email(inviter_name="Prathamesh Shedge", subject_name="Pallavi",
        role="collaborator", invite_url="https://example.invalid/invite", expiry_days=7)
    assert "Prathamesh Shedge invited you to collaborate on <strong>Pallavi's Legacy</strong>." in rendered.html
    assert rendered.html.count("Accept invitation") == 1
    assert "padding:40px 16px" in rendered.html and "padding:42px" in rendered.html
    assert "background:#0b0a08" in rendered.html

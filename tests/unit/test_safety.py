"""Output safety applies to every format: secrets, private links, scripts, remote URLs."""

from __future__ import annotations

from artifactsmith.renderers.safety import check_content, sanitize_text


def test_strips_scripts_and_urls():
    raw = '<p><script>alert(1)</script><a href="https://example.com/x">x</a></p>'
    out = sanitize_text(raw)
    assert "<script" not in out.lower()
    assert "https://" not in out


def test_html_must_be_complete_document():
    problems = check_content("<p>no html tags</p>", fmt="html")
    assert any("complete HTML" in p for p in problems)


def test_rejects_private_links():
    body = "see http://127.0.0.1:8080/admin and http://localhost/secrets"
    problems = check_content(body, fmt="markdown", block_private_links=True)
    assert any("private or local" in p for p in problems)


def test_rejects_secrets():
    body = "token sk-" + ("a" * 24)
    problems = check_content(body, fmt="markdown")
    assert any("possible secret" in p for p in problems)


def test_allow_list_rejects_other_hosts():
    body = "https://evil.example/path"
    problems = check_content(
        body,
        fmt="markdown",
        block_private_links=False,
        allowed_link_domains=["good.example"],
    )
    assert any("allow-list" in p for p in problems)


def test_markdown_rejects_surviving_urls():
    problems = check_content("go to https://example.com/now", fmt="pdf")
    assert any("remote http" in p for p in problems)


def test_size_cap():
    problems = check_content("x" * 50, fmt="markdown", max_chars=10)
    assert any("exceeds" in p for p in problems)

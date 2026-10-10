"""Output safety: secrets, private links, scripts; public http(s) URLs allowed."""

from __future__ import annotations

import pytest

from artifactsmith.renderers.safety import check_content, check_fields, find_private_links, sanitize_text


def test_strips_scripts_keeps_public_urls():
    raw = "<p><script>alert(1)</script>see https://example.com/x for more</p>"
    out = sanitize_text(raw)
    assert "<script" not in out.lower()
    assert "https://example.com/x" in out


def test_check_content_flags_confused_html_href():
    raw = '<a href="https://example.com @127.0.0.1/x">x</a>'
    problems = check_content(raw, fmt="markdown", block_private_links=True)
    assert any("authority-confused HTML" in p or "private or local" in p for p in problems)


def test_check_content_allows_public_html_href():
    raw = '<a href="https://example.com/docs">docs</a>'
    problems = check_content(raw, fmt="markdown", block_private_links=True)
    assert not any("HTML href" in p or "private or local" in p for p in problems)


def test_check_content_ignores_href_in_fenced_code():
    raw = (
        "# Email links\n\nUse [NSF](https://www.nsf.gov/).\n\n```html\n"
        '<a href="mailto:me@example.com">Email me</a>\n'
        '<a href="tel:+15551234567">call</a>\n```\n'
    )
    out = sanitize_text(raw)
    assert out == raw
    problems = check_content(out, fmt="markdown", block_private_links=True)
    assert not any("HTML" in p for p in problems)


def test_html_pre_code_mailto_snippet_builds():
    doc = (
        "<!DOCTYPE html><html><head><title>t</title></head><body>"
        '<pre><code>&lt;a href="mailto:hello@example.com"&gt;</code></pre>'
        "</body></html>"
    )
    problems = check_content(doc, fmt="html", block_private_links=True)
    assert problems == []


@pytest.mark.parametrize(
    "url",
    [
        "http://llm.dunker-mahi.ts.net/",
        "https://foo.bar.ts.net/path",
        "http://ts.net/",
    ],
)
def test_rejects_tailscale_ts_net_hosts(url: str) -> None:
    hits = find_private_links(f"see {url} please")
    assert hits, f"expected private hit for {url}"
    problems = check_content(f"see {url}", fmt="markdown", block_private_links=True)
    assert any("private or local" in p for p in problems)


def test_html_must_be_complete_document():
    problems = check_content("<p>no html tags</p>", fmt="html")
    assert any("complete HTML" in p for p in problems)


def test_rejects_private_links():
    body = "see http://127.0.0.1:8080/admin and http://localhost/secrets"
    problems = check_content(body, fmt="markdown", block_private_links=True)
    assert any("private or local" in p for p in problems)


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1]/",
        "http://127.1/",
        "http://0x7f.0.0.1/",
        "http://0177.0.0.1/",
        "http://2130706433/",
        "http://127.0.0.1.sslip.io/",
        "https://192.168.1.5.nip.io/path",
        "http://10-0-0-1.sslip.io/",
        "http://100.64.1.2/",
        "http://[fd7a:115c:a1e0::1]/",  # Tailscale IPv6 ULA
        "http://127%2e0%2e0%2e1/",
        "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",  # full-width 127.0.0.1
        "http://127.0.0.1.traefik.me/",
        "http://[::ffff:10.0.0.1]/",
        "http://[::10.0.0.1]/",
        "http://app.home.arpa/",
        "http://files.lan/",
    ],
)
def test_rejects_obfuscated_private_link_forms(url):
    hits = find_private_links(f"see {url} please")
    assert hits, f"expected private hit for {url}"
    problems = check_fields(url, label="summary")
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


def test_public_urls_allowed_in_every_format():
    body = "cite https://example.com/paper"
    for fmt in ("html", "markdown", "pdf", "docx", "xlsx"):
        if fmt == "html":
            doc = f"<html><body><p>{body}</p></body></html>"
            problems = check_content(doc, fmt="html", block_private_links=True)
        else:
            problems = check_content(body, fmt=fmt, block_private_links=True)
        assert not any("remote http" in p for p in problems), (fmt, problems)
        assert not any("private or local" in p for p in problems), (fmt, problems)


def test_size_cap():
    problems = check_content("x" * 50, fmt="markdown", max_chars=10)
    assert any("exceeds" in p for p in problems)


def test_check_fields_catches_secret_in_title():
    from artifactsmith.renderers.safety import check_fields

    secret = "sk-" + ("a" * 24)
    problems = check_fields(secret, label="metadata")
    assert any("possible secret" in p for p in problems)

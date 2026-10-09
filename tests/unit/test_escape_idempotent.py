"""Fail-closed markdown escaping must run exactly once (idempotent)."""

from __future__ import annotations

from artifactsmith.renderers.md_sanitize import sanitize_markdown
from artifactsmith.renderers.md_serialize import escape_all_md_punctuation, escape_md_text


def test_escape_all_md_punctuation_idempotent_no_triple_backslash():
    """Already-escaped ``\\[`` must not become ``\\\\\\[`` (three backslashes)."""
    already = r"a\[x]"
    once = escape_all_md_punctuation(already)
    twice = escape_all_md_punctuation(once)
    assert once == twice
    # Exactly one escape before the bracket, not three.
    assert once == r"a\[x\]"
    assert r"\\\[" not in once


def test_escape_all_on_plain_then_stable():
    plain = "a[x]"
    once = escape_all_md_punctuation(plain)
    assert once == r"a\[x\]"
    assert escape_all_md_punctuation(once) == once


def test_escape_md_text_escapes_raw_token_content_once():
    assert escape_md_text("hello[world]") == r"hello\[world\]"


def test_fail_closed_escapes_colon_and_at():
    """Scheme/authority markers must be escaped so readers cannot re-linkify."""
    once = escape_all_md_punctuation("https://127.0.0.1/x user@host")
    assert once == r"https\://127\.0\.0\.1/x user\@host"
    assert escape_all_md_punctuation(once) == once
    # TAB-split scheme cannot reassemble into a live URL after fail-closed.
    tricky = "https:\t//127.0.0.1/x"
    esc = escape_all_md_punctuation(tricky)
    assert "https://" not in esc
    assert r"https\:" in esc
    assert escape_all_md_punctuation(esc) == esc


def test_sanitize_idempotent_with_fallback_shaped_escapes():
    raw = r"text with \\[not a link]"
    once = sanitize_markdown(raw)
    twice = sanitize_markdown(once)
    assert twice == once
    # Re-applying fail-closed escape must not add backslashes.
    assert escape_all_md_punctuation(once) == once

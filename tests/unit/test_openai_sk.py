"""OpenAI sk- keys with proj, svcacct, and admin prefixes.

Page body (HTML), Markdown, and summary/metadata must agree. Invisible
characters are stripped before the match. Short hyphenated prose stays
under the 20-character floor.
"""

from __future__ import annotations

import re

import pytest

from artifactsmith.renderers.safety import (
    SECRET_PATTERNS,
    check_content,
    check_fields,
    find_secrets,
)

_HIT = "possible secret (openai_sk)"
# 20 characters, and it contains both "_" and "-".
_MARKED = "Ab0_Cd-Ef1Gh2_Ij-Kl3"
_EXAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"


def _page(text: str) -> str:
    return f"<!DOCTYPE html><html><head><title>t</title></head><body><p>{text}</p></body></html>"


def _openai_pattern() -> re.Pattern[str]:
    return next(pat for label, pat in SECRET_PATTERNS if label == "openai_sk")


def _assert_flagged(text: str) -> None:
    assert _HIT in find_secrets(text)
    assert any(_HIT in p for p in check_fields(text, label="summary"))
    assert any(_HIT in p for p in check_fields(text, label="metadata"))
    assert _HIT in check_content(_page(text), fmt="html")
    assert _HIT in check_content(text, fmt="markdown")


def _assert_clean(text: str) -> None:
    assert find_secrets(text) == []
    assert check_fields(text, label="summary") == []
    assert check_fields(text, label="metadata") == []
    assert check_content(_page(text), fmt="html") == []
    assert check_content(text, fmt="markdown") == []


def test_marked_body_is_twenty_with_symbols() -> None:
    assert len(_MARKED) == 20
    assert "_" in _MARKED and "-" in _MARKED
    assert len(_EXAMPLE) >= 20


@pytest.mark.parametrize("prefix", ["sk-proj-", "sk-svcacct-", "sk-admin-"])
def test_prefixed_keys_flagged_in_page_markdown_and_summary(prefix: str) -> None:
    _assert_flagged(f"token {prefix}{_MARKED} in the doc")


def test_reported_proj_example_flagged() -> None:
    _assert_flagged("sk-proj-" + _EXAMPLE)


def test_legacy_sk_still_flagged() -> None:
    _assert_flagged("token sk-" + ("b" * 20))


def test_underscore_and_hyphen_count_toward_floor() -> None:
    _assert_flagged("sk-" + _MARKED)


@pytest.mark.parametrize(
    "sep",
    [
        pytest.param("\u200b", id="zwsp"),
        pytest.param("&#8203;", id="zwsp-entity"),
    ],
)
def test_key_split_by_zero_width_flagged(sep: str) -> None:
    left, right = "Ab0_Cd-Ef1", "Gh2_Ij-Kl3"
    raw = "sk-proj-" + left + sep + right
    assert _openai_pattern().search(raw) is None
    _assert_flagged(raw)


@pytest.mark.parametrize(
    "text",
    [
        "sk-...",
        "sk-your-key-here",
        "scikit sk-learn",
        "sk-learn",
        "Use sk-learn for the model.",
        "placeholder sk-your-key-here in the docs",
        "sk-" + ("b" * 19),
        "sk-proj-short",
        "sk-svcacct-example",
        "sk-admin-placeholder",
    ],
)
def test_prose_and_placeholders_not_flagged(text: str) -> None:
    _assert_clean(text)

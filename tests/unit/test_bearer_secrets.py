"""generic_bearer: prose and placeholders pass; real tokens fail, including short ones.

Every case from the PR #25 review is here. Metadata (check_fields) and body
(check_content html and markdown) must agree.
"""

from __future__ import annotations

import pytest

from artifactsmith.renderers.safety import check_content, check_fields, find_secrets

_HIT = "possible secret (generic_bearer)"
_H16 = "9f3a1c7e5b2d8a40"
_HEX32 = _H16 * 2
_B16 = "q7mK2pLx9vN4sD8w"
_JWT = "eyJ" + ("A" * 24) + "." + ("e" * 24) + "." + ("Z" * 24)


def _page(text: str) -> str:
    return f"<!DOCTYPE html><html><head><title>t</title></head><body><p>{text}</p></body></html>"


def _split(sep: str) -> str:
    return f"Bearer {_HEX32[:16]}{sep}{_HEX32[16:]}"


def _assert_bearer(text: str) -> None:
    assert _HIT in find_secrets(text)
    assert any(_HIT in p for p in check_fields(text, label="metadata"))
    assert _HIT in check_content(_page(text), fmt="html")
    assert _HIT in check_content(text, fmt="markdown")


def _assert_clean(text: str) -> None:
    assert find_secrets(text) == []
    assert check_fields(text, label="metadata") == []
    assert check_content(_page(text), fmt="html") == []
    assert check_content(text, fmt="markdown") == []


def _assert_label(text: str, label: str) -> None:
    hit = f"possible secret ({label})"
    assert hit in find_secrets(text)
    assert any(hit in p for p in check_fields(text, label="metadata"))
    assert hit in check_content(_page(text), fmt="html")
    assert hit in check_content(text, fmt="markdown")


@pytest.mark.parametrize(
    "text",
    [
        "Bearer key",
        "Bearer token",
        "Bearer tokens",
        "Bearer auth",
        "Bearer authentication",
        "Bearer Authentication",
        "Bearer KEY",
        "Bearer TOKEN",
        "Bearer API",
        "bearer key",
        "BEARER KEY",
        "Bearer: key",
        "Authorization: Bearer <token>",
        "Authorization: Bearer <your-token>",
        "Bearer YOUR_API_KEY",
        "Bearer ${TOKEN}",
        "Bearer $ARTIFACTSMITH_TOKEN",
        "Bearer $VAR",
        "poll the execution until done. Bearer key. History and data pulls use /api/v1",
        "Bearer YOUR_ARTIFACTSMITH_TOKEN_HERE",
        "Bearer your-api-key-goes-here",
        "Bearer REPLACE_WITH_YOUR_TOKEN",
        "Bearer access_token",
        "Bearer " + ("x" * 8),
        "Bearer " + ("x" * 32),
        "Bearer " + ("*" * 8),
        "Bearer qwmkzplxvnsdwrcf",
        "Bearer&nbsp;key",
        "Bearer k\u200be\u200by",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ],
)
def test_bearer_prose_and_placeholders_pass(text: str) -> None:
    _assert_clean(text)


@pytest.mark.parametrize(
    "text",
    [
        f"Bearer {_H16}",
        f"Bearer {_H16}0",
        f"Bearer {_H16}01",
        f"Bearer {_H16}012",
        "Bearer 9f3a1c7e",
        f"Bearer {_B16}",
        f"Bearer {_B16}R",
        f"Bearer {_B16}R1",
        f"Bearer {_B16}R1c",
        f"Bearer {_B16}R1cF",
        "Bearer YXJ0aWZhY3RzbWl0aA==",
        "Bearer YXJ0aWZhY3RzbWl0aC1kb2NzLXRva2VuIQ==",
        "Bearer YXJ0aWZhY3RzbWl0aC1kb2NzLXRva2VuISE=",
        "Bearer AB12_CD34_EF56_GH",
        "Bearer a1b2c3d4e5f6g7h8i9j",
        f"Bearer {_JWT}",
        "Bearer sk-" + ("a" * 24),
        _split("\u200b"),
        _split("\u200d"),
        _split("\u00ad"),
        _split("\u00a0"),
        f"Bearer {_HEX32[:16]}&#8203;{_HEX32[16:]}",
        f"Bearer\u00a0{_B16}R1cF",
        f"curl -H 'Authorization: Bearer {_H16}'",
        '{"Authorization": "Bearer ' + _H16 + '"}',
        f"`Authorization: Bearer {_H16}`",
        f"Bearer&nbsp;{_H16}",
        f"Bearer&#32;{_H16}",
        f"bearer {_H16}",
        f"BEARER {_H16}",
        f"Bearer: {_H16}",
        f'Bearer "{_H16}"',
        f"Bearer '{_H16}'",
        f"Bearer `{_H16}`",
        "\uff22\uff45\uff41\uff52\uff45\uff52 " + _H16,
    ],
)
def test_bearer_realistic_tokens_flagged(text: str) -> None:
    _assert_bearer(text)


@pytest.mark.parametrize(
    "sep",
    [
        pytest.param("\u034f", id="cgj-034f"),
        pytest.param("\ufe0f", id="vs16-fe0f"),
        pytest.param("\U000e0020", id="tag-e0020"),
        pytest.param("\u2028", id="line-sep-2028"),
        pytest.param("\u061c", id="alm-061c"),
    ],
)
def test_bearer_word_prefix_split_by_invisible(sep: str) -> None:
    _assert_bearer(f"Bearer abcdef{sep}12345XYZ7890qwer")


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("github_pat", "ghp_" + ("a" * 36)),
        ("github_pat", "Bearer ghp_" + ("a" * 36)),
        ("github_fine_grained", "github_pat_" + ("a" * 22)),
        ("github_fine_grained", "Bearer github_pat_" + ("a" * 22)),
        ("slack_token", "xoxb-" + ("a" * 12)),
        ("slack_token", "Bearer xoxb-" + ("a" * 12)),
        ("aws_access_key", "AKIA" + ("A" * 16)),
        ("aws_access_key", "Bearer AKIA" + ("A" * 16)),
        ("openai_sk", "sk-" + ("b" * 20)),
        ("pem_private_key", "-----BEGIN " + "PRIVATE KEY-----"),
        ("github_pat", "gh" + "\u200b" + "p_" + ("a" * 36)),
        ("openai_sk", "sk" + "\u200b" + "-" + ("a" * 24)),
        ("slack_token", "xoxb" + "\u200b" + "-" + ("a" * 12)),
        ("aws_access_key", "AK" + "\u200b" + "IA" + ("A" * 16)),
        ("github_pat", "gh&#8203;p_" + ("a" * 36)),
    ],
)
def test_other_secret_patterns_still_flag(label: str, text: str) -> None:
    _assert_label(text, label)


_ELLIPSIS = "\u2026"


@pytest.mark.parametrize(
    "text",
    [
        "Bearer <token>",
        "Bearer asmb_" + _ELLIPSIS,
        "Bearer asmb_...",
        "Bearer YOUR_TOKEN",
        "$AM_TOKEN",
        "${AM_TOKEN}",
    ],
)
def test_named_placeholders_pass(text: str) -> None:
    _assert_clean(text)


@pytest.mark.parametrize(
    "text",
    [
        "Bearer asmb_",
        "Bearer asmb_.",
        "Bearer asmb_,",
        "Bearer asmb_....",
        "Bearer asmb_..." + _H16,
        "Bearer asmb_" + _ELLIPSIS + _H16,
        "Bearer asmb_" + ("\uff0e" * 3),
    ],
)
def test_asmb_near_misses_still_flag(text: str) -> None:
    _assert_bearer(text)


def test_placeholder_does_not_hide_another_secret() -> None:
    _assert_label("Bearer asmb_... ghp_" + ("a" * 36), "github_pat")

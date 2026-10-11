"""OpenAI sk- keys with proj, svcacct, and admin prefixes.

Page body (HTML), Markdown, and summary/metadata must agree. Invisible
characters are stripped before the match. Short hyphenated prose stays
under the 20-character floor.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

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


# Known residual: "sk-proj-" + "2026-roadmap-final-draft" stays flagged (prefix plus a digit).
# Acceptable. Hyphenated prose without that shape must stay clean.
_B1_PROSE = [
    "sk-learn-compatible-estimator-interface",
    "the sk-learn-style-fit-predict-transform API",
    "use sk-learn-based-classification-models",
    "high-risk-sk-learn-pipeline-components",
    "a kiosk-sk-learn-demo-for-the-trade-show",
    "https://example.com/blog/sk-learn-pipeline-tutorial-guide",
    "sk-learn_pipeline_final_version_v2",
    "sk-proj-management-best-practices-guide",
    "sk-admin-dashboard-and-settings-page",
    "sk-svcacct-rotation-policy-overview",
    "sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "sk-your-openai-api-key-goes-here",
    "risk-free-trial-period-for-everyone",
]


@pytest.mark.parametrize("text", _B1_PROSE)
def test_hyphenated_prose_and_placeholders_not_flagged(text: str) -> None:
    _assert_clean(text)


def test_prefix_plus_digit_roadmap_still_flagged() -> None:
    _assert_flagged("sk-proj-" + "2026-roadmap-final-draft")


_MIXED = "aB3cDeFgHiJkLmNoPqRsTuVw"


def test_embedded_proj_key_flagged() -> None:
    _assert_flagged("foo-sk-proj-" + _MIXED)


def test_proj_key_with_trailing_hyphen_flagged() -> None:
    _assert_flagged("sk-proj-" + _MIXED + "-")


def test_zero_width_between_sk_and_proj_flagged() -> None:
    raw = "sk-\u200bproj-" + _MIXED
    assert _openai_pattern().search(raw) is None
    _assert_flagged(raw)


def test_repeated_sk_prefix_stays_fast() -> None:
    started = time.perf_counter()
    assert find_secrets("sk-" * 666_000) == []
    assert time.perf_counter() - started < 2.0


def _denylist_secret_re() -> str:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "denylist.sh").read_text(encoding="utf-8")
    match = re.search(r"^SECRET_RE='([^']*)'", script, re.M)
    assert match is not None
    return match.group(1)


def test_denylist_ignores_risk_free_trial_phrase(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("risk-free-trial-period-for-everyone\n", encoding="utf-8")
    result = subprocess.run(
        ["grep", "-E", "-n", _denylist_secret_re(), str(sample)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""

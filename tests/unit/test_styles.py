"""HTML visual style presets: house is exact; others swap only the visual block."""

from __future__ import annotations

import pytest

from artifactsmith import builder as b
from artifactsmith.config import Config, ConfigError
from artifactsmith.styles import (
    ALLOWED_STYLES,
    DEFAULT_STYLE,
    EMOJI_STYLES,
    STYLE_HELP,
    VISUAL_BLOCKS,
    StyleError,
    html_system_prompt,
    parse_style,
)

_WRITING = (
    "The reader is busy",
    "Never invent facts",
    "===NEEDS_INPUT===",
    "Preserve explicitly verbatim text",
    "never override factual accuracy",
    "Do not use javascript:",
    "===FILE: index.html===",
    "Follow the house style below for wording and look",
)

_PRESET_MARKERS = {
    "bold": ("deep-navy-to-violet gradient hero", "pill badges", "Emoji may be used as visual icons"),
    "editorial": ("magazine feature", "Georgia", "drop cap", "deep red", "No emoji"),
    "playful": ("Cream background", "neo-brutalist", "coral, sunflower, mint", "Emoji may be used as visual icons"),
    "terminal": ("Near-black", "monospace", "neon green", "shell prompt", "CSS gradients"),
    "swiss": ("Helvetica", "signal-orange", "no rounded corners", "No emoji"),
}

_QUALITY = (
    "iPhone width",
    "readable",
    "tables for tabular data",
    "HTML and CSS only",
)


def test_house_prompt_is_exact_current_text():
    assert html_system_prompt(b.SYSTEM, "house") == b.SYSTEM
    assert b.system_prompt_for("html") == b.SYSTEM
    assert b.system_prompt_for("html", "house") == b.SYSTEM
    assert parse_style(None) == DEFAULT_STYLE
    assert parse_style("") == DEFAULT_STYLE


@pytest.mark.parametrize("style", sorted(ALLOWED_STYLES - {DEFAULT_STYLE}))
def test_preset_keeps_writing_rules_and_own_visual_block(style: str):
    prompt = b.system_prompt_for("html", style)
    assert prompt != b.SYSTEM
    for phrase in _WRITING:
        assert phrase in prompt, phrase
    visual = VISUAL_BLOCKS[style]
    assert visual.rstrip() in prompt
    assert "Spend boldness in one place" not in prompt
    for phrase in _PRESET_MARKERS[style]:
        assert phrase in prompt, phrase
    for phrase in _QUALITY:
        assert phrase in prompt, phrase
    if style in EMOJI_STYLES:
        assert "No emoji." not in prompt
    else:
        assert "No emoji." in prompt


def test_non_html_formats_ignore_style():
    assert b.system_prompt_for("pdf", "bold") == b.CONTENT_SYSTEM
    assert b.system_prompt_for("markdown", "swiss") == b.CONTENT_SYSTEM


def test_invalid_style_lists_allowed():
    with pytest.raises(StyleError, match=STYLE_HELP):
        parse_style("neon")
    with pytest.raises(StyleError, match=STYLE_HELP):
        b.system_prompt_for("html", "neon")


def test_invalid_default_style_fails_at_config_load(monkeypatch):
    monkeypatch.setenv("AM_DEFAULT_STYLE", "neon")
    with pytest.raises(ConfigError, match="AM_DEFAULT_STYLE"):
        Config()
    monkeypatch.setenv("AM_DEFAULT_STYLE", "editorial")
    assert Config().default_style == "editorial"

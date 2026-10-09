"""Builder parses model output, keeps house-style HTML path, and routes other formats to renderers."""

from __future__ import annotations

import pytest

from artifactsmith import builder as b


def test_parse_output_html():
    text = """===ASSUMPTIONS===
- none
===SUMMARY===
A page.
===FILE: index.html===
<html><body>ok</body></html>
===END===
"""
    assumptions, summary, body = b.parse_output(text)
    assert assumptions == []
    assert summary == "A page."
    assert "<html>" in body


def test_needs_input_block():
    text = "===NEEDS_INPUT===\nNeed the store code.\n===END===\n"
    assert "store code" in (b.needs_input(text) or "")


def test_needs_input_ignored_when_file_present():
    text = "===FILE: index.html===\n<html></html>\n===NEEDS_INPUT===\nnope\n"
    assert b.needs_input(text) is None


def test_render_html_wrapper():
    files, primary = b.render_html("<html><body>x</body></html>")
    assert primary == "index.html"
    assert files[primary].startswith(b"<html>")


@pytest.mark.asyncio
async def test_run_build_html(monkeypatch):
    async def fake_call(model, system, user, timeout=300):
        assert "===FILE: index.html===" in system
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\npage\n"
            "===FILE: index.html===\n<html><body>HARBOR-17</body></html>\n===END===\n"
        )

    monkeypatch.setattr(b.llm, "call", fake_call)
    notes = []
    res = await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Pilot",
        verbatim="show code",
        model="mock",
        base_source=None,
        base_version=None,
        history=[],
        progress=notes.append,
        source={"source_content": "The pilot store code is HARBOR-17."},
        format="html",
    )
    assert res.primary == "index.html"
    assert b"HARBOR-17" in res.files["index.html"]


@pytest.mark.asyncio
async def test_run_build_pdf(monkeypatch):
    async def fake_call(model, system, user, timeout=300):
        assert "===FILE: content.md===" in system
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\ndoc\n"
            "===FILE: content.md===\n# Pilot\n\nThe code is HARBOR-17.\n===END===\n"
        )

    monkeypatch.setattr(b.llm, "call", fake_call)
    res = await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Pilot",
        verbatim="show code",
        model="mock",
        base_source=None,
        base_version=None,
        history=[],
        progress=lambda _m: None,
        format="pdf",
    )
    assert res.primary == "document.pdf"
    assert res.files["document.pdf"].startswith(b"%PDF")
    assert b"HARBOR-17" in res.files["content.md"]


@pytest.mark.asyncio
async def test_run_build_rejects_secret(monkeypatch):
    async def fake_call(model, system, user, timeout=300):
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\nbad\n"
            "===FILE: content.md===\n# x\n\nsk-" + ("b" * 24) + "\n===END===\n"
        )

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="secret"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="mock",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="markdown",
        )


@pytest.mark.asyncio
async def test_run_build_needs_input(monkeypatch):
    async def fake_call(model, system, user, timeout=300):
        return "===NEEDS_INPUT===\nNeed revenue.\n===END===\n"

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.NeedsInput, match="revenue"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="mock",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="html",
        )

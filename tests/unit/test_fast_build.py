"""Fast HTML builds: one model call, one fallback, no concurrent calls."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from artifactsmith import builder as b
from artifactsmith.builder import BuildError, NeedsInput
from artifactsmith.config import CFG, Config, ConfigError
from artifactsmith.llm import apply_effort
from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.html_sanitize import sanitize_html_document
from artifactsmith.renderers.pdf import DenyAllURLFetcher

ROOT = Path(__file__).resolve().parents[2]
_PAGE = "# Pilot\n\nThe code is HARBOR-17.\n"


def _md(body: str) -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\npage\n===FILE: page.md===\n{body}\n===END===\n"


def _html(body: str) -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\npage\n===FILE: index.html===\n{body}\n===END===\n"


async def _build(monkeypatch, fake, **kwargs):
    monkeypatch.setattr(b.llm, "call", fake)
    return await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Pilot",
        verbatim="show the code",
        model="gpt-4o-mini",
        base_source=None,
        base_version=None,
        history=[],
        progress=lambda _msg: None,
        format="html",
        mode="fast",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_fast_build_is_one_call(monkeypatch):
    state = {"n": 0, "peak": 0, "calls": 0}

    async def fake(model, system, user, timeout=300, *, effort=None):
        assert effort == "low"
        assert "page.md" in system
        state["calls"] += 1
        state["n"] += 1
        state["peak"] = max(state["peak"], state["n"])
        await asyncio.sleep(0.05)
        state["n"] -= 1
        return _md(_PAGE)

    res = await _build(monkeypatch, fake)
    assert state["calls"] == 1
    assert state["peak"] == 1
    assert res.primary == "index.html"
    assert b"HARBOR-17" in res.files["index.html"]
    assert b"HARBOR-17" in res.files["page.md"]
    assert b"<html" not in res.files["page.md"].lower()


@pytest.mark.asyncio
async def test_unusable_markdown_falls_back_to_html_once(monkeypatch):
    seen: list[tuple[str, str | None]] = []

    async def fake(model, system, user, timeout=300, *, effort=None):
        seen.append((system, effort))
        if len(seen) == 1:
            return _md("<!DOCTYPE html><html><body><p>not markdown</p></body></html>")
        return _html("<html><body><p>HARBOR-17</p></body></html>")

    res = await _build(monkeypatch, fake)
    assert len(seen) == 2
    assert "page.md" in seen[0][0]
    assert seen[0][1] == "low"
    assert "index.html" in seen[1][0]
    assert seen[1][1] is None
    assert b"HARBOR-17" in res.files["index.html"]
    assert "fast layout fell back to full HTML" in res.notes
    assert "page.md" not in res.files


@pytest.mark.asyncio
async def test_secret_retries_fast_prompt_not_html(monkeypatch):
    secret = "sk-" + "proj-" + "ABCDEFGHIJ" + "KLMNOPQRSTUVWXYZ0123456789abcd"
    seen: list[str] = []

    async def fake(model, system, user, timeout=300, *, effort=None):
        assert effort == "low"
        assert "===FILE: page.md===" in system
        seen.append(system)
        if len(seen) == 1:
            return _md(f"# Pilot\n\n{secret}\n")
        return _md(_PAGE)

    res = await _build(monkeypatch, fake)
    assert len(seen) == 2
    assert all("===FILE: page.md===" in system for system in seen)
    assert b"HARBOR-17" in res.files["index.html"]
    assert secret.encode() not in res.files["index.html"]


@pytest.mark.asyncio
async def test_needs_input_does_not_call_twice(monkeypatch):
    state = {"calls": 0}

    async def fake(model, system, user, timeout=300, *, effort=None):
        state["calls"] += 1
        return "===NEEDS_INPUT===\nNeed the store code.\n===END===\n"

    with pytest.raises(NeedsInput, match="store code"):
        await _build(monkeypatch, fake)
    assert state["calls"] == 1


@pytest.mark.asyncio
async def test_fast_mode_ignored_for_markdown(monkeypatch):
    async def fake(model, system, user, timeout=300):
        assert "===FILE: content.md===" in system
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\ndoc\n"
            "===FILE: content.md===\n# Pilot\n\nThe code is HARBOR-17.\n===END===\n"
        )

    monkeypatch.setattr(b.llm, "call", fake)
    res = await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Pilot",
        verbatim="show the code",
        model="gpt-4o-mini",
        base_source=None,
        base_version=None,
        history=[],
        progress=lambda _msg: None,
        format="markdown",
        mode="fast",
    )
    assert res.primary == "document.md"


@pytest.mark.asyncio
async def test_bad_mode_rejected():
    with pytest.raises(BuildError, match="mode"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="Pilot",
            verbatim="x",
            model="gpt-4o-mini",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _msg: None,
            mode="turbo",
        )


def test_effort_field_matches_model_name():
    chat = {"model": "gpt-4o-mini", "messages": []}
    assert apply_effort(chat, "gpt-4o-mini", "low") is chat
    reasoned = apply_effort(chat, "gpt-5-mini", "low")
    assert reasoned["reasoning_effort"] == "low"
    responses = apply_effort({"model": "o3-mini", "input": []}, "o3-mini", "low")
    assert responses["reasoning"] == {"effort": "low"}


def test_invalid_build_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("AM_BUILD_MODE", "turbo")
    with pytest.raises(ConfigError, match="AM_BUILD_MODE"):
        Config()
    monkeypatch.setenv("AM_BUILD_MODE", "fast")
    assert Config().build_mode == "fast"
    assert CFG.build_mode == "full"


def test_layout_worker_bounds_bytes_and_errors():
    from artifactsmith.layout.build import _layout_worker

    class Conn:
        def __init__(self) -> None:
            self.msg: tuple[object, ...] | None = None
            self.closed = False

        def send(self, msg: tuple[object, ...]) -> None:
            self.msg = msg

        def close(self) -> None:
            self.closed = True

    ok = Conn()
    _layout_worker("house", "T", "# T\n\nHello\n", 10**6, ok)
    assert ok.closed
    assert ok.msg is not None and ok.msg[0] == "ok"
    files = ok.msg[1]
    assert isinstance(files, dict)
    assert b"Hello" in files["index.html"]

    small = Conn()
    _layout_worker("house", "T", "# T\n\nHello\n", 10, small)
    assert small.msg is not None and small.msg[0] == "too_large"

    empty = Conn()
    _layout_worker("house", "T", "", 10**6, empty)
    assert empty.msg is not None and empty.msg[0] == "error"
    assert str(empty.msg[1]).startswith("LayoutError")


@pytest.mark.asyncio
async def test_fast_render_keeps_the_event_loop_responsive(monkeypatch):
    import time

    from artifactsmith.renderers.subprocess_render import render_killable

    seen: dict[str, object] = {}

    def wrapped(**kwargs: object) -> object:
        seen["worker"] = kwargs.get("worker")
        seen["timeout"] = kwargs.get("timeout_s")
        seen["max_bytes"] = kwargs.get("max_bytes")
        time.sleep(0.45)
        return render_killable(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("artifactsmith.layout.build.render_killable", wrapped)
    gaps: list[float] = []
    stop = asyncio.Event()

    async def fake(model, system, user, timeout=300, *, effort=None):
        assert effort == "low"
        return _md(_PAGE)

    async def heart() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(heart())
    await _build(monkeypatch, fake)
    stop.set()
    await task
    assert seen["worker"] is not None
    assert isinstance(seen["timeout"], (int, float)) and seen["timeout"] > 0
    assert isinstance(seen["max_bytes"], int) and seen["max_bytes"] > 0
    assert gaps
    assert max(gaps) < 0.2


def test_exports_still_render_sample_markdown():
    from weasyprint import HTML

    source = (ROOT / "examples" / "layout" / "d-long-summary.md").read_text(encoding="utf-8")
    for fmt in ("markdown", "docx", "xlsx"):
        out = get_renderer(fmt).render(title="Summary", body=source)
        assert out.primary
        assert out.files[out.primary]
    from artifactsmith.layout.render import render_layout

    html, notes = render_layout(source, style="swiss", title="Summary")
    assert notes == []
    doc = sanitize_html_document(html)
    pdf = HTML(string=doc, base_url=".", url_fetcher=DenyAllURLFetcher()).write_pdf()
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:4] == b"%PDF"

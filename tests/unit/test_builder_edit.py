"""Edits try a patch, then fall back to a full rewrite in the same job."""

from __future__ import annotations

import logging

import httpx
import pytest

from artifactsmith import builder as b
from artifactsmith.patch import PATCH_SYSTEM_ADDENDUM
from artifactsmith.renderers.html_sanitize import HtmlSanitizeError

PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Stores</title></head>
<body>
<h1>Store report</h1>
<p>North is the lead store.</p>
<table>
<tr><th>Store</th><th>Count</th></tr>
<tr><td>North</td><td>12</td></tr>
<tr><td>South</td><td>9</td></tr>
</table>
<h2>Hours</h2>
<p>North opens at 8.</p>
<h2>Notes</h2>
<p>South is the backup.</p>
</body>
</html>
"""

SECRET = "sk-" + ("a" * 24)


def _patch(find: str, replace: str, summary: str = "Updated the page.") -> str:
    return (
        "===ASSUMPTIONS===\n- none\n===SUMMARY===\n"
        f"{summary}\n===PATCH===\n<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replace}\n<<<END>>>\n===END===\n"
    )


def _full(body: str, summary: str = "Rewrote the page.") -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\n{summary}\n===FILE: index.html===\n{body}\n===END===\n"


def _kw(**overrides: object) -> dict:
    base = {
        "kind": "web_static",
        "slug": "stores",
        "display_name": "Stores",
        "verbatim": "Set the north count to 18.",
        "model": "test-model",
        "base_source": PAGE,
        "base_version": 1,
        "history": ["First page: north count is 12."],
        "progress": lambda _msg: None,
        "format": "html",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_small_edits_apply_a_patch_and_keep_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[tuple[str, str]] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        prompts.append((system, user))
        if "Move the notes" in user:
            old = "<h2>Hours</h2>\n<p>North opens at 8.</p>\n<h2>Notes</h2>\n<p>South is the backup.</p>"
            new = "<h2>Backup</h2>\n<p>South is the backup.</p>\n<h2>Hours</h2>\n<p>North opens at 8.</p>"
            return _patch(old, new, "Moved notes above hours and retitled the section.")
        if "Retitle the page" in user:
            return _patch("<h1>Store report</h1>", "<h1>Pilot stores</h1>", "Retitled the page.")
        return _patch("<td>North</td><td>12</td>", "<td>North</td><td>18</td>", "Changed the north count to 18.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    number = await b.run_build(**_kw())
    moved = await b.run_build(**_kw(verbatim="Move the notes section above hours and retitle it Backup."))
    titled = await b.run_build(**_kw(verbatim="Retitle the page to Pilot stores."))

    assert len(prompts) == 3
    for system, user in prompts:
        assert "EDIT OUTPUT FORMAT" in system
        assert "Return the complete updated file." not in user
        assert "First page: north count is 12." in user
    page = number.files["index.html"].decode()
    assert "18" in page and "South is the backup." in page and "12" not in page
    assert number.summary == "Changed the north count to 18."
    assert "edit patch applied (1 blocks)" in number.notes
    assert number.raw_chars < len(PAGE)
    moved_page = moved.files["index.html"].decode()
    assert moved_page.index("Backup") < moved_page.index("Hours")
    assert "North is the lead store." in moved_page
    assert "Pilot stores" in titled.files["index.html"].decode()
    assert "North opens at 8." in titled.files["index.html"].decode()


@pytest.mark.asyncio
async def test_create_does_not_ask_for_a_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        seen.append(system)
        seen.append(user)
        return _full("<html><body><p>HARBOR-17</p></body></html>")

    monkeypatch.setattr(b.llm, "call", fake_call)
    await b.run_build(**_kw(base_source=None, base_version=None, history=[], verbatim="show the code"))
    assert PATCH_SYSTEM_ADDENDUM not in seen[0]
    assert "<<<FIND>>>" not in seen[1]
    assert "show the code" in seen[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch_reply",
    [
        "this is not a patch",
        _patch("NOT-IN-THE-PAGE", "18"),
    ],
)
async def test_bad_patch_falls_back_to_full_rewrite(monkeypatch: pytest.MonkeyPatch, patch_reply: str) -> None:
    calls: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls.append(user)
        if len(calls) == 1:
            return patch_reply
        body = PAGE.replace("<td>12</td>", "<td>18</td>")
        return _full(body, "Rewrote the count.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    assert len(calls) == 2
    assert "Return a patch" in calls[0]
    assert "Return the complete updated file." in calls[1]
    assert any(note.startswith("edit patch fell back:") for note in result.notes)
    assert b"18" in result.files["index.html"]
    assert b"South is the backup." in result.files["index.html"]


@pytest.mark.asyncio
async def test_patch_that_fails_checks_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls.append(system)
        if len(calls) == 1:
            return _patch("<td>12</td>", '<td><a href="http://127.0.0.1/">18</a></td>')
        return _full(PAGE.replace("<td>12</td>", "<td>18</td>"))

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    assert len(calls) == 2
    assert "127.0.0.1" not in result.files["index.html"].decode()
    assert any(note.startswith("edit patch fell back:") for note in result.notes)


@pytest.mark.asyncio
async def test_safety_checks_see_the_full_patched_document(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    real = b.check_content

    def spy(text: str, **kwargs: object) -> list[str]:
        seen.append(text)
        return real(text, **kwargs)  # type: ignore[arg-type]

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return _patch("<td>12</td>", "<td>18</td>")

    monkeypatch.setattr(b, "check_content", spy)
    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    assert seen
    assert "North is the lead store." in seen[0]
    assert "18" in seen[0]
    assert b"South is the backup." in result.files["index.html"]


@pytest.mark.asyncio
async def test_secret_outside_the_hunk_rejects_the_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    dirty = PAGE.replace("<p>North is the lead store.</p>", f"<p>{SECRET}</p>")

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return _patch("<td>12</td>", "<td>18</td>")
        return _full("<html><body><p>North count is 18.</p><p>South is the backup.</p></body></html>")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw(base_source=dirty))
    assert calls["n"] == 2
    assert SECRET not in result.files["index.html"].decode()
    assert "18" in result.files["index.html"].decode()


@pytest.mark.asyncio
async def test_script_in_a_patch_is_sanitized_like_a_full_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls["n"] += 1
        return _patch("<td>12</td>", "<td>18<script>alert(1)</script></td>")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    page = result.files["index.html"].decode().lower()
    assert calls["n"] == 1
    assert "<script" not in page
    assert "18" in page
    assert "edit patch applied (1 blocks)" in result.notes


@pytest.mark.asyncio
async def test_sanitize_failure_on_a_patch_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = b.sanitize_html

    def flaky(body: str, **_kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise HtmlSanitizeError("page too large")
        return real(body)

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        if calls["n"] == 0:
            return _patch("<td>12</td>", "<td>18</td>")
        return _full(PAGE.replace("<td>12</td>", "<td>18</td>"))

    monkeypatch.setattr(b, "sanitize_html", flaky)
    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    assert calls["n"] == 2
    assert b"18" in result.files["index.html"]
    assert any(note.startswith("edit patch fell back:") for note in result.notes)


@pytest.mark.asyncio
async def test_patch_needs_input_does_not_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls["n"] += 1
        return "===NEEDS_INPUT===\nNeed the new count.\n===END===\n"

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.NeedsInput, match="count"):
        await b.run_build(**_kw())
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_markdown_edit_patches_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    base = "# Stores\n\nNorth count is 12.\n\nSouth is the backup.\n"

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        assert "===FILE: content.md===" in system
        assert "EDIT OUTPUT FORMAT" in system
        return _patch("North count is 12.", "North count is 18.", "Updated the count.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw(base_source=base, format="markdown"))
    text = result.files["document.md"].decode()
    assert "North count is 18." in text
    assert "South is the backup." in text
    assert "edit patch applied (1 blocks)" in result.notes


@pytest.mark.asyncio
async def test_secret_in_patch_needs_input_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "===NEEDS_INPUT===\nMissing " + SECRET + "\n===END===\n"
        return _full("<html><body><p>North count is 18.</p></body></html>")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(**_kw())
    assert calls["n"] == 2
    assert SECRET not in result.files["index.html"].decode()
    assert any(note.startswith("edit patch fell back:") for note in result.notes)


@pytest.mark.asyncio
async def test_create_sanitize_error_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return _full("<html><body><p>ok</p></body></html>")

    def explode(_body: str, **_kwargs: object) -> str:
        raise HtmlSanitizeError("page too large")

    monkeypatch.setattr(b.llm, "call", fake_call)
    monkeypatch.setattr(b, "sanitize_html", explode)
    with pytest.raises(b.BuildError, match="page too large"):
        await b.run_build(**_kw(base_source=None, base_version=None, history=[]))


FACTS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Harbor</title>
<style>
main{display:block}
</style>
</head>
<body>
<main>
<section id="facts"><h2>Facts</h2><p>North leads.</p></section>
<section id="events"><h2>Events</h2><p>Open house Friday.</p></section>
</main>
</body>
</html>
"""

_CSS_MOVE = "main{display:flex;flex-direction:column}\n#events{order:1}\n#facts{order:2}"


def _events_first() -> str:
    return FACTS_PAGE.replace(
        '<section id="facts"><h2>Facts</h2><p>North leads.</p></section>\n'
        '<section id="events"><h2>Events</h2><p>Open house Friday.</p></section>',
        '<section id="events"><h2>Events</h2><p>Open house Friday.</p></section>\n'
        '<section id="facts"><h2>Facts</h2><p>North leads.</p></section>',
    )


@pytest.mark.asyncio
async def test_css_only_move_falls_back_to_a_rewrite(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls.append(user)
        if len(calls) == 1:
            return _patch("main{display:block}", _CSS_MOVE, "Moved Events above Facts.")
        return _full(_events_first(), "Moved the Events section above Facts.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    with caplog.at_level(logging.INFO, logger="artifactsmith.builder"):
        result = await b.run_build(
            **_kw(
                verbatim="Move the Events section above the Facts section",
                base_source=FACTS_PAGE,
            )
        )
    page = result.files["index.html"].decode()
    assert len(calls) == 2
    assert "Return the complete updated file." in calls[1]
    assert page.index("Events") < page.index("Facts")
    assert "order:" not in page
    assert "edit patch fell back: css-only move" in result.notes
    assert "edit patch fell back: css-only move" in caplog.text


@pytest.mark.asyncio
async def test_patch_timeout_falls_back(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    timeouts: list[float] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        timeouts.append(timeout)
        if len(timeouts) == 1:
            raise httpx.ReadTimeout("read timed out")
        return _full(PAGE.replace("<td>12</td>", "<td>18</td>"), "Rewrote the count.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    with caplog.at_level(logging.INFO, logger="artifactsmith.builder"):
        result = await b.run_build(**_kw())
    assert b.CFG.edit_patch_timeout_s == 45
    assert timeouts == [45, 300]
    assert b"18" in result.files["index.html"]
    assert "edit patch fell back: timeout" in result.notes
    assert "edit patch fell back: timeout" in caplog.text


@pytest.mark.asyncio
async def test_marker_in_hunk_falls_back_to_a_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        calls.append(user)
        if len(calls) == 1:
            return _patch("<h2>Facts</h2>", "<h2>Facts</h2>\n<<<<<<< SEARCH")
        return _full(_events_first(), "Moved the Events section above Facts.")

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(
        **_kw(
            verbatim="Move the Events section above the Facts section",
            base_source=FACTS_PAGE,
        )
    )
    page = result.files["index.html"].decode()
    assert len(calls) == 2
    assert "edit patch fell back: marker in hunk" in result.notes
    assert "<<<<<<< SEARCH" not in page
    assert "<<<" not in page
    assert page.index("Events") < page.index("Facts")

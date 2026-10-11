"""Marker lines inside a page are content. A cut-off reply fails instead of publishing."""

from __future__ import annotations

import pytest

from artifactsmith import builder as b

_PAGE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Reply format</title></head>
<body>
<h1>Reply format</h1>
<p>The builder reply uses these markers.</p>
<pre><code>===ASSUMPTIONS===
===SUMMARY===
===FILE: x===
===END===
</code></pre>
<p>The same markers as plain lines:</p>
===ASSUMPTIONS===
===SUMMARY===
===FILE: x===
===END===
<p>The rest of the page stays.</p>
</body>
</html>"""

_NORMAL = (
    "===ASSUMPTIONS===\n- used the source\n===SUMMARY===\nA page.\n"
    "===FILE: index.html===\n<html><body>ok</body></html>\n===END===\n"
)


def _reply(body: str, summary: str = "Documents the reply format.") -> str:
    return f"===ASSUMPTIONS===\n- none\n===SUMMARY===\n{summary}\n===FILE: index.html===\n{body}\n===END===\n"


def test_normal_reply_is_unchanged() -> None:
    assert b.parse_output(_NORMAL) == (["used the source"], "A page.", "<html><body>ok</body></html>")
    assert b.parse_output(_NORMAL, fmt="html") == (["used the source"], "A page.", "<html><body>ok</body></html>")


def test_markers_inside_the_page_parse_in_full() -> None:
    assumptions, summary, body = b.parse_output(_reply(_PAGE), fmt="html")
    assert assumptions == []
    assert summary == "Documents the reply format."
    assert body == _PAGE
    assert body.count("===ASSUMPTIONS===") == 2
    assert body.count("===SUMMARY===") == 2
    assert body.count("===FILE: x===") == 2
    assert body.count("===END===") == 2


def test_truncated_reply_is_an_error() -> None:
    text = (
        "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n"
        "===FILE: index.html===\n<html><body><p>stopped mid-sentence\n"
    )
    with pytest.raises(b.BuildError, match="truncated: missing ===END==="):
        b.parse_output(text, fmt="html")


@pytest.mark.asyncio
async def test_markers_inside_the_page_publish_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return _reply(_PAGE)

    monkeypatch.setattr(b.llm, "call", fake_call)
    result = await b.run_build(
        kind="web_static",
        slug="s",
        display_name="Reply format",
        verbatim="document the reply format",
        model="test-model",
        base_source=None,
        base_version=None,
        history=[],
        progress=lambda _m: None,
        format="html",
    )
    page = result.files["index.html"].decode()
    assert "The rest of the page stays." in page
    assert page.count("===ASSUMPTIONS===") == 2
    assert page.count("===SUMMARY===") == 2
    assert page.count("===FILE: x===") == 2
    assert page.count("===END===") == 2
    assert "<ASL>" not in page


@pytest.mark.asyncio
async def test_truncated_reply_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        nonlocal calls
        calls += 1
        return (
            "===ASSUMPTIONS===\n- none\n===SUMMARY===\nA page.\n"
            "===FILE: index.html===\n<html><body><p>stopped mid-sentence\n"
        )

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="truncated: missing ===END==="):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="test-model",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="html",
        )
    assert calls == 2


@pytest.mark.asyncio
async def test_file_under_20_percent_of_the_reply_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    small = "<!DOCTYPE html>\n<html><body><p>Hi</p></body></html>"
    tail = "\n".join("<p>This sentence was cut off the page and should not vanish.</p>" for _ in range(40))
    reply = _reply(small) + tail

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return reply

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="under 20% of the reply"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="test-model",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="html",
        )


@pytest.mark.asyncio
async def test_file_with_no_visible_text_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pad with a comment so the file is not also under the size floor.
    empty = "<!DOCTYPE html>\n<html><body>\n<ASL></ASL>\n<!-- " + ("pad " * 80) + " -->\n</body></html>"
    reply = _reply(empty)
    _, _, body = b.parse_output(reply, fmt="html")
    assert len(body.strip()) * 5 >= b._non_marker_length(reply)

    async def fake_call(model: str, system: str, user: str, timeout: float = 300) -> str:
        return reply

    monkeypatch.setattr(b.llm, "call", fake_call)
    with pytest.raises(b.BuildError, match="no visible text"):
        await b.run_build(
            kind="web_static",
            slug="s",
            display_name="x",
            verbatim="x",
            model="test-model",
            base_source=None,
            base_version=None,
            history=[],
            progress=lambda _m: None,
            format="html",
        )

"""Killable render subprocess: hung children are killed; oversized output is rejected in-child."""

from __future__ import annotations

import os
import time

import pytest

from artifactsmith.renderers import get_renderer
from artifactsmith.renderers.subprocess_render import (
    RenderCrashed,
    RenderTimeout,
    RenderTooLarge,
    render_bounded,
    render_killable,
)


def _hang_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object, as_limit: int = 0) -> None:
    time.sleep(30)


def _huge_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object, as_limit: int = 0) -> None:
    conn.send(("too_large", max_bytes + 1, max_bytes))  # type: ignore[attr-defined]
    conn.close()  # type: ignore[attr-defined]


def _ok_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object, as_limit: int = 0) -> None:
    conn.send(("ok", {"index.html": b"<html></html>"}, "index.html"))  # type: ignore[attr-defined]
    conn.close()  # type: ignore[attr-defined]


def _crash_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object, as_limit: int = 0) -> None:
    conn.close()  # type: ignore[attr-defined]


def test_render_killable_kills_hung_child():
    t0 = time.monotonic()
    with pytest.raises(RenderTimeout):
        render_killable(
            fmt="html",
            title="t",
            body="<html></html>",
            timeout_s=0.5,
            max_bytes=1024,
            mp_context="fork",
            worker=_hang_worker,
        )
    assert time.monotonic() - t0 < 5


def test_render_killable_rejects_oversized_in_child():
    with pytest.raises(RenderTooLarge) as ei:
        render_killable(
            fmt="html",
            title="t",
            body="x",
            timeout_s=5,
            max_bytes=10,
            mp_context="fork",
            worker=_huge_worker,
        )
    assert ei.value.size == 11
    assert ei.value.limit == 10


def test_render_killable_ok_path():
    out = render_killable(
        fmt="html",
        title="t",
        body="x",
        timeout_s=5,
        max_bytes=1024,
        mp_context="fork",
        worker=_ok_worker,
    )
    assert out.primary == "index.html"
    assert out.files["index.html"].startswith(b"<html>")


def test_real_html_render_via_subprocess():
    out = render_killable(
        fmt="html",
        title="t",
        body="<html><body><p>HARBOR-17</p></body></html>",
        timeout_s=30,
        max_bytes=1024 * 1024,
    )
    assert b"HARBOR-17" in out.files["index.html"]


def test_render_killable_maps_closed_pipe_to_crashed():
    with pytest.raises(RenderCrashed):
        render_killable(
            fmt="html",
            title="t",
            body="x",
            timeout_s=5,
            max_bytes=1024,
            mp_context="fork",
            worker=_crash_worker,
        )


def _abort_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object, as_limit: int = 0) -> None:
    os._exit(99)


def test_html_and_markdown_match_in_process_renderer_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("html/markdown must not start a render child")

    monkeypatch.setattr(
        "artifactsmith.renderers.subprocess_render.render_killable",
        boom,
    )
    html_body = "<html><body><p>HARBOR-17</p></body></html>"
    direct_html = get_renderer("html").render(title="Pilot", body=html_body)
    via = render_bounded(fmt="html", title="Pilot", body=html_body, timeout_s=0.05, max_bytes=10**7)
    assert via.files == direct_html.files
    assert via.primary == direct_html.primary
    assert via.files["index.html"] == html_body.encode("utf-8")

    md_body = "The code is HARBOR-17."
    direct_md = get_renderer("markdown").render(title="Pilot", body=md_body)
    via_md = render_bounded(fmt="markdown", title="Pilot", body=md_body, timeout_s=0.05, max_bytes=10**7)
    assert via_md.files == direct_md.files
    assert via_md.primary == direct_md.primary


def test_inprocess_html_rejects_oversize() -> None:
    with pytest.raises(RenderTooLarge) as ei:
        render_bounded(fmt="html", title="t", body="x" * 50, timeout_s=5, max_bytes=10)
    assert ei.value.size == 50
    assert ei.value.limit == 10


def test_pdf_render_bounded_still_kills_hung_child() -> None:
    t0 = time.monotonic()
    with pytest.raises(RenderTimeout):
        render_bounded(
            fmt="pdf",
            title="t",
            body="x",
            timeout_s=0.4,
            max_bytes=1024,
            mp_context="fork",
            worker=_hang_worker,
        )
    assert time.monotonic() - t0 < 5


def test_native_crash_does_not_kill_caller_or_block_next_job() -> None:
    with pytest.raises(RenderCrashed):
        render_bounded(
            fmt="xlsx",
            title="t",
            body="x",
            timeout_s=5,
            max_bytes=1024,
            mp_context="fork",
            worker=_abort_worker,
        )
    html_body = "<html><body><p>HARBOR-17</p></body></html>"
    out = render_bounded(fmt="html", title="t", body=html_body, timeout_s=5, max_bytes=10**6)
    assert out.files["index.html"] == html_body.encode("utf-8")

"""Killable render subprocess: hung children are killed; oversized output is rejected in-child."""

from __future__ import annotations

import time

import pytest

from artifactsmith.renderers.subprocess_render import (
    RenderTimeout,
    RenderTooLarge,
    render_killable,
)


def _hang_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object) -> None:
    time.sleep(30)


def _huge_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object) -> None:
    conn.send(("too_large", max_bytes + 1, max_bytes))  # type: ignore[attr-defined]
    conn.close()  # type: ignore[attr-defined]


def _ok_worker(fmt: str, title: str, body: str, max_bytes: int, conn: object) -> None:
    conn.send(("ok", {"index.html": b"<html></html>"}, "index.html"))  # type: ignore[attr-defined]
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

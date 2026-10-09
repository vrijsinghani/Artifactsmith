"""Run a renderer in a killable child process with output-size enforcement."""

from __future__ import annotations

import multiprocessing as mp
from typing import Any

from .base import RenderOutput


class RenderTimeout(Exception):
    """Raised when the render child is killed after exceeding its deadline."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"render timed out after {seconds:.0f}s")


class RenderTooLarge(Exception):
    """Raised when rendered bytes exceed the configured cap (checked in-child)."""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"render output {size} bytes exceeds cap {limit}")


def _worker(fmt: str, title: str, body: str, max_bytes: int, conn: Any) -> None:
    # Import inside the child so spawn re-exec does not pull the parent package state.
    from artifactsmith.renderers import get_renderer

    try:
        out = get_renderer(fmt).render(title=title, body=body)
        total = sum(len(b) for b in out.files.values())
        if total > max_bytes:
            conn.send(("too_large", total, max_bytes))
            return
        conn.send(("ok", out.files, out.primary))
    except Exception as e:  # noqa: BLE001 — boundary: serialize error to parent
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        conn.close()


def render_killable(
    *,
    fmt: str,
    title: str,
    body: str,
    timeout_s: float,
    max_bytes: int,
    mp_context: str = "spawn",
    worker: Any | None = None,
) -> RenderOutput:
    """Render in a child process; kill the child if it exceeds timeout_s.

    ``mp_context`` and ``worker`` are for tests (fork + injected hang/huge workers).
    """
    ctx = mp.get_context(mp_context)
    parent, child = ctx.Pipe(duplex=False)
    target = worker or _worker
    proc = ctx.Process(
        target=target,
        args=(fmt, title, body, max_bytes, child),
        daemon=True,
    )
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            proc.kill()
            proc.join(5)
            raise RenderTimeout(timeout_s)
        msg = parent.recv()
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)

    kind = msg[0]
    if kind == "ok":
        return RenderOutput(files=msg[1], primary=msg[2])
    if kind == "too_large":
        raise RenderTooLarge(msg[1], msg[2])
    raise RuntimeError(msg[1])

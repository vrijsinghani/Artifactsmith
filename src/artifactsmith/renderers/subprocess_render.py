"""Run a renderer in a killable child process with output-size and memory limits."""

from __future__ import annotations

import multiprocessing as mp
import resource
from typing import Any

from .base import RenderOutput

# Soft address-space cap for the render child (bytes). Overridable in tests.
DEFAULT_AS_LIMIT = 512 * 1024 * 1024


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


class RenderCrashed(Exception):
    """Raised when the render child exits without a result (OOM, kill, etc.)."""

    def __init__(self, detail: str = "render child exited without a result") -> None:
        super().__init__(detail)


def _apply_memory_limit(as_limit: int) -> None:
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = as_limit
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, OSError):
        # Platforms without RLIMIT_AS still get the byte-size check below.
        pass


def _worker(fmt: str, title: str, body: str, max_bytes: int, conn: Any, as_limit: int = DEFAULT_AS_LIMIT) -> None:
    from artifactsmith.renderers import get_renderer

    _apply_memory_limit(as_limit)
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
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def render_killable(
    *,
    fmt: str,
    title: str,
    body: str,
    timeout_s: float,
    max_bytes: int,
    mp_context: str = "spawn",
    worker: Any | None = None,
    as_limit: int = DEFAULT_AS_LIMIT,
) -> RenderOutput:
    """Render in a child process; kill the child if it exceeds timeout_s."""
    ctx = mp.get_context(mp_context)
    parent, child = ctx.Pipe(duplex=False)
    target = worker or _worker
    process_cls = ctx.Process  # type: ignore[attr-defined]
    proc = process_cls(
        target=target,
        args=(fmt, title, body, max_bytes, child, as_limit),
        daemon=True,
    )
    proc.start()
    child.close()
    msg: tuple[Any, ...] | None = None
    try:
        if not parent.poll(timeout_s):
            proc.kill()
            proc.join(5)
            raise RenderTimeout(timeout_s)
        try:
            msg = parent.recv()
        except EOFError as e:
            raise RenderCrashed("render child closed the pipe (likely memory limit or crash)") from e
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)

    if msg is None:
        raise RenderCrashed()
    kind = msg[0]
    if kind == "ok":
        return RenderOutput(files=msg[1], primary=msg[2])
    if kind == "too_large":
        raise RenderTooLarge(msg[1], msg[2])
    raise RuntimeError(msg[1])

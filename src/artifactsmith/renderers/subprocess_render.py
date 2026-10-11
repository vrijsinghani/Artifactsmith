"""Killable child for native renderers. HTML and Markdown run in-process.

The child exists so a deadline can ``proc.kill`` the job, ``RLIMIT_AS`` applies
only to that job, and a native crash cannot take down the server. It is not a
sandbox (same user, no seccomp). Untrusted text is sanitized before render.

HTML and Markdown only encode a string already capped by ``check_content``.
They cannot abort the interpreter, so they skip the process. PDF (WeasyPrint /
Cairo), DOCX, and XLSX (lxml) stay in a fresh ``spawn`` child. A long-lived
worker would keep that address space and library caches across jobs, and
killing a hung job discards the warm process. ``fork`` is unsafe once the
server has other threads.
"""

from __future__ import annotations

import multiprocessing as mp
import resource
from typing import Any

from .base import RenderOutput

# Pure encoders of already-capped text. No native layout library.
IN_PROCESS_FORMATS = frozenset({"html", "markdown"})

# Soft address-space cap for the render child (bytes). Overridable in tests.
DEFAULT_AS_LIMIT = 512 * 1024 * 1024


class RenderTimeout(Exception):
    """Raised when the render child is killed after exceeding its deadline."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"render timed out after {seconds:.0f}s")


class RenderTooLarge(Exception):
    """Raised when rendered bytes exceed the configured cap."""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"render output {size} bytes exceeds cap {limit}")


class RenderCrashed(Exception):
    """Raised when the render child exits without a result (OOM, kill, etc.)."""

    def __init__(self, detail: str = "render child exited without a result") -> None:
        super().__init__(detail)


def _apply_memory_limit(as_limit: int) -> None:
    """Lower RLIMIT_AS in the render child only.

    The worker functions also run in-process in tests. Capping the test
    runner (or the server) makes the next ``Thread.start`` wait forever
    once the process is already near the cap.
    """
    if mp.parent_process() is None:
        return
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
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


def render_inprocess(*, fmt: str, title: str, body: str, max_bytes: int) -> RenderOutput:
    """Render on this interpreter and enforce the output byte cap."""
    from artifactsmith.renderers import get_renderer

    out = get_renderer(fmt).render(title=title, body=body)
    total = sum(len(blob) for blob in out.files.values())
    if total > max_bytes:
        raise RenderTooLarge(total, max_bytes)
    return out


def render_bounded(
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
    """Render HTML and Markdown here. Run PDF, DOCX, and XLSX in a killable child.

    ``worker`` forces the child path so tests can still inject a hang or a crash.
    """
    key = (fmt or "html").lower().strip()
    if worker is None and key in IN_PROCESS_FORMATS:
        return render_inprocess(fmt=key, title=title, body=body, max_bytes=max_bytes)
    return render_killable(
        fmt=fmt,
        title=title,
        body=body,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        mp_context=mp_context,
        worker=worker,
        as_limit=as_limit,
    )

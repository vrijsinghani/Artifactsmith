"""One fast model call, then at most one follow-up. Never two calls at once."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from .. import llm
from ..builder import (
    BuildError,
    BuildResult,
    NeedsInput,
    RetryableCheck,
    _finish_text,
    _sweep_rendered,
    build_user_prompt,
    needs_input,
    parse_output,
    render_checked_body,
    system_prompt_for,
)
from ..config import CFG
from ..renderers.safety import check_content, check_fields
from ..renderers.subprocess_render import (
    DEFAULT_AS_LIMIT,
    RenderCrashed,
    RenderTimeout,
    RenderTooLarge,
    render_killable,
)
from .model import LayoutError
from .prompt import fast_system_prompt
from .render import render_layout

_NOTES_NAME = "_layout_notes.txt"

_HTML_START = re.compile(r"^(?:<!doctype\s+html\b|<html\b)", re.I)


def _layout_worker(
    style: str,
    title: str,
    body: str,
    max_bytes: int,
    conn: Any,
    as_limit: int = DEFAULT_AS_LIMIT,
) -> None:
    """Render one layout page in the killable child. ``style`` arrives as the format argument."""
    from artifactsmith.renderers.subprocess_render import _apply_memory_limit

    _apply_memory_limit(as_limit)
    try:
        html, notes = render_layout(body, style=style or "house", title=title)
        data = html.encode("utf-8")
        if len(data) > max_bytes:
            conn.send(("too_large", len(data), max_bytes))
            return
        blob = "\n".join(notes).encode("utf-8")
        conn.send(("ok", {"index.html": data, _NOTES_NAME: blob}, "index.html"))
    except LayoutError as exc:
        conn.send(("error", f"LayoutError: {exc}"))
    except Exception as exc:  # noqa: BLE001 — boundary: serialize the error to the parent
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _render_layout_bounded(
    body: str,
    style: str,
    title: str,
    timeout_s: float,
    max_bytes: int,
) -> tuple[str, list[str]]:
    """Render in the killable child. Timeout, crash, and oversize raise."""
    try:
        out = render_killable(
            fmt=style,
            title=title,
            body=body,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            worker=_layout_worker,
        )
    except RuntimeError as exc:
        text = str(exc)
        if text.startswith("LayoutError"):
            raise LayoutError(text) from exc
        raise
    html = out.files["index.html"].decode("utf-8")
    raw_notes = out.files.get(_NOTES_NAME, b"").decode("utf-8")
    return html, [line for line in raw_notes.splitlines() if line]


_FIX = (
    "\n\nYour previous reply failed server checks: {problems}. "
    "Return the full reply again in the required format, fixing these problems."
)


class _Outcome:
    def __init__(self, result: BuildResult | None = None, problems: list[str] | None = None, unusable: bool = False):
        self.result = result
        self.problems = problems or []
        self.unusable = unusable


def _render_failed(problems: list[str]) -> bool:
    prefixes = ("renderer error", "render timed out", "render output", "render child")
    return any(item.startswith(prefixes) for item in problems)


async def run_fast_html(
    *,
    display_name: str,
    verbatim: str,
    model: str,
    base_version: int | None,
    history: list[str],
    progress: Callable[[str], None],
    source: dict[str, Any] | None,
    render_timeout: int,
    style: str,
) -> BuildResult:
    """Markdown layout first. Unusable output falls back to full HTML. Check failures retry Markdown."""
    system = fast_system_prompt(style)
    user = build_user_prompt(verbatim, display_name, None, base_version, history, source)
    notes: list[str] = []
    progress("calling model (fast)")
    # effort=low is attached only when the model name is o1/o3/o4/gpt-5.
    # The fast prompt stays no longer than the full HTML prompt, so a large
    # source does not pay extra prefill. Do not probe a second effort value.
    text = await llm.call(model, system, user, effort="low")
    outcome = await _accept(
        text,
        display_name=display_name,
        model=model,
        style=style,
        render_timeout=render_timeout,
        progress=progress,
        notes=notes,
    )
    if outcome.result is not None:
        return outcome.result
    if outcome.unusable:
        return await _html_fallback(
            model=model,
            style=style,
            user=user,
            display_name=display_name,
            render_timeout=render_timeout,
            progress=progress,
            notes=notes,
        )
    progress("calling model (fast)")
    retry_user = user + _FIX.format(problems="; ".join(outcome.problems))
    text = await llm.call(model, system, retry_user, effort="low")
    outcome = await _accept(
        text,
        display_name=display_name,
        model=model,
        style=style,
        render_timeout=render_timeout,
        progress=progress,
        notes=notes,
    )
    if outcome.result is not None:
        return outcome.result
    raise BuildError("builder output failed checks twice: " + "; ".join(outcome.problems or ["unusable layout"]))


async def _html_fallback(
    *,
    model: str,
    style: str,
    user: str,
    display_name: str,
    render_timeout: int,
    progress: Callable[[str], None],
    notes: list[str],
) -> BuildResult:
    progress("calling model (html fallback)")
    notes.append("fast layout fell back to full HTML")
    text = await llm.call(model, system_prompt_for("html", style), user)
    finish = await _finish_text(
        text,
        fmt="html",
        display_name=display_name,
        model=model,
        render_timeout=render_timeout,
        progress=progress,
        notes=notes,
        attempt_label="html fallback",
    )
    if finish.fatal is not None:
        raise BuildError(str(finish.fatal)) from finish.fatal
    if finish.result is not None:
        return finish.result
    raise BuildError("builder output failed checks twice: " + "; ".join(finish.problems))


async def _accept(
    text: str,
    *,
    display_name: str,
    model: str,
    style: str,
    render_timeout: int,
    progress: Callable[[str], None],
    notes: list[str],
) -> _Outcome:
    missing = needs_input(text)
    if missing:
        problems = check_fields(
            missing,
            block_private_links=CFG.block_private_links,
            allowed_link_domains=CFG.allowed_link_domains,
            label="needs_input",
        )
        if problems:
            return _Outcome(problems=problems)
        raise NeedsInput(missing)
    if "===FILE:" not in text:
        return _Outcome(unusable=True)
    try:
        assumptions, summary, body = parse_output(text, fmt="markdown")
    except BuildError:
        return _Outcome(unusable=True)
    if not body.strip() or _HTML_START.match(body.strip()):
        return _Outcome(unusable=True)
    problems = check_content(
        body,
        fmt="markdown",
        block_private_links=CFG.block_private_links,
        allowed_link_domains=CFG.allowed_link_domains,
    )
    if problems:
        return _Outcome(problems=problems)
    try:
        html, layout_notes = await asyncio.to_thread(
            _render_layout_bounded,
            body,
            style,
            display_name,
            float(render_timeout),
            CFG.max_output_bytes,
        )
    except (LayoutError, RenderTimeout, RenderTooLarge, RenderCrashed):
        return _Outcome(unusable=True)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"fast: {type(exc).__name__}")
        return _Outcome(unusable=True)
    try:
        result = await render_checked_body(
            fmt="html",
            display_name=display_name,
            body=html,
            summary=summary,
            assumptions=assumptions,
            render_timeout=float(render_timeout),
            model=model,
            raw_chars=len(text),
            notes=[*notes, *layout_notes],
            progress=progress,
        )
    except RetryableCheck as exc:
        if _render_failed(exc.problems):
            return _Outcome(unusable=True)
        return _Outcome(problems=list(exc.problems))
    except BuildError:
        return _Outcome(unusable=True)
    page = (body.strip() + "\n").encode("utf-8")
    merged = dict(result.files)
    merged["page.md"] = page
    sweep = _sweep_rendered({"page.md": page})
    if sweep:
        return _Outcome(problems=[sweep])
    result.files = merged
    return _Outcome(result=result)

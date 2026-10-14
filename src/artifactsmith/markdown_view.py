"""HTML view of a stored markdown primary, built when the page is opened."""

from __future__ import annotations

from .config import CFG
from .layout.model import LayoutError
from .layout.render import render_layout
from .renderers import SOURCE_NAME
from .renderers.html_sanitize import HtmlSanitizeError, sanitize_html_document
from .renderers.safety import check_content
from .styles import DEFAULT_STYLE


class MarkdownViewError(Exception):
    """The stored markdown cannot be shown as a safe HTML page."""


def is_markdown_primary(name: str, content_type: str) -> bool:
    """True when this primary file is markdown."""
    if name == SOURCE_NAME["markdown"]:
        return True
    return (content_type or "").lower().startswith("text/markdown")


def markdown_share_html(source: bytes, *, title: str, style: str) -> bytes:
    """House-styled HTML for stored markdown. Raises MarkdownViewError when it cannot be shown."""
    try:
        text = source.decode("utf-8")
    except UnicodeError as exc:
        raise MarkdownViewError("markdown is not utf-8") from exc
    try:
        html, _notes = render_layout(text, style=style or DEFAULT_STYLE, title=title or "Page")
        clean = sanitize_html_document(html)
    except (LayoutError, HtmlSanitizeError) as exc:
        raise MarkdownViewError("markdown view rejected") from exc
    problems = check_content(
        clean,
        fmt="html",
        block_private_links=CFG.block_private_links,
        allowed_link_domains=CFG.allowed_link_domains,
    )
    if problems:
        raise MarkdownViewError("markdown view rejected")
    return clean.encode("utf-8")

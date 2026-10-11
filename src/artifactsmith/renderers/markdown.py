"""Markdown renderer: sanitized markdown bytes, optionally titled.

HTML anchors the link policy already allows are written as ``[text](url)``.
Blocked anchors stay unlinked.
"""

from __future__ import annotations

from .base import RenderOutput
from .md_export import export_markdown


class MarkdownRenderer:
    format = "markdown"
    model_filename = "document.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        text = export_markdown(body).strip() + "\n"
        if title and not text.lstrip().startswith("#"):
            text = f"# {title}\n\n{text}"
        return RenderOutput(files={"document.md": text.encode("utf-8")}, primary="document.md")

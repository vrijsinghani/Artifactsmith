"""Markdown renderer: sanitized markdown bytes, optionally titled."""

from __future__ import annotations

from .base import RenderOutput


class MarkdownRenderer:
    format = "markdown"
    model_filename = "document.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        text = body.strip() + "\n"
        if title and not text.lstrip().startswith("#"):
            text = f"# {title}\n\n{text}"
        return RenderOutput(files={"document.md": text.encode("utf-8")}, primary="document.md")

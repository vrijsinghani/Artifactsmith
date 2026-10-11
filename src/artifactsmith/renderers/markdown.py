"""Markdown renderer: sanitized markdown bytes, optionally titled.

HTML anchors the link policy already allows are written as ``[text](url)``.
Blocked anchors stay unlinked.
"""

from __future__ import annotations

from .base import RenderOutput
from .md_export import export_markdown
from .md_sanitize import _destinations_policy_clean


class MarkdownRenderer:
    format = "markdown"
    model_filename = "document.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        exported = export_markdown(body)
        # Fail closed: unwrapping an anchor must not expose text that linkify would
        # turn into a non-public link (e.g. a bare www.10.0.0.5.nip.io label).
        if exported != body and not _destinations_policy_clean(exported):
            exported = body
        text = exported.strip() + "\n"
        if title and not text.lstrip().startswith("#"):
            text = f"# {title}\n\n{text}"
        return RenderOutput(files={"document.md": text.encode("utf-8")}, primary="document.md")

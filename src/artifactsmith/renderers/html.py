"""HTML renderer: model already produced a complete document; package bytes only."""

from __future__ import annotations

from .base import RenderOutput


class HtmlRenderer:
    format = "html"
    model_filename = "index.html"

    def render(self, *, title: str, body: str) -> RenderOutput:  # noqa: ARG002
        return RenderOutput(files={"index.html": body.encode("utf-8")}, primary="index.html")

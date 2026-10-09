"""PDF renderer via WeasyPrint (HTML/CSS → PDF, no headless browser)."""

from __future__ import annotations

from typing import Any

from .base import RenderOutput
from .md_parse import blocks_to_simple_html, parse_blocks


class DenyAllURLFetcher:
    """WeasyPrint 70+ URLFetcher-compatible object that never fetches."""

    def fetch(self, url: str, headers: Any = None) -> Any:
        raise ValueError(f"WeasyPrint URL fetch denied: {url}")


class PdfRenderer:
    format = "pdf"
    model_filename = "content.md"

    def render(self, *, title: str, body: str) -> RenderOutput:
        from weasyprint import HTML  # lazy: keeps import light for non-PDF unit paths

        blocks = parse_blocks(body)
        html = blocks_to_simple_html(title or "Artifact", blocks)
        pdf_bytes = HTML(string=html, base_url=".", url_fetcher=DenyAllURLFetcher()).write_pdf()
        if not isinstance(pdf_bytes, (bytes, bytearray)):
            raise RuntimeError("WeasyPrint returned no PDF bytes")
        files = {
            "document.pdf": bytes(pdf_bytes),
            "content.md": (body.strip() + "\n").encode("utf-8"),
        }
        return RenderOutput(files=files, primary="document.pdf")

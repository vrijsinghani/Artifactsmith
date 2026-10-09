"""Deterministic output renderers. The model produces content; renderers never call the network."""

from __future__ import annotations

from .base import Renderer, RenderOutput
from .docx_renderer import DocxRenderer
from .html import HtmlRenderer
from .markdown import MarkdownRenderer
from .pdf import PdfRenderer
from .xlsx import XlsxRenderer

SUPPORTED_FORMATS: frozenset[str] = frozenset({"html", "markdown", "pdf", "docx", "xlsx"})

MIME: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

EXTENSION: dict[str, str] = {
    "html": "html",
    "markdown": "md",
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
}

# Editable text source stored alongside binary outputs so edit/rebuild has a text base.
SOURCE_NAME: dict[str, str] = {
    "html": "index.html",
    "markdown": "document.md",
    "pdf": "content.md",
    "docx": "content.md",
    "xlsx": "content.md",
}

_REGISTRY: dict[str, Renderer] = {
    "html": HtmlRenderer(),
    "markdown": MarkdownRenderer(),
    "pdf": PdfRenderer(),
    "docx": DocxRenderer(),
    "xlsx": XlsxRenderer(),
}


def get_renderer(fmt: str) -> Renderer:
    key = (fmt or "html").lower().strip()
    if key not in _REGISTRY:
        raise ValueError(f"unsupported format {fmt!r}; choose one of {sorted(SUPPORTED_FORMATS)}")
    return _REGISTRY[key]


def source_name(kind: str, fmt: str) -> str:  # noqa: ARG001  (kind reserved for future kinds)
    return SOURCE_NAME.get((fmt or "html").lower(), "index.html")


__all__ = [
    "EXTENSION",
    "MIME",
    "RenderOutput",
    "Renderer",
    "SOURCE_NAME",
    "SUPPORTED_FORMATS",
    "get_renderer",
    "source_name",
]

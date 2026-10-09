"""Renderer interface: content in, deterministic bytes out. No network, no model calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RenderOutput:
    """Files to store for one version. ``primary`` is the download/preview entry."""

    files: dict[str, bytes]
    primary: str


class Renderer(Protocol):
    """One output format. Implementations must be pure and side-effect free aside from CPU/memory."""

    format: str
    """Wire format name (html, markdown, pdf, docx, xlsx)."""

    model_filename: str
    """Filename the model must emit in the ===FILE: ...=== section."""

    def render(self, *, title: str, body: str) -> RenderOutput:
        """Turn sanitized model body text into one or more stored files."""
        ...

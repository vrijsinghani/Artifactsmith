"""Self-hosted MCP server that lets AI agents make real documents."""

from __future__ import annotations

__version__ = "0.1.0"

from .renderers import SUPPORTED_FORMATS

__all__ = ["SUPPORTED_FORMATS", "__version__"]

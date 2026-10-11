"""Fast HTML builds: Markdown plus a few blocks, rendered here in one of the six styles."""

from .model import LayoutError
from .render import render_layout

__all__ = ["LayoutError", "render_layout"]

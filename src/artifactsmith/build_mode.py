"""First-build mode. ``full`` is today's HTML build. ``fast`` is server-rendered Markdown."""

from __future__ import annotations

MODES: tuple[str, ...] = ("fast", "full")
DEFAULT_MODE = "full"


class ModeError(ValueError):
    """Unknown build mode."""


def parse_build_mode(value: str | None, *, default: str = DEFAULT_MODE) -> str:
    """Return ``fast`` or ``full``. Empty means ``default``."""
    raw = default if value is None or str(value).strip() == "" else str(value).strip().lower()
    if raw not in MODES:
        raise ModeError("mode must be fast or full")
    return raw

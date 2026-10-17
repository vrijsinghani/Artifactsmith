"""In-process local-owner flag for `artifactsmith stdio`.

The flag is not an environment variable. `artifactsmith serve` never sets it, so a
production process cannot inherit stdio owner mode.
"""

from __future__ import annotations

enabled = False

MESSAGE = "stdio cannot use the object store or the model"


class OfflineError(RuntimeError):
    """Store or model work on the local stdio transport.

    This is not AMError. Tool handlers catch AMError and return a success payload.
    FastMCP reports this exception as an MCP tool error.
    """


def enable() -> None:
    global enabled
    enabled = True


def refuse() -> None:
    if enabled:
        raise OfflineError(MESSAGE)

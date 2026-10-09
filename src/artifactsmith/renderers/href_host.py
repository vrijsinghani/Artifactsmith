"""Host extraction for link policy: urllib and WHATWG-style authority parses."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse

from .safety import _host_is_private, _normalize_host


def urllib_host(url: str) -> str | None:
    """Host from urllib.parse; None if missing, userinfo present, or unparseable."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if "@" in (parsed.netloc or ""):
        return None
    host = parsed.hostname
    if host is None or host == "":
        return None
    return _normalize_host(host)


def whatwg_host(url: str) -> str | None:
    """Host via WHATWG-like authority rules for special http(s) URLs.

    Backslashes in the authority/path delimiter position are treated as slashes.
    Any userinfo (``@`` in authority) is rejected. IPv6 zone IDs are rejected.
    Returns None when the URL must be dropped (fail closed).
    """
    m = re.match(r"^(https?):[/\\][/\\](.*)$", url, flags=re.I | re.S)
    if not m:
        return None
    rest = m.group(2)
    authority: list[str] = []
    for ch in rest:
        if ch in "/\\?#":
            break
        authority.append(ch)
    auth = "".join(authority)
    if not auth or "@" in auth:
        return None
    host = auth
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        inner = host[1:end]
        if "%" in inner:
            return None
        return _normalize_host(inner)
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    if not host:
        return None
    return _normalize_host(host)


def hosts_agree_public(emit: str, *, deobfuscate: Callable[[str], str]) -> bool:
    """True when deobfuscated, urllib, and WHATWG-style parses agree on one public host."""
    if not emit.lower().startswith(("http://", "https://")):
        return False
    hosts: list[str] = []
    for host in (
        urllib_host(emit),
        whatwg_host(emit),
        urllib_host(deobfuscate(emit)),
    ):
        if host is None or host == "" or _host_is_private(host):
            return False
        hosts.append(host)
    return len(set(hosts)) == 1

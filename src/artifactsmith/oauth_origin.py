"""Public origin and OAuth redirect checks.

Metadata and WWW-Authenticate use AM_API_URL only when that origin is https
and the host is not private. Redirects are an allowlist, not a guess.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import CFG
from .renderers.safety import _host_is_private

DEFAULT_REDIRECTS = (
    "https://claude.ai/api/mcp/auth_callback,"
    "https://chatgpt.com/connector_platform_oauth_redirect,"
    "http://127.0.0.1:*/callback,"
    "http://localhost:*/callback"
)

_LOOPBACK = {
    "http://127.0.0.1:*/callback": "127.0.0.1",
    "http://localhost:*/callback": "localhost",
    "http://127.0.0.1/callback": "127.0.0.1",
    "http://localhost/callback": "localhost",
}


def redirect_patterns() -> list[str]:
    raw = CFG.oauth_redirects.strip() or DEFAULT_REDIRECTS
    return [part.strip() for part in raw.split(",") if part.strip()]


def public_origin(api_url: str | None = None) -> str | None:
    """https origin safe to publish. None for http, userinfo, or a private host."""
    parsed = urlparse((api_url if api_url is not None else CFG.api_url).strip())
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    if _host_is_private(host):
        return None
    origin = f"https://{host.lower()}"
    if parsed.port and parsed.port != 443:
        origin += f":{parsed.port}"
    return origin


def resource_url(origin: str) -> str:
    return origin.rstrip("/") + "/mcp"


def canonical_url(raw: str) -> str | None:
    """Lowercase scheme and host, drop a default port, userinfo, query, and fragment."""
    parsed = urlparse(raw.strip())
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    origin = f"{parsed.scheme.lower()}://{host.lower()}"
    default = 443 if parsed.scheme == "https" else 80
    if parsed.port and parsed.port != default:
        origin += f":{parsed.port}"
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return origin + path


def resource_matches(presented: str, expected: str) -> bool:
    got = canonical_url(presented)
    want = canonical_url(expected)
    return bool(got and want and got == want)


def _loopback_uri(uri: str, host: str) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    if (parsed.hostname or "").lower() != host:
        return False
    return (parsed.path or "") == "/callback"


def _star_match(pattern: str, uri: str) -> bool:
    """One wildcard segment. ``?``, ``#``, and ``%`` never match."""
    if any(ch in uri for ch in ("?", "#", "%")):
        return False
    if pattern.count("*") != 1:
        return False
    left, right = pattern.split("*", 1)
    try:
        return re.fullmatch(re.escape(left) + r"[A-Za-z0-9_-]+" + re.escape(right), uri) is not None
    except re.error:
        return False


def form_source(redirect_uri: str) -> str:
    """CSP form-action host for one allowlisted redirect. Empty when it has no origin."""
    parsed = urlparse(redirect_uri)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme == "http" and host == "127.0.0.1":
        return "http://127.0.0.1:*"
    if scheme == "http" and host == "localhost":
        return "http://localhost:*"
    if scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        return ""
    origin = f"{scheme}://{host}"
    default = 443 if scheme == "https" else 80
    if parsed.port and parsed.port != default:
        origin += f":{parsed.port}"
    return origin


def redirect_allowed(uri: str, patterns: list[str] | None = None) -> bool:
    """True when uri matches one allowlist entry. Loopback ignores the port."""
    if not uri or len(uri) > 500 or any(c in uri for c in ("\r", "\n", " ")):
        return False
    for pattern in patterns if patterns is not None else redirect_patterns():
        host = _LOOPBACK.get(pattern)
        if host is not None:
            if _loopback_uri(uri, host):
                return True
            continue
        if "*" in pattern:
            if _star_match(pattern, uri):
                return True
            continue
        if pattern == uri:
            return True
    return False


def same_loopback(registered: str, requested: str) -> bool:
    """Port-agnostic match for one client's registered loopback callback."""
    left = urlparse(registered)
    right = urlparse(requested)
    if left.scheme != "http" or right.scheme != "http":
        return False
    host = (left.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost") or host != (right.hostname or "").lower():
        return False
    if left.username or right.username or left.query or right.query or left.fragment or right.fragment:
        return False
    return (left.path or "") == "/callback" and (right.path or "") == "/callback"


def client_redirect_ok(registered: list[str], requested: str) -> bool:
    if any("*" in item or item in _LOOPBACK for item in registered):
        return redirect_allowed(requested, registered)
    for item in registered:
        if item == requested or same_loopback(item, requested):
            return True
    return False


def authenticate_header() -> str:
    """401 challenge. A private origin gets a bare Bearer, with no URL."""
    origin = public_origin()
    if origin is None:
        return "Bearer"
    quoted = origin + "/.well-known/oauth-protected-resource"
    return f'Bearer error="invalid_token", resource_metadata="{quoted}"'

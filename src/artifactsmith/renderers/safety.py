"""Shared output safety: no scripts, no remote fetches, private-link and secret checks, size limits."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

# Remote URL references (also used by HTML sanitizer).
# Allow IPv6 bracket hosts: http://[::1]/path
URL_RE = re.compile(
    r"""(?:https?|ftp)://(?:\[[0-9A-Fa-f:.]+\]|[^\s"'<>()\[\]]+)[^\s"'<>()\]]*""",
    re.I,
)
SCRIPT_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/>|</?\s*script\b[^>]*>",
    re.I | re.S,
)

# Common secret-shaped strings. Tuned for high precision over recall; false positives fail the build.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    ("github_fine_grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("generic_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b")),
]

PRIVATE_HOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    }
)

# Wildcard DNS products that commonly alias private/loopback addresses into public DNS.
WILDCARD_DNS_SUFFIXES = (
    ".sslip.io",
    ".nip.io",
    ".xip.io",
    ".localtest.me",
    ".lvh.me",
    ".vcap.me",
)


def strip_scripts(text: str) -> str:
    return SCRIPT_RE.sub("", text)


def strip_remote_urls(text: str) -> str:
    return URL_RE.sub("", text)


def sanitize_text(text: str) -> str:
    """Remove executable markup and remote URL references from any textual body."""
    return strip_remote_urls(strip_scripts(text))


def find_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for label, pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(f"possible secret ({label})")
    return hits


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a host as an IP, including short/hex/octal/integer IPv4 forms browsers accept."""
    h = host.strip().strip("[]")
    if not h:
        return None
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    # socket.inet_aton accepts many historical IPv4 spellings (127.1, 0x7f.0.0.1, 0177.0.0.1, 2130706433).
    if re.fullmatch(r"[0-9a-fxA-FX.]+", h):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(h))
        except OSError:
            return None
    return None


def _host_is_private(host: str) -> bool:
    """True when the host is private, loopback, link-local, or a known wildcard-DNS alias.

    Fail closed: unparseable hosts that look like addresses, and bare single-label names, are private.
    """
    h = host.strip(".").lower()
    if not h or h in PRIVATE_HOST_NAMES or h.endswith(".local") or h.endswith(".internal"):
        return True
    if h.endswith(".localhost"):
        return True
    for suf in WILDCARD_DNS_SUFFIXES:
        if h == suf.lstrip(".") or h.endswith(suf):
            return True
    ip = _parse_ip(h)
    if ip is not None:
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    # Bare hostnames without a dot are treated as local/private.
    if "." not in h:
        return True
    # Looks like a dotted address we failed to parse (fail closed).
    if re.fullmatch(r"[0-9a-fxA-FX.:]+", h):
        return True
    return False


def _url_host(raw: str) -> str | None:
    """Extract the host from a URL. Returns None only when there is no host authority."""
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001 — fail closed below
        return ""
    host = parsed.hostname
    if host is not None:
        return host
    # urlparse can leave netloc empty for odd forms; try a manual bracket extract.
    if "://" in raw:
        rest = raw.split("://", 1)[1]
        authority = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if authority.startswith("["):
            end = authority.find("]")
            if end > 0:
                return authority[1:end]
        if authority:
            return authority.split("@")[-1].split(":")[0]
    return ""


def find_private_links(text: str) -> list[str]:
    """Return problems for http(s) links that resolve to private or local hosts."""
    problems: list[str] = []
    for m in URL_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:)")
        host = _url_host(raw)
        if host is None:
            continue
        # Empty host after a scheme (unparseable) → fail closed.
        if host == "" or _host_is_private(host):
            label = host or raw
            problems.append(f"private or local link host: {label}")
    return problems


def find_disallowed_links(text: str, allowed_domains: list[str]) -> list[str]:
    """If an allow-list is set, every remote host must match it (suffix match)."""
    if not allowed_domains:
        return []
    allowed = [d.lower().lstrip(".") for d in allowed_domains if d.strip()]
    problems: list[str] = []
    for m in URL_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:)")
        host = (_url_host(raw) or "").lower()
        if not host:
            problems.append(f"link host not on allow-list: {raw}")
            continue
        if not any(host == d or host.endswith("." + d) for d in allowed):
            problems.append(f"link host not on allow-list: {host}")
    return problems


def check_fields(
    *parts: str,
    block_private_links: bool = True,
    allowed_link_domains: list[str] | None = None,
    max_chars: int = 8_000,
    label: str = "field",
) -> list[str]:
    """Heuristic secret/private-link checks for titles, summaries, and other card fields.

    Secret detection is a best-effort pattern match, not a guarantee.
    """
    problems: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) > max_chars:
            problems.append(f"{label} exceeds {max_chars} characters")
        for hit in find_secrets(part):
            problems.append(f"{label}: {hit}")
        if block_private_links:
            for hit in find_private_links(part):
                problems.append(f"{label}: {hit}")
        for hit in find_disallowed_links(part, allowed_link_domains or []):
            problems.append(f"{label}: {hit}")
    return problems


def check_content(
    text: str,
    *,
    fmt: str,
    block_private_links: bool = True,
    allowed_link_domains: list[str] | None = None,
    max_chars: int = 2_000_000,
) -> list[str]:
    """Return human-readable problems that must be fixed before rendering.

    Secret detection is a heuristic, not a guarantee that no secrets remain.
    """
    problems: list[str] = []
    if len(text) > max_chars:
        problems.append(f"content exceeds {max_chars} characters")
    problems.extend(find_secrets(text))
    if block_private_links:
        problems.extend(find_private_links(text))
    problems.extend(find_disallowed_links(text, allowed_link_domains or []))
    if fmt == "html":
        low = text.lower()
        if "<script" in low:
            problems.append("script tag present in sanitized output")
        if URL_RE.search(text):
            problems.append("remote http(s) URL present in sanitized output")
        if "<html" not in low or "</html>" not in low:
            problems.append("not a complete HTML document")
    elif fmt in ("markdown", "pdf", "docx", "xlsx"):
        if URL_RE.search(text):
            problems.append("remote http(s) URL present in sanitized output")
        if "<script" in text.lower():
            problems.append("script tag present in sanitized output")
    return problems

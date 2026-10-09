"""Public http(s) link policy shared by sanitizer and renderers.

Allows navigational links to global hosts. Blocks private/loopback hosts,
dangerous schemes, and protocol-relative URLs. Does not fetch anything.
"""

from __future__ import annotations

import re
import unicodedata
from html import escape, unescape
from typing import Literal
from urllib.parse import urlparse

from .safety import URL_RE, _host_is_private, _url_host

HrefClass = Literal["fragment", "relative", "public", "blocked"]

_DANGEROUS_PREFIXES = ("javascript:", "vbscript:", "data:", "file:", "blob:")


def _strip_format_chars(s: str) -> str:
    return "".join(c for c in s if unicodedata.category(c) != "Cf")


def normalize_href(value: str) -> str:
    """Unescape HTML entities, strip Unicode format chars, normalize backslashes."""
    raw = unescape(value)
    raw = _strip_format_chars(raw).replace("\\", "/").strip()
    return raw


def classify_href(value: str) -> HrefClass:
    """Classify an href/src candidate after de-obfuscation."""
    raw = normalize_href(value)
    if not raw:
        return "blocked"
    if raw.startswith("#"):
        return "fragment"
    low = raw.lower()
    compact = re.sub(r"\s+", "", low)
    if compact.startswith("//") or low.startswith("//"):
        return "blocked"
    for prefix in _DANGEROUS_PREFIXES:
        if compact.startswith(prefix) or low.startswith(prefix):
            return "blocked"
    if low.startswith(("http://", "https://")):
        host = _url_host(raw)
        if host is None or host == "" or _host_is_private(host):
            return "blocked"
        # Reject credentials-in-URL weirdness that urlparse still hosts.
        try:
            parsed = urlparse(raw)
            if parsed.scheme not in ("http", "https"):
                return "blocked"
        except Exception:  # noqa: BLE001
            return "blocked"
        return "public"
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) or re.match(r"^[a-z][a-z0-9+.-]*:", compact):
        return "blocked"
    # "/\evil" → "//evil" after backslash normalize.
    if low.startswith("//") or (low.startswith("/") and low[1:2] == "/"):
        return "blocked"
    return "relative"


def is_public_http_url(url: str) -> bool:
    return classify_href(url) == "public"


def public_href_or_none(url: str) -> str | None:
    """Return a normalized public http(s) URL, or None if not allowed."""
    raw = normalize_href(url)
    if classify_href(raw) != "public":
        return None
    # Prefer the normalized form (no Cf / backslash smuggling).
    return raw


# Markdown inline [text](url) — no nested brackets.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def iter_inline_segments(text: str) -> list[tuple[str, str | None]]:
    """Split text into (display, href|None) segments for renderers.

    Markdown links to public hosts become hyperlink segments. Bare public
    http(s) URLs become hyperlink segments. Everything else is plain text.
    """
    if not text:
        return []
    parts: list[tuple[str, str | None]] = []
    pos = 0
    # Scan for either markdown links or bare URLs, whichever comes first.
    while pos < len(text):
        md = _MD_LINK_RE.search(text, pos)
        bare = URL_RE.search(text, pos)
        next_md = md.start() if md else len(text) + 1
        next_bare = bare.start() if bare else len(text) + 1
        if md and next_md <= next_bare:
            if md.start() > pos:
                parts.append((text[pos : md.start()], None))
            label, dest = md.group(1), md.group(2)
            href = public_href_or_none(dest)
            if href:
                parts.append((label, href))
            else:
                parts.append((md.group(0), None))
            pos = md.end()
            continue
        if bare and next_bare < next_md:
            if bare.start() > pos:
                parts.append((text[pos : bare.start()], None))
            raw = bare.group(0)
            # Peel trailing sentence punctuation commonly glued to URLs.
            core = raw.rstrip(".,;:)")
            trailing = raw[len(core) :]
            href = public_href_or_none(core)
            if href:
                parts.append((core, href))
                if trailing:
                    parts.append((trailing, None))
            else:
                parts.append((raw, None))
            pos = bare.end()
            continue
        parts.append((text[pos:], None))
        break
    return parts


def linkify_markdown(text: str) -> str:
    """Turn bare public http(s) URLs into markdown links; keep existing safe links."""
    out: list[str] = []
    for display, href in iter_inline_segments(text):
        if href is None:
            out.append(display)
        elif display == href or display.startswith("http://") or display.startswith("https://"):
            # Bare URL segment (or label that is the URL).
            out.append(f"[{href}]({href})")
        else:
            out.append(f"[{display}]({href})")
    return "".join(out)


def harden_external_anchors(html: str) -> str:
    """Add rel and target on <a> tags whose href is public http(s)."""

    def repl(m: re.Match[str]) -> str:
        attrs = m.group(1)
        hm = re.search(r"""\bhref\s*=\s*("([^"]*)"|'([^']*)')""", attrs, re.I)
        if not hm:
            return m.group(0)
        href = hm.group(2) if hm.group(2) is not None else hm.group(3)
        if classify_href(href or "") != "public":
            return m.group(0)
        attrs2 = re.sub(r"""\s*\brel\s*=\s*(".*?"|'.*?')""", "", attrs, flags=re.I)
        attrs2 = re.sub(r"""\s*\btarget\s*=\s*(".*?"|'.*?')""", "", attrs2, flags=re.I)
        return f'<a{attrs2} rel="noopener noreferrer nofollow" target="_blank">'

    return re.sub(r"<a\b([^>]*)>", repl, html, flags=re.I)


def linkify_html_text(html: str) -> str:
    """Wrap bare public URLs that appear in text nodes (not inside tags)."""
    parts = re.split(r"(<[^>]+>)", html)
    out: list[str] = []
    in_anchor = 0
    for part in parts:
        if part.startswith("<"):
            low = part.lower()
            if low.startswith("<a ") or low.startswith("<a>"):
                in_anchor += 1
            elif low.startswith("</a"):
                in_anchor = max(0, in_anchor - 1)
            out.append(part)
            continue
        if in_anchor:
            out.append(part)
            continue
        buf: list[str] = []
        for display, href in iter_inline_segments(part):
            if href is None:
                buf.append(display)
            else:
                buf.append(
                    f'<a href="{escape(href, quote=True)}" '
                    f'rel="noopener noreferrer nofollow" target="_blank">'
                    f"{escape(display)}</a>"
                )
        out.append("".join(buf))
    return "".join(out)

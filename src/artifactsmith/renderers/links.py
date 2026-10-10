"""Public http(s) link policy shared by sanitizer and renderers.

Classification uses a de-obfuscated *view* plus WHATWG-style and urllib parses of
the exact emit string; all must agree on one public host. That agreement
guarantees accepted links do not reach private hosts; ``href_host`` is a
WHATWG-style approximation and does not claim every accepted link is
browser-valid. Emission keeps the original destination (trim, literal spaces →
``%20``, intentional ``//`` → ``https://``). Never HTML-entity-decode or
percent-decode on emit. Userinfo is rejected. Does not fetch anything.
"""

from __future__ import annotations

import re
import unicodedata
from html import escape, unescape
from typing import Literal
from urllib.parse import unquote, urlparse

from .href_host import hosts_agree_public
from .safety import URL_RE, _host_is_private, _url_host

HrefClass = Literal["fragment", "relative", "public", "blocked"]

_DANGEROUS_PREFIXES = ("javascript:", "vbscript:", "data:", "file:", "blob:")


def _strip_format_chars(s: str) -> str:
    return "".join(c for c in s if unicodedata.category(c) != "Cf")


def deobfuscate_href(value: str) -> str:
    """De-obfuscate an href/src for classification only.

    Percent-decodes repeatedly until stable, applies NFKC, strips Unicode format
    characters (Cf), tab/LF/CR anywhere, leading/trailing C0 and DEL, other
    whitespace, and backslashes. Never used as the emitted URL.
    """
    raw = unescape(value)
    for _ in range(8):
        nxt = unquote(raw)
        if nxt == raw:
            break
        raw = nxt
    raw = unicodedata.normalize("NFKC", raw)
    raw = _strip_format_chars(raw)
    # WHATWG strips tab/LF/CR in the URL; browsers strip C0/DEL at the ends.
    raw = raw.replace("\t", "").replace("\n", "").replace("\r", "")
    raw = re.sub(r"^[\x00-\x20\x7f]+|[\x00-\x20\x7f]+$", "", raw)
    raw = re.sub(r"\s+", "", raw)
    raw = raw.replace("\\", "/")
    return raw.strip()


def normalize_href(value: str) -> str:
    """Backward-compatible name for the classification de-obfuscation view."""
    return deobfuscate_href(value)


def emit_href(value: str) -> str:
    """Minimal emit normalization: trim and literal spaces → %20.

    Does not HTML-unescape (``&section=`` must stay ``&section=``) and does not
    percent-decode. Callers add format-specific escaping at write time.
    """
    return value.strip().replace(" ", "%20")


def _classify_view(view: str) -> HrefClass:
    """Classify an already-deobfuscated href view (scheme / private host only)."""
    if not view:
        return "blocked"
    if view.startswith("#"):
        return "fragment"
    low = view.lower()
    if low.startswith("//"):
        return "blocked"
    for prefix in _DANGEROUS_PREFIXES:
        if low.startswith(prefix):
            return "blocked"
    if low.startswith(("http://", "https://")):
        host = _url_host(view)
        if host is None or host == "" or _host_is_private(host):
            return "blocked"
        try:
            parsed = urlparse(view)
            if parsed.scheme not in ("http", "https"):
                return "blocked"
            if parsed.username is not None or parsed.password is not None:
                return "blocked"
            if "@" in (parsed.netloc or ""):
                return "blocked"
        except Exception:  # noqa: BLE001
            return "blocked"
        return "public"
    if re.match(r"^[a-z][a-z0-9+.-]*:", low):
        return "blocked"
    return "relative"


def classify_href(value: str) -> HrefClass:
    """Classify an href/src candidate; http(s) requires multi-parse host agreement."""
    emit = emit_href(value)
    # Protocol-relative stays blocked at classify time (upgrade only in public_href_or_none).
    if emit.startswith("//"):
        return "blocked"
    view = deobfuscate_href(value)
    kind = _classify_view(view)
    if kind != "public":
        return kind
    if not emit.lower().startswith(("http://", "https://")):
        return "blocked"
    if not hosts_agree_public(emit, deobfuscate=deobfuscate_href):
        return "blocked"
    return "public"


def is_public_http_url(url: str) -> bool:
    return classify_href(url) == "public"


def public_href_or_none(url: str) -> str | None:
    """Return an emit-ready public http(s) URL, or None if not allowed.

    Emit keeps the original bytes (plus trim / space→%20 / intentional ``//`` upgrade).
    Drops the link unless the de-obfuscated view is an allowed http(s) URL *and*
    urllib + WHATWG-style parses of the emit string agree on the same public host
    (private-host guarantee; not a claim that the URL is browser-valid).
    """
    emit = emit_href(url)
    if emit.startswith("//") and not emit.lower().startswith("///"):
        emit = "https:" + emit
    if not emit.lower().startswith(("http://", "https://")):
        return None
    if _classify_view(deobfuscate_href(emit)) != "public":
        return None
    if not hosts_agree_public(emit, deobfuscate=deobfuscate_href):
        return None
    return emit


def sanitize_markdown(text: str) -> str:
    """Apply link policy to markdown via CommonMark tokens (see ``md_sanitize``)."""
    from .md_sanitize import sanitize_markdown as _sanitize_markdown

    return _sanitize_markdown(text)


def linkify_markdown(text: str) -> str:
    """Backward-compatible name: full markdown link sanitization + linkify."""
    return sanitize_markdown(text)


def iter_inline_segments(text: str) -> list[tuple[str, str | None]]:
    """Split markdown into (display, href|None) for PDF/DOCX/XLSX renderers."""
    from .md_sanitize import iter_inline_segments as _iter_inline_segments

    return _iter_inline_segments(text)


def _plain_url_segments(text: str) -> list[tuple[str, str | None]]:
    """Bare-URL segments for HTML text nodes (not markdown-parsed)."""
    if not text:
        return []
    parts: list[tuple[str, str | None]] = []
    pos = 0
    for m in URL_RE.finditer(text):
        if m.start() > pos:
            parts.append((text[pos : m.start()], None))
        raw = m.group(0)
        core = raw.rstrip(".,;:)")
        trailing = raw[len(core) :]
        href = public_href_or_none(core)
        if href:
            parts.append((core, href))
            if trailing:
                parts.append((trailing, None))
        else:
            parts.append((raw, None))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], None))
    return parts


def harden_external_anchors(html: str) -> str:
    """Add rel and target on <a> tags whose href is public http(s)."""

    def repl(m: re.Match[str]) -> str:
        attrs = m.group(1)
        hm = re.search(r"""\bhref\s*=\s*("([^"]*)"|'([^']*)')""", attrs, re.I)
        if not hm:
            return m.group(0)
        href = hm.group(2) if hm.group(2) is not None else hm.group(3)
        if public_href_or_none(href or "") is None:
            return m.group(0)
        attrs2 = re.sub(r"""\s*\brel\s*=\s*(".*?"|'.*?')""", "", attrs, flags=re.I)
        attrs2 = re.sub(r"""\s*\btarget\s*=\s*(".*?"|'.*?')""", "", attrs2, flags=re.I)
        return f'<a{attrs2} rel="noopener noreferrer nofollow" target="_blank">'

    return re.sub(r"<a\b([^>]*)>", repl, html, flags=re.I)


def _html_label(text: str) -> str:
    """Escape once for HTML text nodes (normalize any pre-escaped entities)."""
    return escape(unescape(text), quote=False)


def linkify_html_text(html: str) -> str:
    """Wrap bare public URLs that appear in text nodes (not inside tags or <code>)."""
    parts = re.split(r"(<[^>]+>)", html)
    out: list[str] = []
    in_anchor = 0
    in_code = 0
    in_svg = 0
    for part in parts:
        if part.startswith("<"):
            low = part.lower()
            if low.startswith("<a ") or low.startswith("<a>"):
                in_anchor += 1
            elif low.startswith("</a"):
                in_anchor = max(0, in_anchor - 1)
            elif low.startswith("<svg"):
                in_svg += 1
            elif low.startswith("</svg"):
                in_svg = max(0, in_svg - 1)
            elif low.startswith("<code") or low.startswith("<pre"):
                in_code += 1
            elif low.startswith("</code") or low.startswith("</pre"):
                in_code = max(0, in_code - 1)
            out.append(part)
            continue
        if in_anchor or in_code or in_svg:
            out.append(part)
            continue
        buf: list[str] = []
        for display, href in _plain_url_segments(part):
            if href is None:
                buf.append(display)
            else:
                buf.append(
                    f'<a href="{escape(href, quote=True)}" '
                    f'rel="noopener noreferrer nofollow" target="_blank">'
                    f"{_html_label(display)}</a>"
                )
        out.append("".join(buf))
    return "".join(out)

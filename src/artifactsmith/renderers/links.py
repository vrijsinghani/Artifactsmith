"""Public http(s) link policy shared by sanitizer and renderers.

Allows navigational links to global hosts. Blocks private/loopback hosts,
dangerous schemes, and protocol-relative URLs. Does not fetch anything.
Markdown images pointing at remote URLs become normal links (nothing loads on open).
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

# Images, inline links, reference defs/uses, angle autolinks.
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_MD_REF_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)\s*$", re.M)
_MD_REF_USE_RE = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
_MD_AUTOLINK_RE = re.compile(r"<([^>\s]+)>")
_FENCE_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)")
_INLINE_CODE_RE = re.compile(r"`+[^`]+`+")


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
        try:
            parsed = urlparse(raw)
            if parsed.scheme not in ("http", "https"):
                return "blocked"
        except Exception:  # noqa: BLE001
            return "blocked"
        return "public"
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) or re.match(r"^[a-z][a-z0-9+.-]*:", compact):
        return "blocked"
    if low.startswith("//") or (low.startswith("/") and low[1:2] == "/"):
        return "blocked"
    return "relative"


def is_public_http_url(url: str) -> bool:
    return classify_href(url) == "public"


def public_href_or_none(url: str) -> str | None:
    """Return a normalized public http(s) URL, or None if not allowed.

    Protocol-relative ``//host/path`` is upgraded to ``https://host/path`` when the
    host is public, so citations stay clickable.
    """
    raw = normalize_href(url)
    if raw.startswith("//") and not raw.lower().startswith(("///",)):
        raw = "https:" + raw
    if classify_href(raw) != "public":
        return None
    return raw


def _label_or_image(alt: str) -> str:
    return alt.strip() or "image"


def _rewrite_prose(text: str) -> str:
    """Rewrite images, neutralize bad destinations, linkify bare URLs (no code)."""
    # Reference definitions first so uses can resolve.
    defs: dict[str, str] = {}
    for m in _MD_REF_DEF_RE.finditer(text):
        defs[m.group(1).strip().lower()] = m.group(2).strip()

    def repl_image(m: re.Match[str]) -> str:
        alt, dest = m.group(1), m.group(2)
        href = public_href_or_none(dest)
        if href:
            return f"[{_label_or_image(alt)}]({href})"
        return _label_or_image(alt)

    def repl_link(m: re.Match[str]) -> str:
        label, dest = m.group(1), m.group(2)
        href = public_href_or_none(dest)
        if href:
            return f"[{label}]({href})"
        # Relative/fragment stay; dangerous/private → label only.
        kind = classify_href(dest)
        if kind in ("relative", "fragment"):
            return m.group(0)
        return label

    def repl_ref_use(m: re.Match[str]) -> str:
        label, ref = m.group(1), (m.group(2) or m.group(1)).strip()
        dest = defs.get(ref.lower(), "")
        if not dest:
            return m.group(0)
        href = public_href_or_none(dest)
        if href:
            return f"[{label}]({href})"
        kind = classify_href(dest)
        if kind in ("relative", "fragment"):
            return m.group(0)
        return label

    def repl_ref_def(m: re.Match[str]) -> str:
        ident, dest = m.group(1), m.group(2)
        href = public_href_or_none(dest)
        if href:
            return f"[{ident}]: {href}"
        kind = classify_href(dest)
        if kind in ("relative", "fragment"):
            return m.group(0)
        # Drop dangerous/private definitions (uses already neutralized to label).
        return ""

    def repl_autolink(m: re.Match[str]) -> str:
        inner = m.group(1)
        href = public_href_or_none(inner)
        if href:
            return f"[{href}]({href})"
        kind = classify_href(inner)
        if kind in ("relative", "fragment"):
            return m.group(0)
        # Dangerous or private autolink → plain text without angle brackets if URL-like.
        if ":" in inner or inner.startswith("//"):
            return ""
        return m.group(0)

    out = _MD_IMAGE_RE.sub(repl_image, text)
    out = _MD_LINK_RE.sub(repl_link, out)
    out = _MD_REF_USE_RE.sub(repl_ref_use, out)
    out = _MD_REF_DEF_RE.sub(repl_ref_def, out)
    out = _MD_AUTOLINK_RE.sub(repl_autolink, out)
    # Collapse blank lines left by removed ref defs.
    out = re.sub(r"\n{3,}", "\n\n", out)

    # Linkify bare public URLs (skip anything already inside ](url) by scanning).
    parts: list[str] = []
    pos = 0
    for m in URL_RE.finditer(out):
        # Skip if this URL is a markdown destination: ...](URL
        start = m.start()
        if start >= 2 and out[start - 2 : start] == "](":
            continue
        parts.append(out[pos:start])
        raw = m.group(0)
        core = raw.rstrip(".,;:)")
        trailing = raw[len(core) :]
        href = public_href_or_none(core)
        if href:
            parts.append(f"[{href}]({href})")
            if trailing:
                parts.append(trailing)
        else:
            parts.append(raw)
        pos = m.end()
    parts.append(out[pos:])
    return "".join(parts)


def sanitize_markdown(text: str) -> str:
    """Apply link policy to markdown: images→links, neutralize bad dests, linkify.

    Code fences and inline code are left untouched (no linkify inside code).
    """
    if not text:
        return text

    def protect_inline(chunk: str) -> str:
        slots: list[str] = []

        def stash(m: re.Match[str]) -> str:
            slots.append(m.group(0))
            return f"\x00C{len(slots) - 1}\x00"

        protected = _INLINE_CODE_RE.sub(stash, chunk)
        rewritten = _rewrite_prose(protected)
        for i, original in enumerate(slots):
            rewritten = rewritten.replace(f"\x00C{i}\x00", original)
        return rewritten

    chunks = _FENCE_RE.split(text)
    out: list[str] = []
    for i, chunk in enumerate(chunks):
        if i % 2 == 1:
            # Fenced code — unchanged.
            out.append(chunk)
        else:
            out.append(protect_inline(chunk))
    return "".join(out)


def linkify_markdown(text: str) -> str:
    """Backward-compatible name: full markdown link sanitization + linkify."""
    return sanitize_markdown(text)


def iter_inline_segments(text: str) -> list[tuple[str, str | None]]:
    """Split text into (display, href|None) segments for renderers.

    Assumes sanitize_markdown already ran on stored bodies; still classifies
    destinations so dangerous leftovers never become hyperlinks.
    """
    if not text:
        return []
    parts: list[tuple[str, str | None]] = []
    pos = 0
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
                kind = classify_href(dest)
                if kind in ("relative", "fragment"):
                    parts.append((md.group(0), None))
                else:
                    parts.append((label, None))
            pos = md.end()
            continue
        if bare and next_bare < next_md:
            if bare.start() > pos:
                parts.append((text[pos : bare.start()], None))
            raw = bare.group(0)
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


def _html_label(text: str) -> str:
    """Escape once for HTML text nodes (normalize any pre-escaped entities)."""
    return escape(unescape(text), quote=False)


def linkify_html_text(html: str) -> str:
    """Wrap bare public URLs that appear in text nodes (not inside tags or <code>)."""
    parts = re.split(r"(<[^>]+>)", html)
    out: list[str] = []
    in_anchor = 0
    in_code = 0
    for part in parts:
        if part.startswith("<"):
            low = part.lower()
            if low.startswith("<a ") or low.startswith("<a>"):
                in_anchor += 1
            elif low.startswith("</a"):
                in_anchor = max(0, in_anchor - 1)
            elif low.startswith("<code") or low.startswith("<pre"):
                in_code += 1
            elif low.startswith("</code") or low.startswith("</pre"):
                in_code = max(0, in_code - 1)
            out.append(part)
            continue
        if in_anchor or in_code:
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
                    f"{_html_label(display)}</a>"
                )
        out.append("".join(buf))
    return "".join(out)

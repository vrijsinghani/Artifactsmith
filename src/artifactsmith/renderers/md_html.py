"""Raw HTML ``href`` / ``src`` policy for markdown-embedded tags.

Public http(s) attribute values are left byte-for-byte. Blocked or
authority-confused values drop that attribute (tag stays, no private link).
"""

from __future__ import annotations

import re

from .links import classify_href, public_href_or_none

# Same separator class as destination confusion in md_sanitize.
_DEST_SEP = r"[\t \x0b\x0c\r\n\u00a0\u200b\u3000\ufeff]"
_ATTR_RE = re.compile(
    r'(?i)(\s+)(href|src)(\s*=\s*)("([^"]*)"|\'([^\']*)\'|[^\s>]+)',
)
_ATTR_FIND_RE = re.compile(
    r'(?i)\b(href|src)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
)


def _attr_value_confused(value: str) -> bool:
    if re.search(rf"https?:{_DEST_SEP}+//", value, re.I):
        return True
    if re.search(rf"{_DEST_SEP}+@", value):
        return True
    if re.search(rf"{_DEST_SEP}+:[^\s]*@", value):
        return True
    return False


def href_src_attr_ok(value: str) -> bool:
    """True when an HTML href/src value may stay as written."""
    if _attr_value_confused(value):
        return False
    kind = classify_href(value)
    if kind == "public":
        return public_href_or_none(value) is not None
    return kind in ("relative", "fragment")


def sanitize_raw_html_attrs(html: str) -> str:
    """Drop blocked/confused href|src attributes; leave public values untouched."""
    if not html:
        return html

    def repl(m: re.Match[str]) -> str:
        quoted = m.group(4)
        if quoted.startswith('"'):
            val = quoted[1:-1]
        elif quoted.startswith("'"):
            val = quoted[1:-1]
        else:
            val = quoted
        if href_src_attr_ok(val):
            return m.group(0)
        return ""

    return _ATTR_RE.sub(repl, html)


def find_bad_html_attr_urls(text: str) -> list[str]:
    """Problems for blocked or authority-confused href/src in raw HTML."""
    problems: list[str] = []
    for m in _ATTR_FIND_RE.finditer(text):
        val = m.group(2) if m.group(2) is not None else m.group(3)
        if val is None:
            val = m.group(4) or ""
        if href_src_attr_ok(val):
            continue
        problems.append(f"blocked or authority-confused HTML {m.group(1)}: {val[:80]}")
    return problems

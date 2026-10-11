"""Inline Markdown to escaped HTML. Raw HTML is dropped except ``<br>``."""

from __future__ import annotations

import re
from html import escape

from markdown_it.token import Token

from ..renderers.links import public_href_or_none
from .model import ALLOWED_CLASSES, TONES, classify

_BR = re.compile(r"<br\s*/?>", re.I)
_COLSPAN = re.compile(r"\{(\d{1,2})\}\s*$")
MAX_COLSPAN = 12


def esc(text: str) -> str:
    return escape(text, quote=True)


def text_html(raw: str) -> str:
    """Escape text. A literal ``<br>`` becomes a break; anything else stays text."""
    parts = _BR.split(raw)
    if len(parts) == 1:
        return esc(parts[0])
    head = esc(parts[0])
    rest = "".join(f'<span class="sub">{esc(part)}</span>' for part in parts[1:])
    return head + rest


def scrub_bad_badges(raw: str) -> str:
    """Keep the label of a badge whose attributes are not on the allow-list.

    One forward pass. A run of ``[`` does not rescan the tail.
    """
    if "[" not in raw or "]" not in raw or "{" not in raw:
        return raw
    from mdit_py_plugins.attrs.parse import ParseError, parse

    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != "[":
            out.append(raw[i])
            i += 1
            continue
        j = i + 1
        while j < n and raw[j] not in "]\n[":
            j += 1
        if j >= n or raw[j] != "]" or j == i + 1 or j + 1 >= n or raw[j + 1] != "{":
            out.append(raw[i])
            i += 1
            continue
        k = j + 2
        while k < n and raw[k] not in "}\n{":
            k += 1
        if k >= n or raw[k] != "}":
            out.append(raw[i])
            i += 1
            continue
        label = raw[i + 1 : j]
        try:
            _name, attrs = parse("{" + raw[j + 2 : k] + "}")
        except ParseError:
            out.append(label)
            i = k + 1
            continue
        keys = {str(key) for key in attrs}
        classes = {tok for tok in str(attrs.get("class", "")).split() if tok}
        if keys <= {"class"} and classes and classes <= ALLOWED_CLASSES:
            out.append(raw[i : k + 1])
        else:
            out.append(label)
        i = k + 1
    return "".join(out)


def _collect(tokens: list[Token], start: int, close: str) -> tuple[list[Token], int]:
    inner: list[Token] = []
    i = start
    while i < len(tokens) and tokens[i].type != close:
        inner.append(tokens[i])
        i += 1
    if i < len(tokens) and tokens[i].type == close:
        i += 1
    return inner, i


def render_inlines(tokens: list[Token]) -> str:
    """Return HTML for an inline token list. Unsafe attributes and tags are not copied."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        kind = tok.type
        if kind == "text":
            out.append(text_html(scrub_bad_badges(tok.content or "")))
            i += 1
        elif kind == "softbreak":
            out.append(" ")
            i += 1
        elif kind == "hardbreak":
            out.append("<br>")
            i += 1
        elif kind == "code_inline":
            out.append(f"<code>{esc(tok.content or '')}</code>")
            i += 1
        elif kind == "html_inline":
            if _BR.fullmatch((tok.content or "").strip()):
                out.append("<br>")
            i += 1
        elif kind in ("strong_open", "em_open"):
            tag = "strong" if kind == "strong_open" else "em"
            close = "strong_close" if tag == "strong" else "em_close"
            inner, i = _collect(tokens, i + 1, close)
            out.append(f"<{tag}>{render_inlines(inner)}</{tag}>")
        elif kind == "span_open":
            inner, i = _collect(tokens, i + 1, "span_close")
            _attrs, classes = classify(tok.attrs)
            tone = next((name for name in classes if name in TONES), "")
            label = render_inlines(inner)
            if tone:
                out.append(f'<span class="asl-badge tone-{tone}">{label}</span>')
            else:
                out.append(label)
        elif kind == "link_open":
            inner, i = _collect(tokens, i + 1, "link_close")
            href = public_href_or_none(str((tok.attrs or {}).get("href") or ""))
            label = render_inlines(inner)
            if href:
                out.append(f'<a href="{esc(href)}">{label}</a>')
            else:
                out.append(label)
        elif kind == "image":
            alt = ""
            for child in tok.children or []:
                if child.type == "text":
                    alt += child.content or ""
            label = alt.strip() or "image"
            href = public_href_or_none(str((tok.attrs or {}).get("src") or ""))
            if href:
                out.append(f'<a class="asl-fig" href="{esc(href)}">{esc(label)}</a>')
            else:
                out.append(esc(label))
            i += 1
        else:
            i += 1
    return "".join(out)


def plain_text(tokens: list[Token]) -> str:
    parts: list[str] = []
    for tok in tokens:
        if tok.type in ("text", "code_inline"):
            parts.append(tok.content or "")
        elif tok.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif tok.children:
            parts.append(plain_text(tok.children))
    return "".join(parts)


def cell_html(tokens: list[Token], content: str) -> tuple[str, str, int]:
    """Inline HTML, plain text, and a trailing ``{N}`` column span."""
    span = 1
    match = _COLSPAN.search(content or "")
    if match and int(match.group(1)) >= 1:
        span = min(int(match.group(1)), MAX_COLSPAN)
    html = render_inlines(tokens)
    plain = _BR.sub(" ", plain_text(tokens))
    if span > 1:
        html = _COLSPAN.sub("", html).rstrip()
        plain = _COLSPAN.sub("", plain).rstrip()
    return html, plain.strip(), span

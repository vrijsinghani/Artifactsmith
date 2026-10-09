"""CommonMark markdown link sanitization via markdown-it-py tokens.

Regex link parsers miss titled destinations and whitespace forms. Every link and
image destination goes through the shared URL policy in ``links``.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .links import classify_href, public_href_or_none
from .md_serialize import escape_all_md_punctuation, serialize_blocks
from .safety import URL_RE


def _parser() -> MarkdownIt:
    """CommonMark parser that accepts every destination; we enforce policy ourselves."""
    md = MarkdownIt("commonmark", {"linkify": True})
    md.enable("linkify")
    # markdown-it rejects javascript:/etc. before we can neutralize them.
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    return md


def _attr_str(tok: Token, name: str) -> str:
    val = tok.attrGet(name)
    return "" if val is None else str(val)


def _label_text(tokens: list[Token]) -> str:
    parts: list[str] = []
    for t in tokens:
        if t.type in ("text", "code_inline"):
            parts.append(t.content or "")
        elif t.children:
            parts.append(_label_text(t.children))
    return "".join(parts)


def _text_token(content: str) -> Token:
    t = Token("text", "", 0)
    t.content = content
    return t


def _link_tokens(href: str, label: str, title: str | None = None) -> list[Token]:
    open_t = Token("link_open", "a", 1)
    open_t.attrSet("href", href)
    if title:
        open_t.attrSet("title", title)
    return [open_t, _text_token(label), Token("link_close", "a", -1)]


def _label_is_destination(label: str, href_raw: str) -> bool:
    """True when the label is the dangerous URL itself (autolink / mirrored dest)."""
    a, b = label.strip(), href_raw.strip()
    if not a:
        return True
    if a == b:
        return True
    return classify_href(a) == "blocked" and bool(re.match(r"^[a-z][a-z0-9+.-]*:", a, re.I))


def _rewrite_inline(children: list[Token] | None) -> list[Token]:
    if not children:
        return []
    out: list[Token] = []
    i = 0
    while i < len(children):
        tok = children[i]
        if tok.type == "image":
            src = _attr_str(tok, "src")
            label = (_label_text(tok.children or []) or (tok.content or "")).strip() or "image"
            href = public_href_or_none(src)
            if href:
                out.extend(_link_tokens(href, label))
            else:
                out.append(_text_token(label))
            i += 1
            continue
        if tok.type == "link_open":
            href_raw = _attr_str(tok, "href")
            title = _attr_str(tok, "title") or None
            inner: list[Token] = []
            i += 1
            while i < len(children) and children[i].type != "link_close":
                inner.append(children[i])
                i += 1
            if i < len(children) and children[i].type == "link_close":
                i += 1
            label = _label_text(inner) or href_raw
            href = public_href_or_none(href_raw)
            if href:
                out.extend(_link_tokens(href, label, title))
            else:
                kind = classify_href(href_raw)
                if kind in ("relative", "fragment"):
                    open_t = Token("link_open", "a", 1)
                    open_t.attrSet("href", href_raw)
                    if title:
                        open_t.attrSet("title", title)
                    out.append(open_t)
                    out.extend(_rewrite_inline(inner))
                    out.append(Token("link_close", "a", -1))
                elif inner and not _label_is_destination(label, href_raw):
                    out.extend(_rewrite_inline(inner))
                elif label and not _label_is_destination(label, href_raw):
                    out.append(_text_token(label))
            continue
        if tok.type == "code_inline":
            out.append(tok)
            i += 1
            continue
        if tok.type == "text":
            out.extend(_linkify_text_token(tok.content or ""))
            i += 1
            continue
        if tok.children:
            tok.children = _rewrite_inline(tok.children)
        out.append(tok)
        i += 1
    return out


def _linkify_text_token(text: str) -> list[Token]:
    if not text:
        return []
    parts: list[Token] = []
    pos = 0
    for m in URL_RE.finditer(text):
        if m.start() > pos:
            parts.append(_text_token(text[pos : m.start()]))
        raw = m.group(0)
        core = raw.rstrip(".,;:)")
        trailing = raw[len(core) :]
        href = public_href_or_none(core)
        if href:
            parts.extend(_link_tokens(href, href))
            if trailing:
                parts.append(_text_token(trailing))
        else:
            parts.append(_text_token(raw))
        pos = m.end()
    if pos < len(text):
        parts.append(_text_token(text[pos:]))
    return parts or [_text_token(text)]


def _linkify_prose_urls(text: str) -> str:
    """Linkify bare public URLs in residual HTML blocks (not markdown-parsed)."""
    if not text:
        return text
    parts: list[str] = []
    pos = 0
    for m in URL_RE.finditer(text):
        start = m.start()
        if start >= 2 and text[start - 2 : start] == "](":
            continue
        parts.append(text[pos:start])
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
    parts.append(text[pos:])
    return "".join(parts)


def _destinations_policy_clean(md_text: str) -> bool:
    """True when re-parsed output has no images and only allowed link destinations."""
    md = _parser()
    tokens = md.parse(md_text)

    def walk(children: list[Token] | None) -> bool:
        if not children:
            return True
        i = 0
        while i < len(children):
            tok = children[i]
            if tok.type == "image":
                return False
            if tok.type == "link_open":
                href = _attr_str(tok, "href")
                kind = classify_href(href)
                if kind == "public":
                    if public_href_or_none(href) is None:
                        return False
                elif kind not in ("relative", "fragment"):
                    return False
            if tok.children and not walk(tok.children):
                return False
            i += 1
        return True

    for tok in tokens:
        if tok.type == "inline" and tok.children is not None and not walk(tok.children):
            return False
        if tok.type == "image":
            return False
    return True


def collect_link_destinations(md_text: str) -> list[str]:
    """Return every link/image destination in ``md_text`` after CommonMark parse."""
    md = _parser()
    tokens = md.parse(md_text)
    found: list[str] = []

    def walk(children: list[Token] | None) -> None:
        if not children:
            return
        for tok in children:
            if tok.type == "image":
                found.append(_attr_str(tok, "src"))
            elif tok.type == "link_open":
                found.append(_attr_str(tok, "href"))
            if tok.children:
                walk(tok.children)

    for tok in tokens:
        if tok.type == "inline":
            walk(tok.children)
        elif tok.type == "image":
            found.append(_attr_str(tok, "src"))
    return found


def sanitize_markdown(text: str) -> str:
    """Rewrite markdown links/images via CommonMark tokens; leave code untouched.

    After serialize, re-parse and require policy-clean destinations (no images;
    only public/relative/fragment links). If that check fails, fall back to
    escaping all Markdown punctuation so nothing parses as a link or image.
    """
    if not text:
        return text
    md = _parser()
    tokens = md.parse(text)
    for tok in tokens:
        if tok.type == "inline" and tok.children is not None:
            tok.children = _rewrite_inline(tok.children)
        elif tok.type == "html_block" and tok.content:
            tok.content = _linkify_prose_urls(tok.content)
    out, _ = serialize_blocks(tokens)
    out = out.rstrip("\n")
    if text.endswith("\n") and out:
        out += "\n"
    if out and not _destinations_policy_clean(out):
        escaped = escape_all_md_punctuation(out)
        if text.endswith("\n") and escaped and not escaped.endswith("\n"):
            escaped += "\n"
        return escaped
    return out


def iter_inline_segments(text: str) -> list[tuple[str, str | None]]:
    """Flatten inline markdown to (display, href|None) for PDF/DOCX/XLSX.

    URLs inside code spans and fences are never hyperlinked.
    """
    if not text:
        return []
    md = _parser()
    tokens = md.parse(text)
    parts: list[tuple[str, str | None]] = []

    def emit_text(s: str) -> None:
        if s:
            parts.append((s, None))

    def walk_inline(children: list[Token] | None) -> None:
        if not children:
            return
        i = 0
        while i < len(children):
            tok = children[i]
            if tok.type == "code_inline":
                emit_text(tok.content or "")
                i += 1
                continue
            if tok.type == "link_open":
                href_raw = _attr_str(tok, "href")
                inner: list[Token] = []
                i += 1
                while i < len(children) and children[i].type != "link_close":
                    inner.append(children[i])
                    i += 1
                if i < len(children):
                    i += 1
                label = _label_text(inner) or href_raw
                href = public_href_or_none(href_raw)
                if href:
                    parts.append((label, href))
                else:
                    emit_text(label)
                continue
            if tok.type == "image":
                label = (_label_text(tok.children or []) or "image").strip() or "image"
                href = public_href_or_none(_attr_str(tok, "src"))
                if href:
                    parts.append((label, href))
                else:
                    emit_text(label)
                i += 1
                continue
            if tok.type == "text":
                raw = tok.content or ""
                pos = 0
                for m in URL_RE.finditer(raw):
                    if m.start() > pos:
                        emit_text(raw[pos : m.start()])
                    core = m.group(0).rstrip(".,;:)")
                    trailing = m.group(0)[len(core) :]
                    href = public_href_or_none(core)
                    if href:
                        parts.append((core, href))
                        if trailing:
                            emit_text(trailing)
                    else:
                        emit_text(m.group(0))
                    pos = m.end()
                if pos < len(raw):
                    emit_text(raw[pos:])
                i += 1
                continue
            if tok.type in ("softbreak", "hardbreak"):
                emit_text("\n")
                i += 1
                continue
            if tok.type in ("em_open", "em_close", "strong_open", "strong_close"):
                emit_text(tok.markup or "")
                i += 1
                continue
            if tok.children:
                walk_inline(tok.children)
            i += 1

    for tok in tokens:
        if tok.type == "inline":
            walk_inline(tok.children)
        elif tok.type in ("fence", "code_block"):
            emit_text(tok.content or "")
    return parts

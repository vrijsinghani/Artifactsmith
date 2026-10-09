"""Serialize markdown-it tokens back to CommonMark-ish markdown."""

from __future__ import annotations

import re

from markdown_it.token import Token

# Inline text: escape characters that can open links, images, emphasis, or HTML.
_MD_TEXT_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()!<>])")
# Fail-closed: every CommonMark-special character, plus :/@ so scheme/authority
# fragments (e.g. ``https:<TAB>//127.0.0.1``) cannot be re-linkified.
_MD_FAIL_CLOSED_SPECIALS = "\\`*_{}[]()#+.!|<>~-:@"
_MD_FAIL_CLOSED_RE = re.compile(r"([\\`*_{}\[\]()#+.!|<>~:@\-])")


def _unescape_md_specials(text: str, specials: str) -> str:
    """Remove one layer of backslash-escapes before ``specials`` (and backslash)."""
    allowed = set(specials) | {"\\"}
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n and text[i + 1] in allowed:
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def escape_md_text(text: str) -> str:
    """Escape Markdown-significant characters in a prose text token.

    Token content is raw display text (already unescaped by the parser), so every
    special is escaped exactly once.
    """
    return _MD_TEXT_ESCAPE_RE.sub(r"\\\1", text)


def escape_all_md_punctuation(text: str) -> str:
    """Fail-closed: escape every Markdown-special character in a full document.

    Idempotent: one unescape layer, then escape once (no doubled backslashes).
    """
    plain = _unescape_md_specials(text, _MD_FAIL_CLOSED_SPECIALS)
    return _MD_FAIL_CLOSED_RE.sub(r"\\\1", plain)


def _attr_str(tok: Token, name: str) -> str:
    val = tok.attrGet(name)
    return "" if val is None else str(val)


def _escape_md_link_dest(href: str) -> str:
    if re.search(r"[\s()]", href):
        return f"<{href}>"
    return href


def _escape_md_title(title: str) -> str:
    return '"' + title.replace('"', '\\"') + '"'


def serialize_inline(children: list[Token] | None) -> str:
    if not children:
        return ""
    parts: list[str] = []
    i = 0
    while i < len(children):
        tok = children[i]
        if tok.type == "text":
            parts.append(escape_md_text(tok.content or ""))
        elif tok.type == "code_inline":
            tick = tok.markup or "`"
            content = tok.content or ""
            # CommonMark: pad when content starts/ends with a backtick.
            if content.startswith("`") or content.endswith("`"):
                content = f" {content} "
            parts.append(f"{tick}{content}{tick}")
        elif tok.type == "softbreak":
            parts.append("\n")
        elif tok.type == "hardbreak":
            parts.append("  \n")
        elif tok.type == "link_open":
            href = _attr_str(tok, "href")
            title = _attr_str(tok, "title") or None
            inner: list[Token] = []
            i += 1
            while i < len(children) and children[i].type != "link_close":
                inner.append(children[i])
                i += 1
            label = serialize_inline(inner)
            if title:
                parts.append(f"[{label}]({_escape_md_link_dest(href)} {_escape_md_title(title)})")
            else:
                parts.append(f"[{label}]({_escape_md_link_dest(href)})")
        elif tok.type in ("em_open", "strong_open"):
            parts.append(tok.markup or ("*" if tok.type == "em_open" else "**"))
        elif tok.type in ("em_close", "strong_close"):
            parts.append(tok.markup or ("*" if tok.type == "em_close" else "**"))
        elif tok.type == "html_inline":
            parts.append(tok.content or "")
        elif tok.children:
            parts.append(serialize_inline(tok.children))
        i += 1
    return "".join(parts)


def _find_close(tokens: list[Token], open_idx: int, close_type: str) -> int:
    depth = 0
    open_type = tokens[open_idx].type
    for j in range(open_idx, len(tokens)):
        t = tokens[j].type
        if t == open_type:
            depth += 1
        elif t == close_type:
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _serialize_list(tokens: list[Token], start: int, end: int, *, ordered: bool) -> str:
    parts: list[str] = []
    i = start
    n = 1
    while i < end:
        tok = tokens[i]
        if tok.type == "list_item_open":
            close = _find_close(tokens, i, "list_item_close")
            body, _ = serialize_blocks(tokens, i + 1, close)
            bullet = f"{n}. " if ordered else "- "
            n += 1
            # Continuations need indent ≥ marker width (ordered ``1. `` is 3 spaces).
            indent = " " * len(bullet)
            lines = body.strip("\n").split("\n")
            if not lines:
                parts.append(bullet + "\n")
            else:
                parts.append(bullet + lines[0] + "\n")
                for ln in lines[1:]:
                    if ln == "":
                        parts.append("\n")
                    else:
                        parts.append(indent + ln + "\n")
            i = close + 1
        else:
            i += 1
    return "".join(parts)


def serialize_blocks(tokens: list[Token], i: int = 0, end: int | None = None) -> tuple[str, int]:
    parts: list[str] = []
    n = len(tokens) if end is None else end
    while i < n:
        tok = tokens[i]
        if tok.type == "inline":
            parts.append(serialize_inline(tok.children))
            i += 1
        elif tok.type == "paragraph_open":
            close = _find_close(tokens, i, "paragraph_close")
            body, _ = serialize_blocks(tokens, i + 1, close)
            parts.append(body.strip("\n") + "\n\n")
            i = close + 1
        elif tok.type == "heading_open":
            close = _find_close(tokens, i, "heading_close")
            level = int((tok.tag or "h1")[1] or "1")
            body, _ = serialize_blocks(tokens, i + 1, close)
            parts.append("#" * level + " " + body.strip() + "\n\n")
            i = close + 1
        elif tok.type == "fence":
            info = tok.info or ""
            mark = tok.markup or "```"
            parts.append(f"{mark}{info}\n{tok.content}{mark}\n\n")
            i += 1
        elif tok.type == "code_block":
            indented = "\n".join("    " + ln for ln in (tok.content or "").splitlines())
            parts.append(indented + "\n\n")
            i += 1
        elif tok.type == "blockquote_open":
            close = _find_close(tokens, i, "blockquote_close")
            body, _ = serialize_blocks(tokens, i + 1, close)
            quoted = "\n".join("> " + ln if ln else ">" for ln in body.rstrip("\n").split("\n"))
            parts.append(quoted + "\n\n")
            i = close + 1
        elif tok.type == "bullet_list_open":
            close = _find_close(tokens, i, "bullet_list_close")
            parts.append(_serialize_list(tokens, i + 1, close, ordered=False) + "\n")
            i = close + 1
        elif tok.type == "ordered_list_open":
            close = _find_close(tokens, i, "ordered_list_close")
            parts.append(_serialize_list(tokens, i + 1, close, ordered=True) + "\n")
            i = close + 1
        elif tok.type == "hr":
            parts.append("---\n\n")
            i += 1
        elif tok.type == "html_block":
            parts.append((tok.content or "") + "\n")
            i += 1
        else:
            i += 1
    return "".join(parts), i

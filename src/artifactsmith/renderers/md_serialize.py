"""Serialize markdown-it tokens back to CommonMark-ish markdown."""

from __future__ import annotations

import re

from markdown_it.token import Token


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
            parts.append(tok.content or "")
        elif tok.type == "code_inline":
            tick = tok.markup or "`"
            parts.append(f"{tick}{tok.content}{tick}")
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
            lines = body.strip("\n").split("\n")
            if not lines:
                parts.append(bullet + "\n")
            else:
                parts.append(bullet + lines[0] + "\n")
                for ln in lines[1:]:
                    parts.append("  " + ln + "\n")
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

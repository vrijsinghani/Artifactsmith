"""CommonMark markdown link sanitization via markdown-it-py tokens.

Regex link parsers miss titled destinations and whitespace forms. Every link and
image destination goes through the shared URL policy in ``links``.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .links import classify_href, public_href_or_none
from .md_html import find_bad_html_element_attrs, sanitize_raw_html_attrs
from .md_serialize import escape_all_md_punctuation, serialize_blocks
from .safety import URL_RE


def _parser(*, linkify: bool = True) -> MarkdownIt:
    """CommonMark parser that accepts every destination; we enforce policy ourselves."""
    md = MarkdownIt("commonmark", {"linkify": linkify})
    if linkify:
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


def _code_inline_token(content: str) -> Token:
    """Inline code with a backtick run longer than any run inside ``content``."""
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    t = Token("code_inline", "code", 0)
    t.markup = "`" * (longest + 1)
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


def _coalesce_text_softbreaks(children: list[Token]) -> list[Token]:
    """Join text/softbreak/hardbreak runs so LF inside a URL stays one span."""
    out: list[Token] = []
    i = 0
    while i < len(children):
        tok = children[i]
        if tok.type == "text":
            buf = [tok.content or ""]
            j = i + 1
            while j < len(children) and children[j].type in ("text", "softbreak", "hardbreak"):
                if children[j].type == "text":
                    buf.append(children[j].content or "")
                else:
                    buf.append("\n")
                j += 1
            out.append(_text_token("".join(buf)))
            i = j
            continue
        out.append(tok)
        i += 1
    return out


def _rewrite_inline(children: list[Token] | None) -> list[Token]:
    if not children:
        return []
    children = _coalesce_text_softbreaks(children)
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
            link_markup = tok.markup or ""
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
                elif link_markup in ("autolink", "linkify") or _label_is_destination(label, href_raw):
                    # Never emit a blocked destination as plain text (re-linkifies).
                    # Autolink / linkify / mirrored dest → inline code (incl. mailto).
                    if label:
                        out.append(_code_inline_token(label))
                elif inner:
                    out.extend(_rewrite_inline(inner))
                elif label:
                    out.append(_text_token(label))
            continue
        if tok.type == "code_inline":
            out.append(tok)
            i += 1
            continue
        if tok.type == "html_inline":
            raw = tok.content or ""
            cleaned = sanitize_raw_html_attrs(raw)
            if cleaned != raw:
                tok = Token("html_inline", "", 0)
                tok.content = cleaned
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


# Separators that join a URL to ``@…`` *inside a link destination* (…)/(…)/refdef.
_DEST_SEP = r"[\t \x0b\x0c\r\n\u00a0\u200b\u3000\ufeff]"
# Separators bare linkifiers actually consume into one URL (ZWSP); not SP/TAB/LF/CR/NBSP.
_BARE_JOIN_SEP = r"[\u200b\ufeff]"
_BROKEN_MD_LINK_BEFORE_RE = re.compile(r"\[([^\]]*)\]\($")
_BROKEN_MD_LINK_AFTER_RE = re.compile(r'^(?:\s+"[^"]*")?\)')
_LINK_OPEN_BEFORE_RE = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\($")
_LINK_OPEN_ANGLE_BEFORE_RE = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(<$")
_REF_DEF_BEFORE_RE = re.compile(r"^(.*)(\[[^\]\n]+\]:\s*)(<)?$", re.S)


def _confusion_ext_re(sep: str) -> re.Pattern[str]:
    return re.compile(
        rf"^(?:%[0-9A-Fa-f]{{2}})*{sep}+@[^\s\[\]<>)'\"]*"
        rf"|^{sep}+:[^\s\[\]<>)'\"]*@[^\s\[\]<>)'\"]*"
    )


def _scheme_split_re(sep: str) -> re.Pattern[str]:
    return re.compile(rf"https?:{sep}+//[^\s\[\]<>)'\"]+", re.I)


_DEST_EXT_RE = _confusion_ext_re(_DEST_SEP)
_BARE_EXT_RE = _confusion_ext_re(_BARE_JOIN_SEP)
# Scheme splits with SP/TAB/LF/CR still matter in destinations and bare prose.
_SCHEME_SPLIT_RE = _scheme_split_re(_DEST_SEP)


def _md_inline_code(content: str) -> str:
    """Serialize inline code the same way ``_code_inline_token`` would."""
    tok = _code_inline_token(content)
    ticks = tok.markup or "`"
    body = tok.content or ""
    if body.startswith("`") or body.endswith("`"):
        return f"{ticks} {body} {ticks}"
    return f"{ticks}{body}{ticks}"


def _span_is_confused(raw: str, sep: str) -> bool:
    if re.search(rf"https?:{sep}+//", raw, re.I):
        return True
    if re.search(rf"{sep}+@", raw):
        return True
    if re.search(rf"{sep}+:[^\s]*@", raw):
        return True
    return False


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _skip_char_ranges(text: str) -> list[tuple[int, int]]:
    """Ranges pretreat must not rewrite: code + raw HTML (markdown-it maps)."""
    md = _parser(linkify=False)
    tokens = md.parse(text)
    starts = _line_starts(text)
    n = len(text)
    ranges: list[tuple[int, int]] = []

    def add_lines(a: int, b: int) -> None:
        if a < 0 or a >= len(starts):
            return
        lo = starts[a]
        hi = starts[b] if b < len(starts) else n
        ranges.append((lo, hi))

    for tok in tokens:
        if tok.type in ("fence", "code_block", "html_block") and tok.map:
            add_lines(tok.map[0], tok.map[1])
        if tok.type != "inline" or not tok.children or not tok.map:
            continue
        a, b = tok.map[0], tok.map[1]
        if a >= len(starts):
            continue
        block_off = starts[a]
        block = text[block_off : starts[b] if b < len(starts) else n]
        cursor = 0
        for ch in tok.children:
            if ch.type == "html_inline":
                content = ch.content or ""
                if not content:
                    continue
                idx = block.find(content, cursor)
                if idx >= 0:
                    ranges.append((block_off + idx, block_off + idx + len(content)))
                    cursor = idx + len(content)
                continue
            if ch.type != "code_inline":
                continue
            ticks = ch.markup or "`"
            content = ch.content or ""
            candidates = [f"{ticks}{content}{ticks}"]
            if content.startswith("`") or content.endswith("`"):
                candidates.insert(0, f"{ticks} {content} {ticks}")
            for cand in candidates:
                idx = block.find(cand, cursor)
                if idx >= 0:
                    ranges.append((block_off + idx, block_off + idx + len(cand)))
                    cursor = idx + len(cand)
                    break
    return ranges


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < hi and end > lo for lo, hi in ranges)


def _is_destination_context(text: str, url_start: int) -> bool:
    before = text[:url_start]
    if _REF_DEF_BEFORE_RE.match(before):
        return True
    if _LINK_OPEN_BEFORE_RE.search(before) or _LINK_OPEN_ANGLE_BEFORE_RE.search(before):
        return True
    if before.endswith("<") and not before.endswith(")<"):
        return True
    return False


def _url_spans(
    text: str,
    *,
    ext_re: re.Pattern[str],
    scheme_re: re.Pattern[str],
) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    covered = [False] * (len(text) + 1)

    def add(start: int, end: int) -> None:
        if start < 0 or end <= start or any(covered[start:end]):
            return
        for i in range(start, end):
            covered[i] = True
        spans.append((start, end, text[start:end]))

    for m in URL_RE.finditer(text):
        start, end = m.start(), m.end()
        ext = ext_re.match(text[end:])
        if ext:
            end = end + ext.end()
        add(start, end)
    for m in scheme_re.finditer(text):
        add(m.start(), m.end())
    spans.sort(key=lambda s: s[0])
    return spans


def _span_is_safe_public(raw: str) -> str | None:
    """Public emit href, or None when scheme-split / sep-@ confused / blocked."""
    if _span_is_confused(raw, _DEST_SEP):
        return None
    return public_href_or_none(raw)


def _apply_neutralization(
    text: str,
    start: int,
    end: int,
    raw: str,
    pos: int,
    parts: list[str],
) -> int:
    """Append neutralization for text[start:end]; return new pos in ``text``."""
    core = raw.rstrip(".,;:)")
    trailing = raw[len(core) :]
    before = text[pos:start]
    after = text[end:]
    code_core = core.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    ref_m = _REF_DEF_BEFORE_RE.match(before)
    if ref_m is not None:
        parts.append(ref_m.group(1))
        rest = after
        if ref_m.group(3) and rest.startswith(">"):
            rest = rest[1:]
        title_m = re.match(r"""^\s*(?:"[^"]*"|'[^']*'|\([^)]*\))?""", rest)
        if title_m:
            rest = rest[title_m.end() :]
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n") or rest.startswith("\r"):
            rest = rest[1:]
        return end + (len(after) - len(rest))
    am = _BROKEN_MD_LINK_AFTER_RE.match(after)
    bm = _LINK_OPEN_BEFORE_RE.search(before) if am is not None else None
    if bm is not None and am is not None:
        parts.append(before[: bm.start()])
        parts.append(bm.group(1))
        return end + am.end()
    label_m = _LINK_OPEN_ANGLE_BEFORE_RE.search(before)
    if label_m is not None and after.startswith(">)"):
        parts.append(before[: label_m.start()])
        parts.append(label_m.group(1))
        return end + 2
    if before.endswith("<") and after.startswith(">"):
        parts.append(before[:-1])
        parts.append(_md_inline_code(code_core))
        return end + 1
    parts.append(before)
    parts.append(_md_inline_code(code_core))
    if trailing:
        parts.append(trailing)
    return end


def _pretreat_confused_urls(text: str) -> str:
    """Neutralize authority-confused destinations; leave bare URL + @mention alone.

    SP/TAB/LF/CR/NBSP + ``@`` is an attack only inside ``(…)``, ``<…>``, or a
    refdef destination. Bare URLs only join across ZWSP (what linkify consumes).
    Fence / indented / inline code and raw HTML ranges from markdown-it are skipped.
    """
    if not text:
        return text
    skip = _skip_char_ranges(text)
    work: list[tuple[int, int, str]] = []

    for start, end, raw in _url_spans(text, ext_re=_DEST_EXT_RE, scheme_re=_SCHEME_SPLIT_RE):
        if _overlaps(start, end, skip) or not _is_destination_context(text, start):
            continue
        core = raw.rstrip(".,;:)")
        if not _span_is_confused(raw, _DEST_SEP) and _span_is_safe_public(core) is not None:
            continue
        work.append((start, end, raw))

    for start, end, raw in _url_spans(text, ext_re=_BARE_EXT_RE, scheme_re=_SCHEME_SPLIT_RE):
        if _overlaps(start, end, skip) or _is_destination_context(text, start):
            continue
        # Bare: only ZWSP-joined @ or any scheme-split — never SP/TAB/LF/CR + @mention.
        if not (_span_is_confused(raw, _BARE_JOIN_SEP) or _SCHEME_SPLIT_RE.fullmatch(raw.rstrip(".,;:)"))):
            continue
        work.append((start, end, raw))

    if not work:
        return text
    work.sort(key=lambda t: t[0])
    parts: list[str] = []
    pos = 0
    for start, end, raw in work:
        if start < pos:
            continue
        pos = _apply_neutralization(text, start, end, raw, pos, parts)
    parts.append(text[pos:])
    return "".join(parts)


def _linkify_text_token(text: str) -> list[Token]:
    """Linkify residual bare public URLs; ZWSP-confused / private forms become code."""
    if not text:
        return []
    parts: list[Token] = []
    pos = 0
    for start, end, raw in _url_spans(text, ext_re=_BARE_EXT_RE, scheme_re=_SCHEME_SPLIT_RE):
        before = text[pos:start]
        after = text[end:]
        core = raw.rstrip(".,;:)")
        trailing = raw[len(core) :]
        bm = _BROKEN_MD_LINK_BEFORE_RE.search(before)
        am = _BROKEN_MD_LINK_AFTER_RE.match(after) if bm is not None else None
        if bm is not None and am is not None:
            parts.append(_text_token(before[: bm.start()]))
            if bm.group(1):
                parts.append(_text_token(bm.group(1)))
            pos = end + am.end()
            continue
        if before.endswith("<") and after.startswith(">"):
            parts.append(_text_token(before[:-1]))
            parts.append(_code_inline_token(core))
            pos = end + 1
            continue
        if before.endswith("(<") and after.startswith(">)"):
            label_m = re.search(r"\[([^\]]*)\]\(<$", before)
            if label_m is not None:
                parts.append(_text_token(before[: label_m.start()]))
                if label_m.group(1):
                    parts.append(_text_token(label_m.group(1)))
                pos = end + 2
                continue
        if before:
            parts.append(_text_token(before))
        safe = _span_is_safe_public(core) if not _span_is_confused(core, _BARE_JOIN_SEP) else None
        if safe is None and not _span_is_confused(core, _DEST_SEP):
            safe = public_href_or_none(core)
        if safe:
            parts.extend(_link_tokens(safe, safe))
        else:
            parts.append(_code_inline_token(core))
        if trailing:
            parts.append(_text_token(trailing))
        pos = end
    if pos < len(text):
        parts.append(_text_token(text[pos:]))
    return parts or [_text_token(text)]


def _destinations_policy_clean(md_text: str) -> bool:
    """True when re-parsed output has no images and only allowed link destinations.

    Linkify is on so bare private URLs that would re-linkify fail closed.
    Raw HTML URL attrs are checked only on ``html_inline`` / ``html_block`` tokens.
    """
    md = _parser(linkify=True)
    tokens = md.parse(md_text)

    def walk(children: list[Token] | None) -> bool:
        if not children:
            return True
        i = 0
        while i < len(children):
            tok = children[i]
            if tok.type == "image":
                return False
            if tok.type == "html_inline" and find_bad_html_element_attrs(tok.content or ""):
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
        if tok.type == "html_block" and find_bad_html_element_attrs(tok.content or ""):
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

    Pretreat authority-confused destinations first (destination SP/@ and bare
    ZWSP joins), then parse with linkify so email/www forms match the final
    policy check. After serialize, re-parse with linkify and require
    policy-clean destinations. If that check fails, fall back to escaping all
    Markdown punctuation.
    """
    if not text:
        return text
    text = _pretreat_confused_urls(text)
    md = _parser(linkify=True)
    tokens = md.parse(text)
    for tok in tokens:
        if tok.type == "inline" and tok.children is not None:
            tok.children = _rewrite_inline(tok.children)
        elif tok.type == "html_block" and tok.content:
            tok.content = sanitize_raw_html_attrs(tok.content)
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

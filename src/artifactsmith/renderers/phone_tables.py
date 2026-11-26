"""Stack wide tables on a phone for bold full-mode HTML.

The model writes the page. This pass adds a card layout under 40rem, the same
shape fast mode uses for ``table.stack``. Desktop rules are untouched.
"""

from __future__ import annotations

import re
from html import escape, unescape

_TABLE_TOKEN = re.compile(r"</?table\b[^>]*>", re.I)
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
_CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t\1>", re.I | re.S)
_COLSPAN = re.compile(r"""\bcolspan\s*=\s*["'](\d+)["']""", re.I)
_THEAD = re.compile(r"<thead\b[^>]*>(.*?)</thead>", re.I | re.S)
_CLASS = re.compile(r"""\bclass\s*=\s*(["'])""", re.I)
_TAG = re.compile(r"<[^>]+>")

_CARD_CSS = (
    "@media (max-width:40rem){"
    "table.am-stack{display:block;width:100%;max-width:100%}"
    "table.am-stack thead,table.am-stack tr.am-stack-head{display:none}"
    "table.am-stack tr{display:block;margin:0 0 .75rem;border:1px solid #ccc;padding:.45rem .65rem}"
    "table.am-stack td,table.am-stack th{display:block;border:0;text-align:left;"
    "padding:.2rem 0;white-space:normal}"
    "table.am-stack td::before,table.am-stack th::before{content:attr(data-label);"
    "display:block;font-size:.75rem;font-weight:600}"
    "}"
)


def stack_wide_tables(html: str) -> str:
    """Card-layout every table whose header row has four or more columns."""
    spans = _top_tables(html)
    if not spans:
        return html
    parts: list[str] = []
    cursor = 0
    changed = False
    for start, end in spans:
        parts.append(html[cursor:start])
        rewritten = _rewrite_table(html[start:end])
        changed = changed or rewritten != html[start:end]
        parts.append(rewritten)
        cursor = end
    parts.append(html[cursor:])
    if not changed:
        return html
    doc = "".join(parts)
    return _insert_css(doc)


def _top_tables(html: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for match in _TABLE_TOKEN.finditer(html):
        token = match.group(0)
        if token.lower().startswith("</"):
            if not stack:
                continue
            start = stack.pop()
            if not stack:
                spans.append((start, match.end()))
        else:
            stack.append(match.start())
    return spans


def _rewrite_table(table: str) -> str:
    header_html, labels, in_thead = _header(table)
    if len(labels) < 4 or not header_html:
        return table
    open_end = table.find(">") + 1
    opener = _add_class(table[:open_end], "am-stack")
    body = table[open_end:]
    if not in_thead:
        body = body.replace(header_html, _add_class(header_html, "am-stack-head"), 1)
    body = _ROW.sub(lambda match: _label_row(match.group(0), labels, header_html), body)
    return opener + body


def _header(table: str) -> tuple[str, list[str], bool]:
    thead = _THEAD.search(table)
    if thead:
        rows = _ROW.findall(thead.group(1))
        if not rows:
            return "", [], True
        row = rows[-1]
        return row, _labels(row), True
    rows = _ROW.findall(table)
    if not rows:
        return "", [], False
    return rows[0], _labels(rows[0]), False


def _labels(row: str) -> list[str]:
    labels: list[str] = []
    for _tag, attrs, inner in _CELL.findall(row):
        text = _TAG.sub("", unescape(inner))
        text = " ".join(text.split())
        span = _colspan(attrs)
        labels.extend([text] * span)
    return labels


def _colspan(attrs: str) -> int:
    match = _COLSPAN.search(attrs)
    if match is None:
        return 1
    return max(int(match.group(1)), 1)


def _label_row(row: str, labels: list[str], header_html: str) -> str:
    if row == header_html or "am-stack-head" in row:
        return row

    column = 0

    def one(match: re.Match[str]) -> str:
        nonlocal column
        tag, attrs, inner = match.group(1), match.group(2), match.group(3)
        span = _colspan(attrs)
        label = labels[column] if column < len(labels) else ""
        column += span
        if not label or re.search(r"\bdata-label\s*=", attrs, re.I):
            return match.group(0)
        safe = escape(label, quote=True)
        return f'<t{tag}{attrs} data-label="{safe}">{inner}</t{tag}>'

    return _CELL.sub(one, row)


def _add_class(tag: str, name: str) -> str:
    if re.search(rf"""\b{re.escape(name)}\b""", tag):
        return tag
    match = _CLASS.search(tag)
    if match is None:
        return tag[:-1] + f' class="{name}">'
    return tag[: match.end()] + name + " " + tag[match.end() :]


def _insert_css(html: str) -> str:
    if "table.am-stack" in html:
        return html
    style = f"<style>{_CARD_CSS}</style>"
    lower = html.lower()
    head = lower.rfind("</head>")
    if head >= 0:
        return html[:head] + style + html[head:]
    return style + html

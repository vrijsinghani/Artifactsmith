"""Parse and serialize the small SVG vocabulary after nh3."""

from __future__ import annotations

from html.parser import HTMLParser


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("", [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), [(n, v or "") for n, v in attrs])
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == name:
                del self._stack[i:]
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:  # noqa: ARG002
        return


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(value: str) -> str:
    return escape_text(value).replace('"', "&quot;")


def serialize(node: Node) -> str:
    if not node.tag:
        return "".join(serialize(c) if isinstance(c, Node) else escape_text(c) for c in node.children)
    attrs = "".join(f' {name}="{escape_attr(value)}"' for name, value in node.attrs)
    inner = "".join(serialize(c) if isinstance(c, Node) else escape_text(c) for c in node.children)
    return f"<{node.tag}{attrs}>{inner}</{node.tag}>"


def parse_fragment(fragment: str) -> Node:
    parser = _Tree()
    parser.feed(fragment)
    parser.close()
    return parser.root


def _tag_name_at(low: str, i: int) -> tuple[str, bool, int] | None:
    if i >= len(low) or low[i] != "<":
        return None
    j = i + 1
    ended = j < len(low) and low[j] == "/"
    if ended:
        j += 1
    start = j
    while j < len(low) and low[j] not in " \t\n\r/>":
        j += 1
    if start == j:
        return None
    return low[start:j], ended, j


def remove_elements_with_content(html: str, names: frozenset[str]) -> str:
    """Delete listed elements and their descendants (case-insensitive local names)."""
    wanted = {name.lower() for name in names}
    low = html.lower()
    parts: list[str] = []
    i = 0
    n = len(html)
    while i < n:
        lt = html.find("<", i)
        if lt == -1:
            parts.append(html[i:])
            break
        parts.append(html[i:lt])
        parsed = _tag_name_at(low, lt)
        if parsed is None or parsed[0] not in wanted:
            gt = _tag_gt(html, lt)
            if gt == -1:
                parts.append(html[lt:])
                break
            parts.append(html[lt : gt + 1])
            i = gt + 1
            continue
        name, ended, _rest = parsed
        gt = _tag_gt(html, lt)
        if gt == -1:
            break
        if ended or html[gt - 1] == "/":
            i = gt + 1
            continue
        depth = 1
        k = gt + 1
        while k < n:
            nxt = html.find("<", k)
            if nxt == -1:
                i = n
                break
            inner = _tag_name_at(low, nxt)
            inner_gt = _tag_gt(html, nxt)
            if inner is None or inner_gt == -1:
                k = nxt + 1
                continue
            inner_name, inner_end, _ = inner
            if inner_name != name:
                k = inner_gt + 1
                continue
            if inner_end:
                depth -= 1
                k = inner_gt + 1
                if depth == 0:
                    i = k
                    break
                continue
            if html[inner_gt - 1] != "/":
                depth += 1
            k = inner_gt + 1
        else:
            i = n
    return "".join(parts)


def _tag_gt(html: str, start: int) -> int:
    quote = ""
    for i, ch in enumerate(html[start:], start):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ">":
            return i
    return -1


def svg_root_spans(html: str) -> list[tuple[int, int]]:
    """Byte spans of top-level ``<svg>…</svg>`` roots, including nested roots."""
    spans: list[tuple[int, int]] = []
    low = html.lower()
    i = 0
    n = len(html)
    while i < n:
        j = low.find("<svg", i)
        if j == -1:
            break
        if j + 4 < n and low[j + 4] not in " \t\n\r>/":
            i = j + 4
            continue
        depth = 0
        k = j
        end = -1
        while k < n:
            if low.startswith("<svg", k) and (k + 4 == n or low[k + 4] in " \t\n\r>/"):
                gt = _tag_gt(html, k)
                if gt == -1:
                    return spans
                depth += 1
                k = gt + 1
                continue
            if low.startswith("</svg", k):
                gt = html.find(">", k)
                if gt == -1:
                    return spans
                depth -= 1
                k = gt + 1
                if depth == 0:
                    end = k
                    break
                continue
            k += 1
        if end == -1:
            break
        spans.append((j, end))
        i = end
    return spans

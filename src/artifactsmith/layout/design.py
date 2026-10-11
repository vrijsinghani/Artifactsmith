"""Style-specific structure added by the renderer. The model does not write CSS."""

from __future__ import annotations

import re

from .inline import esc
from .model import Block

_RISK_HEAD = re.compile(r"\b(risks?|issues?|concerns?|watch-?outs?)\b", re.I)
_STRONG = re.compile(r"<strong>([^<]*)</strong>")


def design_page(style: str, lead: list[Block], sections: list[list[Block]]) -> tuple[list[Block], list[list[Block]]]:
    """Add the structures each style is known for. House already has its own lead design."""
    if style == "bold":
        return _bold_band(lead), _mark_risks(sections)
    if style == "editorial":
        if not _mark_pull(lead, sections):
            text = _find_pull(lead, sections)
            if text:
                lead = _insert_pull(lead, text)
        _apply_drop(lead, sections)
        return lead, sections
    if style == "playful":
        _tag_first(lead, {"hero", "callout"}, "sticker")
        for section in sections:
            _add_class(section[0], "sticker")
        return lead, sections
    if style == "terminal":
        _tag_first(lead, {"callout"}, "term")
        _tag_lists(lead, sections, "log")
        return lead, sections
    if style == "swiss" and not any(block.kind == "hero" for block in lead):
        _tag_first(lead, {"callout"}, "swiss")
    return lead, sections


def _bold_band(lead: list[Block]) -> list[Block]:
    """One dark band holds the opening verdict or hero and the stat tiles."""
    hero_at: int | None = None
    stat_at: int | None = None
    for index, block in enumerate(lead):
        if hero_at is None and (block.kind == "hero" or (block.kind == "callout" and "verdict" in block.classes)):
            hero_at = index
        elif stat_at is None and _is_stat_grid(block):
            stat_at = index
    chosen = {index for index in (hero_at, stat_at) if index is not None}
    if not chosen:
        return lead
    band = Block(kind="band", children=[lead[index] for index in sorted(chosen)])
    out: list[Block] = []
    placed = False
    for index, block in enumerate(lead):
        if index in chosen:
            if not placed:
                out.append(band)
                placed = True
            continue
        out.append(block)
    return out


def _is_stat_grid(block: Block) -> bool:
    return bool(block.kind == "grid" and block.children and all(child.kind == "stat" for child in block.children))


def _mark_risks(sections: list[list[Block]]) -> list[list[Block]]:
    for section in sections:
        if not section or _RISK_HEAD.search(section[0].plain) is None:
            continue
        for block in section[1:]:
            items = _items(block)
            if items is None or not 2 <= len(items) <= 8:
                continue
            if any(not item.plain.strip() or len(item.plain) > 160 for item in items):
                continue
            target = block.children[0] if _wrapped_list(block) else block
            _add_class(target, "risks")
            for index, item in enumerate(items):
                _add_class(item, f"tone-{_risk_tone(item.plain, index)}")
            break
    return sections


def _risk_tone(text: str, index: int) -> str:
    low = text.lower()
    if any(word in low for word in ("critical", "severe", "high", "blocked", "fail")):
        return "red"
    if any(word in low for word in ("medium", "watch", "warn", "open", "delay")):
        return "amber"
    if any(word in low for word in ("low", "mitigated", "done", "clear")):
        return "green"
    return ("red", "amber", "blue", "green")[index % 4]


def _apply_drop(lead: list[Block], sections: list[list[Block]]) -> None:
    _apply_drop_nodes(lead) or any(_apply_drop_nodes(section) for section in sections)


def _apply_drop_nodes(nodes: list[Block]) -> bool:
    for block in nodes:
        if block.kind in {"paragraph", "hero"} and len(block.plain) >= 40:
            updated = _with_drop(block.html)
            if updated != block.html:
                block.html = updated
                _add_class(block, "drop")
                return True
        if block.kind in {"table", "chart", "code", "stat"}:
            continue
        if block.children and _apply_drop_nodes(block.children):
            return True
    return False


def _with_drop(html: str) -> str:
    if not html:
        return html
    if html[0].isalpha() or html[0].isdigit():
        return f'<span class="cap">{html[0]}</span>{html[1:]}'
    match = re.match(r"^(<(?:strong|em|b)>)([A-Za-z0-9])", html)
    if match is None:
        return html
    return f'{match.group(1)}<span class="cap">{match.group(2)}</span>{html[match.end() :]}'


def _mark_pull(lead: list[Block], sections: list[list[Block]]) -> bool:
    """Use a written ``>`` quote or ``::: quote`` as the pull quote."""
    for block in _walk(lead, sections):
        if block.kind == "quote":
            _add_class(block, "pull")
            return True
    return False


def _find_pull(lead: list[Block], sections: list[list[Block]]) -> str:
    for block in _walk(lead, sections):
        if block.kind == "quote":
            continue
        text = _pull_text(block.html, block.plain)
        if text:
            return text
    return ""


def _pull_text(html: str, plain: str) -> str:
    strongs = [re.sub(r"<[^>]+>", "", str(part)).strip() for part in _STRONG.findall(html)]
    strongs = [part for part in strongs if part]
    if not strongs:
        return ""
    for text in strongs:
        if text[-1:] in ".!?" and 12 <= len(text) <= 220:
            return text
    for text in strongs:
        if 12 <= len(text) <= 80:
            return text
    # Split on sentence ends, not on decimal points inside "$15,459.15".
    for sentence in re.split(r"(?<=[.!?])\s+", plain.strip()):
        sentence = sentence.strip()
        if 20 <= len(sentence) <= 220 and any(part in sentence for part in strongs):
            return sentence
    return ""


def _insert_pull(lead: list[Block], text: str) -> list[Block]:
    quote = Block(kind="quote", classes=("pull",), children=[Block(kind="paragraph", html=esc(text), plain=text)])
    for index, block in enumerate(lead):
        if block.kind in {"hero", "callout", "band"}:
            return [*lead[: index + 1], quote, *lead[index + 1 :]]
    return [quote, *lead]


def _tag_first(blocks: list[Block], kinds: set[str], name: str) -> None:
    for block in blocks:
        if block.kind in kinds:
            _add_class(block, name)
            return


def _tag_lists(lead: list[Block], sections: list[list[Block]], name: str) -> None:
    for block in _walk(lead, sections):
        if block.kind == "list" and _items(block) is not None and not _wrapped_list(block):
            _add_class(block, name)


def _walk(lead: list[Block], sections: list[list[Block]]) -> list[Block]:
    found: list[Block] = []

    def visit(nodes: list[Block]) -> None:
        for block in nodes:
            found.append(block)
            if block.kind not in {"table", "chart"}:
                visit(block.children)

    visit(lead)
    for section in sections:
        visit(section)
    return found


def _items(block: Block) -> list[Block] | None:
    if block.kind in {"list", "olist"} and block.children and block.children[0].kind == "item":
        return list(block.children)
    if _wrapped_list(block):
        return _items(block.children[0])
    return None


def _wrapped_list(block: Block) -> bool:
    return (
        block.kind == "list"
        and len(block.children) == 1
        and block.children[0].kind in {"list", "olist"}
        and bool(block.children[0].children)
        and block.children[0].children[0].kind == "item"
    )


def _add_class(block: Block, name: str) -> None:
    if name not in block.classes:
        block.classes = (*block.classes, name)

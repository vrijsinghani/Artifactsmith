"""Style-specific structure added by the renderer. The model does not write CSS."""

from __future__ import annotations

import re

from .inline import esc
from .model import Block

_RISK_HEAD = re.compile(r"\b(risks?|issues?|concerns?|watch-?outs?)\b", re.I)
_RISK_MAX = 320
_STRONG = re.compile(r"<strong>([^<]*)</strong>")
_LEAD = re.compile(r"^(\s*)<strong>([^<]*)</strong>:?\s*")
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE = re.compile(
    rf"^(?:{_MONTH}\.?\s+\d{{1,2}}(?:,\s*\d{{4}})?|\d{{4}}|q[1-4]\s+\d{{4}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}})"
    rf"(?:\s*(?:to|through|–|—|-)\s*(?:{_MONTH}\.?\s+)?\d{{1,2}}(?:,\s*\d{{4}})?)?$",
    re.I,
)
_VERB = re.compile(
    r"\b(?:is|are|was|were|be|been|being|has|have|had|do|does|did|will|can|could|should|would|"
    r"may|might|must|need|needs|owe|owes|owed|hold|holds|held|keep|keeps|make|makes|made|"
    r"pay|pays|paid|show|shows|include|includes|cover|covers|approve|approves|approved|"
    r"slip|slips|remain|remains|reach|reaches|fall|falls|rise|rises|leave|leaves|take|takes|"
    r"come|comes|say|says|said|call|calls|give|gives|start|starts|expect|expects|expected|"
    r"estimate|estimates|estimated|recommend|recommends|decide|decides|decided|require|requires|"
    r"bill|billed|grow|grows|stay|stays|turn|turns|lead|leads|want|wants|use|uses|used|work|works|"
    r"run|runs|look|looks|find|finds|found|open|opens|close|closes|end|ends|stop|stops|see|sees|"
    r"get|gets|go|goes|sold)\b",
    re.I,
)


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
            if any(not item.plain.strip() or len(item.plain) > _RISK_MAX for item in items):
                continue
            target = block.children[0] if _wrapped_list(block) else block
            _add_class(target, "risks")
            for index, item in enumerate(items):
                _add_class(item, f"tone-{_risk_tone(item.plain, index)}")
                _risk_title(item)
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


def _risk_title(item: Block) -> None:
    """The bold lead phrase is the card title. The rest of the item stays body copy."""
    nodes = [child for child in item.children if child.kind == "paragraph"] or [item]
    for node in nodes:
        match = _LEAD.match(node.html)
        if match is None:
            continue
        title = match.group(2).strip().rstrip(":").strip()
        if not title:
            continue
        node.html = f'{match.group(1)}<strong class="risk-title">{title}</strong>{node.html[match.end() :]}'
        item.html = node.html
        return


_DROP_MIN = 120
_LABEL_LINE = re.compile(r"^[A-Za-z][^:\n]{0,40}:\s")


def _apply_drop(lead: list[Block], sections: list[list[Block]]) -> None:
    """Opening prose only: a long paragraph in the lead or the first two sections."""
    pools = [lead, *sections[:2]]
    for block in _body_paragraphs(pools):
        if _opening_prose(block):
            _add_class(block, "drop")
            return


def _opening_prose(block: Block) -> bool:
    text = block.plain.strip()
    if len(text) < _DROP_MIN or not text[0].isalpha():
        return False
    return _LABEL_LINE.match(text) is None


def _body_paragraphs(pools: list[list[Block]]) -> list[Block]:
    found: list[Block] = []

    def visit(nodes: list[Block]) -> None:
        for block in nodes:
            if block.kind in {"hero", "callout", "band", "quote"}:
                continue
            if block.kind == "paragraph":
                found.append(block)
            elif block.kind in {"grid", "flow", "compare"}:
                visit(block.children)

    for pool in pools:
        visit(pool)
    return found


def _mark_pull(lead: list[Block], sections: list[list[Block]]) -> bool:
    """Use a section ``>`` quote when it is a sentence the opening did not already say."""
    opening = _opening_text(lead)
    for block in _walk([], sections):
        text = _block_plain(block)
        if block.kind == "quote" and _is_pull_sentence(text) and not _reuses_opening(text, opening):
            _add_class(block, "pull")
            return True
    return False


def _find_pull(lead: list[Block], sections: list[list[Block]]) -> str:
    opening = _opening_text(lead)
    for block in _walk([], sections):
        if block.kind == "quote":
            continue
        text = _pull_text(block.html, block.plain)
        if text and not _reuses_opening(text, opening):
            return text
    return ""


def _opening_text(lead: list[Block]) -> str:
    """Hero, verdict, and note text. A pull quote must not repeat it."""
    parts: list[str] = []

    def visit(nodes: list[Block], *, inside: bool) -> None:
        for block in nodes:
            now = inside or block.kind in {"hero", "callout", "band"}
            if now and block.kind not in {"band", "callout"} and block.plain.strip():
                parts.append(block.plain.strip())
            if now:
                visit(block.children, inside=True)

    visit(lead, inside=False)
    return " ".join(parts)


def _reuses_opening(sentence: str, opening: str) -> bool:
    if not opening:
        return False
    needle = re.sub(r"\s+", " ", sentence).strip().casefold().rstrip(".!?")
    folded = re.sub(r"\s+", " ", opening).casefold()
    return bool(needle) and needle in folded


def _pull_text(html: str, plain: str) -> str:
    strongs = [re.sub(r"<[^>]+>", "", str(part)).strip() for part in _STRONG.findall(html)]
    strongs = [part for part in strongs if part]
    sentences = _sentences(plain)
    for text in strongs:
        if _is_pull_sentence(text):
            return text
    for sentence in sentences:
        if any(part in sentence for part in strongs) and _is_pull_sentence(sentence):
            return sentence
    for sentence in sentences:
        if _is_pull_sentence(sentence):
            return sentence
    return ""


def _sentences(plain: str) -> list[str]:
    # Split on sentence ends, not on decimal points inside "$15,459.15".
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", plain.strip()) if part.strip()]


def _is_pull_sentence(text: str) -> bool:
    """A pull quote is a sentence with a verb and at least eight words."""
    cleaned = text.strip()
    if not cleaned or _date_or_number(cleaned):
        return False
    words = cleaned.split()
    if len(words) < 8 or len(cleaned) > 220:
        return False
    return _VERB.search(cleaned) is not None


def _date_or_number(text: str) -> bool:
    cleaned = text.strip().rstrip(".!?")
    if _DATE.fullmatch(cleaned):
        return True
    letters = re.findall(r"[A-Za-z]{2,}", cleaned)
    return not letters and bool(re.search(r"\d", cleaned))


def _block_plain(block: Block) -> str:
    if block.plain.strip():
        return block.plain.strip()
    return " ".join(child.plain for child in block.children).strip()


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

"""Find/replace patches apply to the original base file or fail closed."""

from __future__ import annotations

import pytest

from artifactsmith.patch import (
    PATCH_SYSTEM_ADDENDUM,
    Hunk,
    PatchError,
    apply_patch,
    css_only_move,
    parse_patch_reply,
)


def _reply(find: str, replace: str, *, summary: str = "Changed one span.", extra: str = "") -> str:
    return (
        "===ASSUMPTIONS===\n"
        "- kept the other rows\n"
        "===SUMMARY===\n"
        f"{summary}\n"
        "===PATCH===\n"
        f"{extra}"
        "<<<FIND>>>\n"
        f"{find}\n"
        "<<<REPLACE>>>\n"
        f"{replace}\n"
        "<<<END>>>\n"
        "===END===\n"
    )


BASE = """# Store report

North is the lead store.

| Store | Count |
| --- | --- |
| North | 12 |
| South | 9 |

## Hours

North opens at 8.

## Notes

South is the backup.
"""


def test_change_number_and_keep_the_rest():
    parsed = parse_patch_reply(_reply("| North | 12 |", "| North | 18 |"))
    assert parsed.summary == "Changed one span."
    assert parsed.assumptions == ["kept the other rows"]
    updated = apply_patch(BASE, parsed.hunks)
    assert "| North | 18 |" in updated
    assert "| South | 9 |" in updated
    assert "South is the backup." in updated
    assert "| North | 12 |" not in updated


def test_move_section_and_retitle_in_one_span():
    old = "## Hours\n\nNorth opens at 8.\n\n## Notes\n\nSouth is the backup."
    new = "## Backup\n\nSouth is the backup.\n\n## Hours\n\nNorth opens at 8."
    updated = apply_patch(BASE, parse_patch_reply(_reply(old, new, summary="Moved notes above hours.")).hunks)
    assert updated.index("## Backup") < updated.index("## Hours")
    assert "## Notes" not in updated
    assert "North is the lead store." in updated
    assert "| North | 12 |" in updated


def test_retitle():
    updated = apply_patch(BASE, parse_patch_reply(_reply("# Store report", "# Pilot stores")).hunks)
    assert updated.startswith("# Pilot stores\n")
    assert "| North | 12 |" in updated


def test_two_hunks_match_the_original_and_ignore_order():
    first = parse_patch_reply(
        "===PATCH===\n"
        "<<<FIND>>>\n| North | 12 |\n<<<REPLACE>>>\n| North | 18 |\n<<<END>>>\n"
        "<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# Pilot stores\n<<<END>>>\n"
        "===END===\n"
    ).hunks
    second = list(reversed(first))
    assert apply_patch(BASE, first) == apply_patch(BASE, second)
    assert apply_patch(BASE, first).startswith("# Pilot stores\n")


def test_delete_span():
    updated = apply_patch(BASE, parse_patch_reply(_reply("## Notes\n\nSouth is the backup.\n", "")).hunks)
    assert "South is the backup." not in updated
    assert "## Hours" in updated


def test_trailing_whitespace_still_unique():
    base = "Count 12  \nNext line\n"
    parsed = parse_patch_reply(_reply("Count 12\nNext line", "Count 18\nNext line"))
    assert apply_patch(base, parsed.hunks) == "Count 18\nNext line\n"


def test_search_replace_blocks_and_outer_fence():
    text = (
        "```text\n"
        "===SUMMARY===\nRetitled.\n"
        "===PATCH===\n"
        "<<<<<<< SEARCH\n"
        "# Store report\n"
        "=======\n"
        "# Pilot stores\n"
        ">>>>>>> REPLACE\n"
        "===END===\n"
        "```"
    )
    updated = apply_patch(BASE, parse_patch_reply(text).hunks)
    assert updated.startswith("# Pilot stores\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no markers", "no patch section"),
        ("===PATCH===\nnot a hunk\n===END===\n", "empty patch"),
        (
            "===PATCH===\n<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# x\n<<<END>>>\nleftover\n===END===\n",
            "malformed patch",
        ),
        (_reply("", "x"), "empty find"),
        ("===PATCH===\n<<<REPLACE>>>\nx\n<<<END>>>\n===END===\n", "malformed patch"),
        ("===PATCH===\n<<<FIND>>>\n# Store report\n<<<END>>>\n===END===\n", "malformed patch"),
        (
            "===PATCH===\n<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# x\n<<<REPLACE>>>\n# y\n<<<END>>>\n===END===\n",
            "malformed patch",
        ),
        ("===PATCH===\n<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# x\n===END===\n", "malformed patch"),
    ],
)
def test_malformed_replies_raise(text: str, message: str):
    with pytest.raises(PatchError, match=message):
        parse_patch_reply(text)


def test_missing_and_ambiguous_and_overlap():
    parsed = parse_patch_reply(_reply("missing row", "x"))
    with pytest.raises(PatchError, match="did not match"):
        apply_patch(BASE, parsed.hunks)
    ambiguous = parse_patch_reply(_reply("South", "East"))
    with pytest.raises(PatchError, match="more than once"):
        apply_patch(BASE, ambiguous.hunks)
    base = "AA \nBB \n\nAA  \nBB\t\n"
    fuzzy = parse_patch_reply(_reply("AA\nBB", "CC"))
    with pytest.raises(PatchError, match="more than once"):
        apply_patch(base, fuzzy.hunks)
    overlap = parse_patch_reply(
        "===PATCH===\n"
        "<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# A\n<<<END>>>\n"
        "<<<FIND>>>\n# Store report\n\nNorth is the lead store.\n<<<REPLACE>>>\n# B\n<<<END>>>\n"
        "===END===\n"
    )
    with pytest.raises(PatchError, match="overlapping"):
        apply_patch(BASE, overlap.hunks)


def test_indented_lines_and_apply_guards():
    base = "  Count 12 \nNext\n"
    updated = apply_patch(base, parse_patch_reply(_reply("  Count 12\nNext", "  Count 18\nNext")).hunks)
    assert updated == "  Count 18\nNext\n"
    crlf = apply_patch("Count 12\r\nNext\r\n", parse_patch_reply(_reply("Count 12\nNext", "Count 18\nNext")).hunks)
    assert crlf == "Count 18\nNext\r\n"
    with pytest.raises(PatchError, match="did not match"):
        apply_patch("only \n", parse_patch_reply(_reply("only\nsecond", "x")).hunks)
    with pytest.raises(PatchError, match="did not match"):
        apply_patch("a", [Hunk("\n", "x")])
    with pytest.raises(PatchError, match="empty patch"):
        apply_patch("a", [])
    with pytest.raises(PatchError, match="too many hunks"):
        apply_patch("u0", [Hunk(f"u{i}", "v") for i in range(33)])
    with pytest.raises(PatchError, match="malformed"):
        parse_patch_reply("===PATCH===\nnote\n<<<FIND>>>\n# Store report\n<<<REPLACE>>>\n# x\n<<<END>>>\n===END===\n")


def test_too_many_hunks():
    blocks = "".join(f"<<<FIND>>>\nunique-{i}\n<<<REPLACE>>>\nchanged-{i}\n<<<END>>>\n" for i in range(33))
    with pytest.raises(PatchError, match="too many hunks"):
        parse_patch_reply(f"===PATCH===\n{blocks}===END===\n")


def test_addendum_tells_the_model_to_move_markup() -> None:
    rules = PATCH_SYSTEM_ADDENDUM
    assert (
        "To move content, delete it at its old place and insert it at its new place in the markup "
        "(two blocks). Never use CSS (order, flex, grid, position, float) "
        "to change the reading order."
    ) in rules
    assert "one block that spans both places" not in rules
    assert (
        "For a move, use two blocks: one whose FIND is the moved block and whose REPLACE is empty, "
        "and one whose FIND is only the first line of the destination (e.g. the heading) and whose REPLACE "
        "is the moved block followed by that line."
    ) in rules


EVENTS = "\n".join(
    [
        "<h2>Events</h2>",
        "<p>Open house Friday.</p>",
        "<ul>",
        "<li>Dock walk</li>",
        "<li>Night watch</li>",
        "</ul>",
    ]
)
FACTS_PAGE = f"<h2>Facts</h2>\n<p>North leads.</p>\n{EVENTS}\n<h2>Scanner board</h2>\n<p>Live board.</p>\n"


def test_missing_end_before_next_find_is_two_hunks() -> None:
    """The move reply that omitted <<<END>>> after an empty REPLACE."""
    reply = (
        "===SUMMARY===\nMoved Events above Facts.\n"
        "===PATCH===\n"
        "<<<FIND>>>\n"
        f"{EVENTS}\n"
        "<<<REPLACE>>>\n"
        "\n"
        "<<<FIND>>>\n"
        "<h2>Facts</h2>\n"
        "<<<REPLACE>>>\n"
        f"{EVENTS}\n"
        "<h2>Facts</h2>\n"
        "<<<END>>>\n"
        "===END===\n"
    )
    parsed = parse_patch_reply(reply)
    assert len(parsed.hunks) == 2
    assert parsed.hunks[0] == Hunk(EVENTS, "")
    assert parsed.hunks[1] == Hunk("<h2>Facts</h2>", f"{EVENTS}\n<h2>Facts</h2>")
    updated = apply_patch(FACTS_PAGE, parsed.hunks)
    assert updated.count("<h2>Facts</h2>") == 1
    assert updated.count("<h2>Events</h2>") == 1
    assert updated.index("<h2>Events</h2>") < updated.index("<h2>Facts</h2>")
    assert updated.index("<h2>Facts</h2>") < updated.index("<h2>Scanner board</h2>")
    assert "<<<" not in updated


@pytest.mark.parametrize("marker", ["<<<<<<< SEARCH", "=======", "===NOTE==="])
def test_marker_line_inside_replace_raises(marker: str) -> None:
    reply = f"===PATCH===\n<<<FIND>>>\n<h2>Facts</h2>\n<<<REPLACE>>>\n<h2>Facts</h2>\n{marker}\n<<<END>>>\n===END===\n"
    with pytest.raises(PatchError, match="marker in hunk"):
        parse_patch_reply(reply)


def test_inserted_marker_text_is_rejected() -> None:
    with pytest.raises(PatchError, match="marker in hunk"):
        apply_patch("hello\n", [Hunk("hello", "hello <<<END>>>")])
    kept = apply_patch("see <<<FIND>>> once\n", [Hunk("once", "once")])
    assert kept == "see <<<FIND>>> once\n"


def test_empty_replace_followed_by_end_deletes() -> None:
    reply = "===PATCH===\n<<<FIND>>>\n## Notes\n\nSouth is the backup.\n<<<REPLACE>>>\n<<<END>>>\n===END===\n"
    parsed = parse_patch_reply(reply)
    assert parsed.hunks == [Hunk("## Notes\n\nSouth is the backup.", "")]
    updated = apply_patch(BASE, parsed.hunks)
    assert "South is the backup." not in updated
    assert "## Hours" in updated


def test_css_only_move_is_style_text_on_a_move_request() -> None:
    base = "<style>main{display:block}</style>\n<h2>Facts</h2>\n<h2>Events</h2>\n"
    style = [Hunk("main{display:block}", "main{display:flex}#events{order:1}")]
    assert css_only_move("Move Events above Facts", base, style)
    assert not css_only_move("Recolor the heading", base, style)
    markup = [Hunk("<h2>Events</h2>", "")]
    assert not css_only_move("Move Events above Facts", base, markup)

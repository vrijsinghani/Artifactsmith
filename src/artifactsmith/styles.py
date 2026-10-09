"""HTML visual-design presets. Writing, facts, links, and sanitizer stay unchanged."""

from __future__ import annotations

STYLES: tuple[str, ...] = ("house", "bold", "editorial", "playful", "terminal", "swiss")
ALLOWED_STYLES: frozenset[str] = frozenset(STYLES)
DEFAULT_STYLE = "house"
EMOJI_STYLES: frozenset[str] = frozenset({"bold", "playful", "terminal"})
STYLE_HELP = ", ".join(STYLES)

_QUALITY = (
    "- Works on iPhone width and on desktop. Keep body text readable: strong contrast and a "
    "comfortable line length (about 68ch). Use HTML tables for tabular data. Draw every visual "
    "with HTML and CSS only (system font stacks; no scripts, no webfonts, no remote images as decoration)."
)


class StyleError(ValueError):
    """Unknown style name."""


def parse_style(value: str | None, *, default: str = DEFAULT_STYLE) -> str:
    """Return a canonical style name, or raise StyleError listing the allowed values."""
    raw = default if value is None else str(value).strip()
    if raw == "":
        raw = default
    name = raw.lower()
    if name not in ALLOWED_STYLES:
        raise StyleError(f"style must be one of: {STYLE_HELP}")
    return name


# Each block replaces only the "Visual design:" section of the HTML system prompt.
VISUAL_BLOCKS: dict[str, str] = {
    "bold": f"""Visual design:
- Dark deep-navy-to-violet gradient hero, huge white headline, and pill badges. Below the hero, big
  cards each in its own saturated accent (blue, violet, amber, green) with big numbers. Chunky colored
  tiles and chips. Dark terminal-style code blocks with three window dots.
- Emoji may be used as visual icons, not as decoration in every sentence.
- Spend color on the hero and the accent cards; keep body copy plain and dark on a near-white page.
{_QUALITY}
""",
    "editorial": f"""Visual design:
- A magazine feature. Off-white paper background, a very large serif display headline (Georgia / Times
  New Roman / serif) with tight leading, thin black rules, a drop cap on the opening paragraph, and a
  large italic serif pull quote. Two columns on desktop; one column on a phone.
- Black text with one deep red accent. Small-caps section labels. Big serif numerals. No emoji.
{_QUALITY}
""",
    "playful": f"""Visual design:
- Cream background and chunky rounded cards in candy colors (coral, sunflower, mint, sky, lilac). Thick
  dark outlines with offset hard shadows (neo-brutalist). A big rounded sans headline. Sticker-style
  badges. Fun but readable.
- Emoji may be used as visual icons, not as decoration in every sentence.
{_QUALITY}
""",
    "terminal": f"""Visual design:
- Dark mode. Near-black background, monospace throughout, neon green and cyan accents. Style the
  headline as a shell prompt. Sections are fake terminal windows with title bars and three dots.
  Steps read as a log. Use ✓ checklists. A subtle grid background made with CSS gradients only.
- Emoji may be used as visual icons, not as decoration in every sentence.
{_QUALITY}
""",
    "swiss": f"""Visual design:
- White background, a strict grid, and a huge bold black grotesque headline (Helvetica / Arial / sans-serif).
  Generous whitespace. One signal-orange accent used only on numbers and rules. Oversized numerals.
  Hairline dividers. No shadows, no rounded corners, no emoji.
{_QUALITY}
""",
}


def html_system_prompt(base: str, style: str) -> str:
    """Return the HTML system prompt with ``style``'s visual block swapped in.

    ``house`` is ``base`` unchanged. Other presets replace only the Visual design
    section. ``bold``, ``playful``, and ``terminal`` drop the writing-rule "No emoji."
    so icons are allowed; every other writing and fact rule stays.
    """
    name = parse_style(style)
    if name == DEFAULT_STYLE:
        return base
    text = base
    if name in EMOJI_STYLES:
        text = text.replace(" No emoji.", "")
    start = text.index("Visual design:\n")
    end = text.index("\nOtherwise respond in EXACTLY this format")
    return text[:start] + VISUAL_BLOCKS[name].rstrip() + "\n" + text[end:]

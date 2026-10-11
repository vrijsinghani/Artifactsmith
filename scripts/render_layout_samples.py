"""Write the three sample pages in all six styles for screenshots."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from artifactsmith.layout.render import render_layout  # noqa: E402
from artifactsmith.styles import STYLES  # noqa: E402

SAMPLES = ROOT / "examples" / "layout"
OUT = ROOT / "docs" / "layout-samples"


def main() -> None:
    pages = sorted(SAMPLES.glob("*.md"))
    if len(pages) != 3:
        raise SystemExit(f"expected 3 sample pages in {SAMPLES}")
    for style in STYLES:
        for page in pages:
            html, notes = render_layout(page.read_text(encoding="utf-8"), style=style, title=page.stem)
            if notes:
                raise SystemExit(f"{style}/{page.name}: {notes}")
            dest = OUT / style / f"{page.stem}.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
            print(dest.relative_to(ROOT))


if __name__ == "__main__":
    main()

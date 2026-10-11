"""One stylesheet per style. The model never writes CSS."""

from __future__ import annotations

# Text on its own background. Tests check these pairs for WCAG AA (4.5:1).
CONTRAST_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "house": (
        ("#1c1917", "#ffffff"),
        ("#3f3f46", "#ffffff"),
        ("#ffffff", "#0b5cab"),
        ("#0d6b3d", "#ffffff"),
        ("#b42318", "#ffffff"),
    ),
    "bold": (
        ("#1a1230", "#f7f5f2"),
        ("#ffffff", "#1b1464"),
        ("#ffffff", "#1d4ed8"),
        ("#ffffff", "#047857"),
        ("#ffffff", "#b45309"),
    ),
    "editorial": (("#1a1a1a", "#faf7f2"), ("#3a3a3a", "#faf7f2"), ("#8f1d1d", "#faf7f2")),
    "playful": (("#1f1300", "#fff8ee"), ("#1f1300", "#ffd0c8"), ("#1f1300", "#ffe08a"), ("#1f1300", "#c9f3df")),
    "terminal": (("#d6ffe8", "#0d1110"), ("#8fd9b0", "#0d1110"), ("#3dff9a", "#0d1110")),
    "swiss": (("#111111", "#ffffff"), ("#3f3f46", "#ffffff"), ("#9a3412", "#ffffff")),
}
BADGE_PAIRS: tuple[tuple[str, str], ...] = (
    ("#0d6b3d", "#e7f6ee"),
    ("#7a4e00", "#fff6e5"),
    ("#b42318", "#fdecea"),
    ("#0b5cab", "#e8f1fb"),
    ("#5b21b6", "#f3e8ff"),
    ("#3f3f46", "#f4f4f5"),
)

_PALETTES: dict[str, dict[str, str]] = {
    "house": {
        "bg": "#ffffff",
        "ink": "#1c1917",
        "muted": "#3f3f46",
        "accent": "#0b5cab",
        "surface": "#f3f6fb",
        "line": "#d5deea",
        "pos": "#0d6b3d",
        "neg": "#b42318",
        "font": '"Segoe UI", system-ui, sans-serif',
        "c0": "#0b5cab",
        "c1": "#0d6b3d",
        "c2": "#b45309",
        "c3": "#6d28d9",
        "c4": "#0f766e",
        "c5": "#9a3412",
    },
    "bold": {
        "bg": "#f7f5f2",
        "ink": "#1a1230",
        "muted": "#3b3550",
        "accent": "#5b2dbe",
        "surface": "#efeafc",
        "line": "#ddd6ee",
        "pos": "#047857",
        "neg": "#b42318",
        "font": '"Segoe UI", system-ui, sans-serif',
        "c0": "#1d4ed8",
        "c1": "#6d28d9",
        "c2": "#b45309",
        "c3": "#047857",
        "c4": "#be185d",
        "c5": "#0f766e",
    },
    "editorial": {
        "bg": "#faf7f2",
        "ink": "#1a1a1a",
        "muted": "#3a3a3a",
        "accent": "#8f1d1d",
        "surface": "#f3efe6",
        "line": "#d9d2c5",
        "pos": "#0d6b3d",
        "neg": "#8f1d1d",
        "font": 'Georgia, "Iowan Old Style", "Palatino Linotype", Palatino, serif',
        "c0": "#1a1a1a",
        "c1": "#8f1d1d",
        "c2": "#3a3a3a",
        "c3": "#6b5b4b",
        "c4": "#1a1a1a",
        "c5": "#8f1d1d",
    },
    "playful": {
        "bg": "#fff8ee",
        "ink": "#1f1300",
        "muted": "#3f2a12",
        "accent": "#e85d4c",
        "surface": "#fff1d6",
        "line": "#1f1300",
        "pos": "#0d6b3d",
        "neg": "#b42318",
        "font": '"Avenir Next", "Segoe UI", system-ui, sans-serif',
        "c0": "#e85d4c",
        "c1": "#e0a100",
        "c2": "#0d6b3d",
        "c3": "#0b5cab",
        "c4": "#6d28d9",
        "c5": "#b45309",
    },
    "terminal": {
        "bg": "#0d1110",
        "ink": "#d6ffe8",
        "muted": "#8fd9b0",
        "accent": "#3dff9a",
        "surface": "#14211b",
        "line": "#1f3d30",
        "pos": "#3dff9a",
        "neg": "#ff6b6b",
        "font": 'ui-monospace, "Cascadia Code", "SFMono-Regular", Menlo, Consolas, monospace',
        "c0": "#3dff9a",
        "c1": "#7ee7ff",
        "c2": "#d6ff4a",
        "c3": "#ffb020",
        "c4": "#ff6b6b",
        "c5": "#c4b5fd",
    },
    "swiss": {
        "bg": "#ffffff",
        "ink": "#111111",
        "muted": "#3f3f46",
        "accent": "#9a3412",
        "surface": "#fafafa",
        "line": "#e4e4e7",
        "pos": "#0d6b3d",
        "neg": "#b42318",
        "font": "Helvetica, Arial, sans-serif",
        "c0": "#9a3412",
        "c1": "#52525b",
        "c2": "#a1a1aa",
        "c3": "#111111",
        "c4": "#9a3412",
        "c5": "#3f3f46",
    },
}

_BASE = """
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:16px;line-height:1.5}
.asl-title,main{max-width:72rem;margin:0 auto;padding-left:1.25rem;padding-right:1.25rem;box-sizing:border-box}
.asl-title{padding-top:1.25rem;margin-bottom:1.2rem}
main{padding-top:0;padding-bottom:3rem}
p{max-width:68ch}
h1,h2,h3,h4{line-height:1.15;margin:0 0 .5rem}
h1{font-size:2.1rem}
h2{font-size:1.4rem;margin-top:1.4rem}
h3{font-size:1.15rem}
a{color:var(--accent)}
.asl-title .dek{margin:.35rem 0 0;color:var(--muted);max-width:62ch}
.asl-hero{margin:0 0 1.25rem;padding:1rem 1.15rem;background:var(--surface);border-left:4px solid var(--accent);border-radius:10px}
.asl-hero p{margin:0;font-size:1.15rem;line-height:1.45;max-width:62ch}
.asl-callout.verdict{font-size:1.08rem}
.asl-callout.verdict p{max-width:62ch}
.asl-section{margin:1.4rem 0}
.asl-grid{display:grid;gap:1rem;margin:1rem 0}
.asl-stat{padding:1rem 1.05rem}
.asl-stat .label{margin:0;font-size:.85rem;color:var(--muted)}
.asl-stat .value{margin:.15rem 0 0;font-size:1.8rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.asl-stat .note{margin:.3rem 0 0;font-size:.85rem;color:var(--muted)}
.asl-stat.emph .value{font-size:2.3rem}
.asl-stat.size-sm .value{font-size:1.3rem}
.asl-stat.size-lg .value{font-size:2.2rem}
.asl-stat.size-xl .value{font-size:3rem}
.asl-callout{margin:1rem 0;padding:.8rem 1rem}
.asl-callout-label{margin:0 0 .3rem;font-size:.78rem;font-weight:700}
.asl-callout p{margin:.35rem 0}
.asl-table-wrap{overflow-x:auto;max-width:100%;margin:1rem 0}
table{border-collapse:collapse;width:100%;margin:0;font-size:1rem;line-height:1.45}
th,td{padding:.65rem .8rem;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{font-weight:600}
caption{caption-side:top;text-align:left;font-weight:600;padding:0 0 .35rem}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--pos)}
.neg{color:var(--neg)}
td .sub,th .sub{display:block;font-size:.82em;color:var(--muted);font-weight:400}
tfoot td,tfoot th{font-weight:700;border-top:2px solid var(--ink)}
.asl-badge{display:inline-block;padding:.05rem .45rem;border-radius:999px;font-size:.82rem;font-weight:600;background:var(--surface);color:var(--ink)}
.tone-green{background:#e7f6ee;color:#0d6b3d}
.tone-amber{background:#fff6e5;color:#7a4e00}
.tone-red{background:#fdecea;color:#b42318}
.tone-blue{background:#e8f1fb;color:#0b5cab}
.tone-violet{background:#f3e8ff;color:#5b21b6}
.tone-gray{background:#f4f4f5;color:#3f3f46}
.tone-accent{background:var(--surface);color:var(--accent)}
.asl-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem 1.25rem;margin:1rem 0}
.asl-kv .kv dt{margin:0;font-size:.78rem;color:var(--muted)}
.asl-kv .kv dd{margin:.12rem 0 0;font-weight:600}
.asl-timeline,.asl-steps{margin:1rem 0;padding:0}
.asl-timeline{list-style:none;border-left:3px solid var(--accent)}
.asl-timeline li{position:relative;padding:0 0 .9rem 1rem}
.asl-timeline .when{margin:0;font-weight:700}
.asl-timeline .what{margin:.1rem 0 0}
.asl-steps{padding-left:1.4rem}
.asl-steps li{margin:.35rem 0}
.asl-quote{margin:1rem 0;font-size:1.25rem}
.asl-code{padding:.85rem 1rem;overflow-x:auto;max-width:100%}
.asl-code .lang{display:block;font-size:.75rem;color:var(--muted);margin-bottom:.3rem}
.asl-code code{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace}
.asl-chart svg{width:100%;height:auto;display:block}
.asl-chart .bar{fill:var(--c0)}
.asl-chart .line{fill:none;stroke:var(--c0);stroke-width:2px}
.asl-chart .dot{fill:var(--c0)}
.asl-chart .axis{stroke:var(--line);stroke-width:1px}
.asl-chart .pie0{fill:var(--c0)}.asl-chart .pie1{fill:var(--c1)}.asl-chart .pie2{fill:var(--c2)}
.asl-chart .pie3{fill:var(--c3)}.asl-chart .pie4{fill:var(--c4)}.asl-chart .pie5{fill:var(--c5)}
.asl-chart text{fill:var(--ink);font-family:var(--font)}
.asl-list{margin:.4rem 0 1rem;padding-left:1.2rem}
.cols-2{columns:2;column-gap:1.5rem}
.asl-rule{border:0;border-top:1px solid var(--line);margin:1.4rem 0}
.g-1{grid-template-columns:1fr}
.g-2{grid-template-columns:1fr 1fr}
.g-3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g-4{grid-template-columns:repeat(4,minmax(0,1fr))}
.g-2-1{grid-template-columns:2fr 1fr}
.g-1-2{grid-template-columns:1fr 2fr}
.g-3-2{grid-template-columns:3fr 2fr}
.g-3-1{grid-template-columns:3fr 1fr}
.sp-2{grid-column:span 2}.sp-3{grid-column:span 3}.sp-4{grid-column:span 4}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
summary{min-height:44px;display:flex;align-items:center;cursor:pointer}
"""

_TAIL = """
@media (max-width:40rem){
  .asl-grid[class*="g-"]{grid-template-columns:1fr}
  .asl-grid.stats{grid-template-columns:1fr 1fr}
  .asl-stat.long{grid-column:1 / -1}
  [class*="sp-"]{grid-column:auto}
  .cols-2{columns:1}
  .asl-title.variant-split{display:block}
  .asl-table-wrap{overflow:visible}
  table.stack thead{display:none}
  table.stack tr{display:block;margin:0 0 .75rem;border:1px solid var(--line);padding:.45rem .65rem}
  table.stack td,table.stack th{display:block;border:0;text-align:left;padding:.2rem 0}
  table.stack td::before,table.stack th::before{content:attr(data-label);display:block;font-size:.75rem;color:var(--muted);font-weight:600}
  table.stack tfoot{display:block}
  table.stack tr.total-line{display:flex;flex-wrap:wrap;align-items:baseline;gap:.2rem .55rem;border:0;margin:0;padding:.35rem 0}
  table.stack tr.total-line td,table.stack tr.total-line th{display:inline;width:auto;border:0;padding:0;font-weight:700}
  table.stack tr.total-line td::before,table.stack tr.total-line th::before{content:none;display:inline}
  table.scroll{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
  pre,.asl-code{overflow-x:auto}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none;transition:none}
}
@media print{
  body,.asl-title,.asl-stat,.asl-callout,.asl-quote,.asl-code{background:#fff;color:#000;box-shadow:none}
  a{text-decoration:underline}
}
"""

_EXTRAS: dict[str, str] = {
    "house": """
.asl-title h1{border-bottom:4px solid var(--accent);padding-bottom:.25rem}
.asl-title.variant-band{background:var(--surface);margin:0 auto 1.25rem;padding:1.5rem 1.25rem}
.asl-section.variant-card{border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem}
.asl-section h2{border-bottom:2px solid var(--accent);padding-bottom:.15rem}
.asl-stat{background:var(--surface);border:1px solid var(--line);border-radius:10px}
.asl-stat .value{color:var(--accent)}
.asl-stat.tone-green .value{color:#0d6b3d}
.asl-stat.tone-red .value{color:#b42318}
.asl-callout{background:var(--surface);border-left:4px solid var(--accent);border-radius:8px}
.asl-callout.warn{border-left-color:#b45309}
.asl-callout.success{border-left-color:#0d6b3d}
.asl-callout.danger{border-left-color:#b42318}
.asl-table th{background:var(--surface)}
.asl-table.variant-striped tbody tr:nth-child(even){background:var(--surface)}
.asl-code{background:#f4f4f5;border-radius:8px}
.asl-quote{border-left:4px solid var(--accent);padding:.2rem 0 .2rem 1rem}
""",
    "bold": """
.asl-title{background-image:linear-gradient(135deg,#1b1464,#5b2dbe);color:#fff;margin:0 auto 1.25rem;padding:2rem 1.25rem;border-radius:0 0 16px 16px}
.asl-hero{background:#1d4ed8;color:#fff;border-left:0;border-radius:12px}
.asl-hero p,.asl-hero a{color:#fff}
.asl-title h1{font-size:2.7rem;color:#fff}
.asl-title .dek,.asl-title a{color:#e0e7ff}
.asl-badge{border-radius:999px;background:#fff;color:#1a1230}
.asl-stat{background:#1d4ed8;color:#fff;border-radius:14px}
.asl-stat .label,.asl-stat .note{color:#f8fafc}
.asl-stat .value{color:#fff}
.asl-stat.tone-green{background:#047857}
.asl-stat.tone-amber{background:#b45309}
.asl-stat.tone-violet,.asl-stat.tone-accent{background:#6d28d9}
.asl-stat.tone-red{background:#b42318}
.asl-callout{color:#fff;border-radius:12px;padding:1rem}
.asl-callout.note{background:#1d4ed8}
.asl-callout.warn{background:#b45309}
.asl-callout.success{background:#047857}
.asl-callout.danger{background:#b42318}
.asl-callout-label{color:#fff}
.asl-table th{background:#1e1b4b;color:#fff}
.asl-code{background:#1e1b4b;color:#e0e7ff;border-radius:12px}
.asl-code .lang{color:#c4b5fd}
.asl-quote{background:#5b2dbe;color:#fff;padding:1rem 1.2rem;border-radius:12px}
.asl-section.variant-card{background:#fff;border-radius:14px;padding:1rem;box-shadow:0 8px 24px rgb(27 20 100 / 8%)}
""",
    "editorial": """
.asl-title h1{font-size:3rem;font-weight:500;letter-spacing:-0.02em}
.asl-hero{background:transparent;border:0;border-top:1px solid var(--accent);border-bottom:1px solid var(--accent);border-radius:0;padding:.8rem 0;font-style:italic}
.asl-title.variant-split{display:grid;grid-template-columns:1.5fr .8fr;gap:1.5rem;align-items:end}
.asl-section h2{font-variant:small-caps;letter-spacing:.06em;font-size:1rem;border-top:1px solid var(--ink);padding-top:.45rem}
.asl-stat{padding:0}
.asl-stat .value{font-size:2.5rem;font-weight:500}
.asl-callout{font-style:italic;border-top:1px solid var(--accent);border-bottom:1px solid var(--accent);background:transparent}
.asl-quote{font-size:1.6rem;font-style:italic;margin-left:0}
.asl-table th{background:transparent;border-bottom:1px solid var(--ink)}
.asl-code{background:transparent;border:1px solid var(--line)}
""",
    "playful": """
a{color:#7f1d1d}
.asl-code a{color:#ffe08a}
.asl-title h1{font-size:2.5rem;background:#ffe08a;display:inline-block;padding:.15rem .6rem;border:3px solid #1f1300;border-radius:14px;box-shadow:4px 4px 0 #1f1300}
.asl-hero{background:#fff;border:3px solid #1f1300;border-left:3px solid #1f1300;border-radius:14px;box-shadow:4px 4px 0 #1f1300}
.asl-stat{background:#ffd0c8;border:3px solid #1f1300;border-radius:16px;box-shadow:4px 4px 0 #1f1300}
.asl-stat.tone-green{background:#c9f3df}
.asl-stat.tone-blue{background:#cfe4ff}
.asl-stat.tone-amber{background:#ffe08a}
.asl-stat.tone-violet{background:#ead7ff}
.asl-stat .label,.asl-stat .note,.asl-stat .value{color:#1f1300}
.asl-callout{border:3px solid #1f1300;border-radius:14px;box-shadow:3px 3px 0 #1f1300;background:#fff}
.asl-badge{border:2px solid #1f1300}
.asl-table{border:3px solid #1f1300;border-radius:12px}
.asl-table th{background:#ffe08a}
.asl-code{background:#1f1300;color:#fff8ee;border-radius:14px}
.asl-code .lang{color:#ffe08a}
.asl-quote{background:#fff;border:3px solid #1f1300;border-radius:16px;padding:.8rem 1rem;box-shadow:4px 4px 0 #1f1300}
.asl-section.variant-card{border:3px solid #1f1300;border-radius:16px;padding:.8rem 1rem;box-shadow:4px 4px 0 #1f1300}
""",
    "terminal": """
.asl-title h1{font-size:1.8rem}
.asl-hero{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:6px}
.asl-title h1::before{content:"$ "}
.asl-section{border:1px solid var(--line);border-radius:8px;margin:1rem 0;overflow:hidden}
.asl-section h2{background:#12211a;margin:0;padding:.45rem .75rem;font-size:1rem}
.asl-section h2::before{content:"● ● ●  ";color:var(--muted);font-weight:400}
.asl-section > *:not(h2){margin-left:.75rem;margin-right:.75rem}
.asl-stat{background:var(--surface);border:1px solid var(--line);border-radius:6px}
.asl-stat .value{color:var(--accent)}
.asl-callout{border:1px solid var(--line);background:var(--surface);font-family:inherit}
.asl-callout-label::before{content:"[";}
.asl-callout-label::after{content:"]"}
.asl-table th,.asl-table td{border-color:var(--line);font-variant-numeric:tabular-nums}
.asl-code{background:#070b09;border:1px solid var(--line);color:var(--ink)}
.asl-quote{border-left:3px solid var(--accent);padding-left:.8rem}
.asl-badge{border-radius:4px;background:#14211b}
.tone-green{background:#14211b;color:#3dff9a}
.tone-amber{background:#14211b;color:#ffb020}
.tone-red{background:#14211b;color:#ff8d8d}
.tone-gray,.tone-blue,.tone-violet,.tone-accent{background:#14211b;color:var(--ink)}
""",
    "swiss": """
.asl-title h1{font-size:3.3rem;font-weight:800;letter-spacing:-0.035em;line-height:.95}
.asl-hero{background:transparent;border:0;border-top:3px solid var(--accent);border-radius:0;padding:.6rem 0}
.asl-stat{padding:.2rem 0}
.asl-stat .value{color:var(--accent);font-size:2.8rem;font-weight:800}
.asl-section{border-top:1px solid #111;padding-top:.55rem}
.asl-callout{border-top:3px solid var(--accent);background:transparent;padding:.55rem 0}
.asl-table th,.asl-table td{background:transparent;border-bottom:1px solid #ccc}
.asl-quote{font-size:2rem;font-weight:700;margin:.5rem 0}
.asl-code{border:1px solid #111;background:#fff}
.asl-badge{border-radius:0}
""",
}


def _root(palette: dict[str, str]) -> str:
    body = "".join(f"--{key}:{value};" for key, value in palette.items())
    return f":root{{{body}}}"


def stylesheet(style: str, extra: str = "") -> str:
    """CSS for ``style``. ``extra`` is server-generated grid rules, still not model CSS."""
    palette = _PALETTES.get(style) or _PALETTES["house"]
    extra_block = _EXTRAS.get(style, "")
    return _root(palette) + _BASE + extra_block + extra + _TAIL


def grid_class(spec: str) -> str:
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if not parts or any(not part.isdigit() or int(part) < 1 or int(part) > 6 for part in parts):
        return "g-1"
    if len(parts) == 1:
        count = parts[0]
        return f"g-{count}" if count in {"1", "2", "3", "4"} else "g-1"
    return "g-" + "-".join(parts)


def grid_rule(spec: str) -> str:
    """A column rule for a spec that is not already in the base sheet."""
    cls = grid_class(spec)
    if cls in {"g-1", "g-2", "g-3", "g-4", "g-2-1", "g-1-2", "g-3-2", "g-3-1"}:
        return ""
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if len(parts) == 1 and parts[0].isdigit():
        columns = f"repeat({int(parts[0])},minmax(0,1fr))"
    else:
        columns = " ".join(f"{int(part)}fr" for part in parts if part.isdigit())
    if not columns:
        return ""
    return f".asl-grid.{cls}{{grid-template-columns:{columns}}}"

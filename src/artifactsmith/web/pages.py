"""Server-rendered My pages markup. House palette, no external assets."""

from __future__ import annotations

import secrets
from html import escape
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .catalog import STATUSES
from .clock import format_when

_CSS = """
:root{--bg:#ffffff;--ink:#1c1917;--muted:#3f3f46;--accent:#0b5cab;--surface:#f3f6fb;--line:#d5deea;--pos:#0d6b3d;--neg:#b42318}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Segoe UI",system-ui,sans-serif;font-size:16px;line-height:1.5}
main{max-width:40rem;margin:0 auto;padding:1rem}
h1{font-size:1.4rem;line-height:1.2;margin:0}
h2{font-size:1.05rem;margin:0 0 .35rem}
a{color:var(--accent)}
p{margin:.4rem 0}
header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:.75rem}
header form{margin:0}
header button{width:auto;background:transparent;color:var(--accent);border:1px solid var(--line)}
.muted{color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:.9rem 1rem;margin:0 0 .75rem}
.meta{display:grid;grid-template-columns:6.5rem 1fr;gap:.15rem .75rem;margin:.35rem 0 0}
.meta dt{color:var(--muted);margin:0}
.meta dd{margin:0;overflow-wrap:anywhere}
label{display:block;font-weight:600;margin:.7rem 0 .25rem}
input,select,button{font:inherit;width:100%;padding:.55rem .7rem;border-radius:8px;border:1px solid var(--line);background:#fff;color:var(--ink)}
button.primary{background:var(--accent);color:#fff;border:0;font-weight:600;margin-top:.8rem}
.error{color:var(--neg);font-weight:600}
.back{display:inline-block;margin-bottom:.6rem}
.pager{display:flex;gap:.5rem;margin:.5rem 0 1rem}
.pager a{flex:1;text-align:center;padding:.55rem .7rem;border:1px solid var(--line);border-radius:8px;text-decoration:none}
"""


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


def security_headers(nonce: str) -> dict[str, str]:
    csp = (
        "default-src 'none'; script-src 'none'; "
        f"style-src 'nonce-{nonce}'; img-src 'none'; font-src 'none'; connect-src 'none'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )
    return {
        "Content-Security-Policy": csp,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), microphone=()",
    }


def esc(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _shell(title: str, body: str, nonce: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        "<meta name=robots content=noindex>"
        f"<title>{esc(title)}</title>"
        f'<style nonce="{esc(nonce)}">{_CSS}</style></head><body><main>{body}</main></body></html>'
    )


def _share(url: str) -> str:
    if not url:
        return "not shared"
    return f'<a href="{esc(url)}">{esc(url)}</a>'


def _meta(pairs: list[tuple[str, str]]) -> str:
    bits = []
    for label, value in pairs:
        bits.append(f"<dt>{esc(label)}</dt><dd>{value}</dd>")
    return "<dl class=meta>" + "".join(bits) + "</dl>"


def render_login(nonce: str, csrf: str, message: str = "") -> str:
    error = f"<p class=error>{esc(message)}</p>" if message else ""
    body = (
        "<h1>My pages</h1>"
        "<p>Paste an ArtifactSmith token for this workspace. The token is not put in the address bar.</p>"
        f"{error}"
        '<form method=post action="/app/login">'
        f'<input type=hidden name=csrf value="{esc(csrf)}">'
        "<label for=token>Token</label>"
        "<input id=token name=token type=password autocomplete=current-password spellcheck=false "
        "required maxlength=512>"
        "<button class=primary type=submit>Sign in</button></form>"
    )
    return _shell("My pages", body, nonce)


def render_message(nonce: str, title: str, message: str) -> str:
    body = f"<h1>{esc(title)}</h1><p>{esc(message)}</p>"
    return _shell(title, body, nonce)


def _status_options(current: str) -> str:
    options = ['<option value="">Any status</option>']
    for name in STATUSES:
        selected = " selected" if name == current else ""
        options.append(f'<option value="{esc(name)}"{selected}>{esc(name)}</option>')
    return "".join(options)


def _list_href(query: str, status: str, page: int) -> str:
    params: list[tuple[str, str]] = []
    if query:
        params.append(("q", query))
    if status:
        params.append(("status", status))
    if page > 1:
        params.append(("page", str(page)))
    if not params:
        return "/app/"
    return "/app/?" + urlencode(params)


def _zone_form(action: str, zone: ZoneInfo, hidden: list[tuple[str, str]]) -> str:
    fields = "".join(f'<input type=hidden name={esc(name)} value="{esc(value)}">' for name, value in hidden if value)
    return (
        f'<form method=get action="{esc(action)}">'
        f"{fields}"
        "<label for=tz>Time zone</label>"
        f'<input id=tz name=tz value="{esc(zone.key)}" maxlength=64 autocapitalize=none spellcheck=false>'
        "<button type=submit>Set zone</button></form>"
        f"<p class=muted>Times shown in {esc(zone.key)}</p>"
    )


def _pager(query: str, status: str, page: int, has_next: bool) -> str:
    links = []
    if page > 1:
        links.append(f'<a href="{esc(_list_href(query, status, page - 1))}">Previous</a>')
    if has_next:
        links.append(f'<a href="{esc(_list_href(query, status, page + 1))}">Next</a>')
    if not links:
        return ""
    return "<nav class=pager>" + "".join(links) + "</nav>"


def _header(workspace: str, csrf: str) -> str:
    return (
        "<header><h1>My pages</h1>"
        '<form method=post action="/app/logout">'
        f'<input type=hidden name=csrf value="{esc(csrf)}">'
        "<button type=submit>Sign out</button></form></header>"
        f"<p class=muted>Workspace {esc(workspace)}. Read-only.</p>"
    )


def render_list(
    nonce: str,
    workspace: str,
    rows: list[dict[str, Any]],
    query: str,
    status: str,
    csrf: str,
    page: int,
    has_next: bool,
    zone: ZoneInfo,
) -> str:
    cards = []
    for row in rows:
        version = "—" if row["latest_version"] is None else str(row["latest_version"])
        href = f"/app/a/{esc(row['artifact_id'])}"
        cards.append(
            "<article class=card>"
            f'<h2><a href="{href}">{esc(row["title"])}</a></h2>'
            + _meta(
                [
                    ("Format", esc(row["format"])),
                    ("Style", esc(row["style"])),
                    ("Version", esc(version)),
                    ("Status", esc(row["status"] or "—")),
                    ("Updated", esc(format_when(row["updated_at"], zone) or "—")),
                    ("Share", _share(str(row["share_url"]))),
                ]
            )
            + "</article>"
        )
    if cards:
        listing = "".join(cards)
    elif query.strip() or status:
        listing = "<p class=muted>No pages match.</p>"
    else:
        listing = "<p class=muted>No pages in this workspace.</p>"
    body = (
        _header(workspace, csrf)
        + '<form method=get action="/app/">'
        + "<label for=q>Search title or slug</label>"
        + f'<input id=q name=q value="{esc(query)}" maxlength=200>'
        + "<label for=status>Status</label>"
        + f"<select id=status name=status>{_status_options(status)}</select>"
        + "<button class=primary type=submit>Filter</button></form>"
        + _zone_form("/app/", zone, [("q", query), ("status", status), ("page", str(page) if page > 1 else "")])
        + listing
        + _pager(query, status, page, has_next)
    )
    return _shell("My pages", body, nonce)


def render_detail(nonce: str, workspace: str, page: dict[str, Any], csrf: str, zone: ZoneInfo) -> str:
    blocks = [
        '<a class=back href="/app/">All pages</a>',
        _header(workspace, csrf),
        f"<h2>{esc(page['title'])}</h2>",
        _zone_form(f"/app/a/{page['artifact_id']}", zone, []),
        _meta(
            [
                ("Slug", esc(page["slug"])),
                ("Format", esc(page["format"])),
                ("Updated", esc(format_when(page["updated_at"], zone) or "—")),
                ("Share", _share(str(page["share_url"]))),
            ]
        ),
    ]
    versions = page["versions"]
    if not versions:
        blocks.append("<p class=muted>No versions yet.</p>")
    for ver in versions:
        blocks.append(
            "<article class=card>"
            f"<h2>Version {esc(ver['version'])}</h2>"
            + _meta(
                [
                    ("Status", esc(ver["status"])),
                    ("Format", esc(ver["format"])),
                    ("Style", esc(ver["style"])),
                    ("Created", esc(format_when(ver["created_at"], zone) or "—")),
                    ("Job", esc(ver["job_status"] or "—")),
                    ("Duration", esc(ver["duration"] or "—")),
                    ("Check", esc(ver["check"] or "—")),
                    ("Note", esc(ver["note"] or "—")),
                ]
            )
            + "</article>"
        )
    return _shell(str(page["title"]), "".join(blocks), nonce)


def render_not_found(nonce: str) -> str:
    return render_message(nonce, "Not found", "That page is not in this workspace.")

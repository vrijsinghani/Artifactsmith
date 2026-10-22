"""Quiet credit line on the HTML share view. Stored bytes and exports stay unchanged."""

from __future__ import annotations

import re
from html import escape

from .config import CFG
from .trial import REPO

CREDIT_TEXT = "Made with ArtifactSmith"
CREDIT_ID = "am-share-credit"

# Inline !important beats a page rule that hides footer, a, or this id.
# The line stays in normal flow so it cannot cover the page or widen it.
_FOOTER_STYLE = (
    "all:revert!important;display:block!important;visibility:visible!important;opacity:1!important;"
    "position:static!important;float:none!important;clear:both!important;inset:auto!important;"
    "transform:none!important;filter:none!important;clip:auto!important;clip-path:none!important;"
    "box-sizing:border-box!important;width:auto!important;max-width:100%!important;min-width:0!important;"
    "height:auto!important;max-height:none!important;overflow:visible!important;"
    "margin:1.25rem 0 0!important;padding:0.75rem 1rem 1rem!important;"
    "font:400 12px/1.4 system-ui,sans-serif!important;color:#6b7280!important;background:transparent!important;"
    "text-align:left!important;text-indent:0!important;letter-spacing:normal!important;"
    "white-space:normal!important;overflow-wrap:anywhere!important;word-break:break-word!important;"
    "pointer-events:auto!important;"
)
_LINK_STYLE = (
    "color:#6b7280!important;font:400 12px/1.4 system-ui,sans-serif!important;"
    "text-decoration:underline!important;visibility:visible!important;opacity:1!important;"
    "display:inline!important;white-space:normal!important;overflow-wrap:anywhere!important;"
    "pointer-events:auto!important;"
)
_BODY_END = re.compile(r"</body>", re.I)


def stamp_share_credit(html: bytes) -> bytes:
    """Append the credit footer when the setting is on. Otherwise return the same bytes.

    Called only for an HTML share view, after the stored file has been read.
    """
    if not CFG.share_credit:
        return html
    text = html.decode("utf-8", errors="replace")
    link = (
        f'<a href="{escape(REPO, quote=True)}" target="_blank" rel="noopener" style="{_LINK_STYLE}">'
        f"{escape(CREDIT_TEXT)}</a>"
    )
    footer = f'<footer id="{CREDIT_ID}" style="{_FOOTER_STYLE}">{link}</footer>'
    if _BODY_END.search(text):
        text = _BODY_END.sub(footer + "</body>", text, count=1)
    else:
        text += footer
    return text.encode("utf-8")

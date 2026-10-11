"""Test-only fake OpenAI Chat Completions endpoint.

Used by compose.test.yaml and unit tests. Not part of the shipped package.

Behaviour:
- Echoes the supplied request and <source_material> text from the prompt into a tiny,
  self-contained HTML page, so a fact the user supplied (e.g. a store code) reaches
  the rendered page.
- Emits the needs-input marker whenever the prompt contains the sentinel FACTS_MISSING,
  so a request whose fact is absent from the source material can never produce a done page.
- When the system prompt asks for a find/replace patch and the user message has a base file,
  returns a patch of the echoed fact span instead of a new full file.
It performs no network calls and never invents a page. The whole reply is plain text
the server parses; nothing here is executed by the server.
"""

from __future__ import annotations

import html
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

MISSING_SENTINEL = "FACTS" + "_MISSING"  # built by concatenation so this file carries no bare sentinel token

REQUEST_RE = re.compile(r"<<<REQUEST\n(.*?)\nREQUEST>>>", re.S)
SOURCE_RE = re.compile(r"<source_material>\n(.*?)\n</source_material>", re.S)
TITLE_RE = re.compile(r"^Title:\s*(.+?)$", re.M)
BASE_RE = re.compile(r"<base_file>\n(.*)\n</base_file>", re.S)
PRE_RE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.S | re.I)
MD_DETAILS_RE = re.compile(r"## Details\n\n(.*?)\n\n\| field \|", re.S)


def _user_text(messages: list[dict]) -> str:
    """Only the user message carries the request + source material. The system prompt is instructions that
    contain example markers, so it is deliberately ignored when extracting facts."""
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")


def _extract(text: str) -> tuple[str, str, str]:
    title_m = TITLE_RE.search(text)
    title = title_m.group(1).strip() if title_m else "Artifact"
    req = REQUEST_RE.search(text)
    src = SOURCE_RE.search(text)
    request_text = req.group(1).strip() if req else ""
    source_text = src.group(1).strip() if src else ""
    if "(none supplied" in source_text:
        source_text = ""
    return title, request_text, source_text


def _system_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")


def _patch_message(old: str, new: str, summary: str) -> str:
    return (
        "===ASSUMPTIONS===\n"
        "- none\n"
        "===SUMMARY===\n"
        f"{summary}\n"
        "===PATCH===\n"
        "<<<FIND>>>\n"
        f"{old}\n"
        "<<<REPLACE>>>\n"
        f"{new}\n"
        "<<<END>>>\n"
        "===END===\n"
    )


def _edit_patch(user_text: str) -> str | None:
    """When the builder asks for a patch, replace the echoed fact span inside the base file."""
    base_m = BASE_RE.search(user_text)
    if not base_m:
        return None
    base = base_m.group(1)
    _title, request_text, source_text = _extract(user_text)
    bits = [part for part in (source_text, request_text) if part]
    facts = "\n".join(bits) if bits else "no facts supplied"
    pre = PRE_RE.search(base)
    if pre and pre.group(1).strip() and base.count(pre.group(1)) == 1:
        return _patch_message(pre.group(1), html.escape(facts), "Updated the details to the supplied facts.")
    details = MD_DETAILS_RE.search(base)
    if details and details.group(1).strip() and base.count(details.group(1)) == 1:
        return _patch_message(details.group(1), facts, "Updated the details to the supplied facts.")
    return None


def _reply(messages: list[dict]) -> str:
    text = _user_text(messages)
    if MISSING_SENTINEL in text:
        return (
            "===NEEDS_INPUT===\n"
            "A fact the request depends on is not present in the supplied source material. "
            "Provide it as source_content and try again.\n===END===\n"
        )
    # Patch calls carry the find/replace rules in the system prompt. A full rewrite does not.
    if "<<<FIND>>>" in _system_text(messages):
        patched = _edit_patch(text)
        if patched:
            return patched
    title, request_text, source_text = _extract(text)
    body_bits = []
    if source_text:
        body_bits.append(source_text)
    if request_text:
        body_bits.append(request_text)
    facts = "\n".join(body_bits) if body_bits else "no facts supplied"
    wants_markdown = "===FILE: content.md===" in _system_text(messages)
    if wants_markdown:
        md = f"# {title}\n\n## Details\n\n{facts}\n"
        if "|" not in facts:
            md += "\n| field | value |\n| --- | --- |\n| title | " + title + " |\n"
        return (
            "===ASSUMPTIONS===\n"
            "- Layout chosen by the builder; wording follows the supplied material.\n"
            "===SUMMARY===\n"
            "Built one Markdown document echoing the supplied material.\n"
            "===FILE: content.md===\n" + md + "===END===\n"
        )
    fact_block = html.escape(facts)
    page = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:48rem;line-height:1.5}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>"
        '<section class="facts"><h2>Details</h2>'
        f'<pre style="white-space:pre-wrap">{fact_block}</pre></section>'
        "</body></html>"
    )
    return (
        "===ASSUMPTIONS===\n"
        "- Layout chosen by the builder; wording follows the supplied material.\n"
        "===SUMMARY===\n"
        "Built one self-contained page echoing the supplied material.\n"
        "===FILE: index.html===\n" + page + "\n===END===\n"
    )


async def chat_completions(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)
    payload = body if isinstance(body, dict) else {}
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    content = _reply(messages)
    model = payload.get("model", "test-echo")
    return JSONResponse(
        {
            "id": "fake-chat",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        }
    )


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "fake-chat-llm"})


app = Starlette(
    routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/healthz", healthz),
    ]
)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


if __name__ == "__main__":
    run()

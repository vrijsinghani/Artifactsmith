"""Dev/test mock of an OpenAI Chat Completions endpoint (used only under the compose 'test' profile).

Behaviour:
- Echoes the supplied request and <source_material> text from the prompt into a tiny, self-contained HTML page,
  so a fact the user supplied (e.g. a store code) provably reaches the rendered page.
- Emits the needs-input marker whenever the prompt contains the sentinel FACTS_MISSING, so a request whose fact is
  absent from the source material can never produce a done page.
It performs no network calls and never invents a page. The whole reply is plain text the server parses; nothing here
is executed by the server.
"""
from __future__ import annotations

import html
import json
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

MISSING_SENTINEL = "FACTS" + "_MISSING"  # built by concatenation so this file carries no bare sentinel token

REQUEST_RE = re.compile(r"<<<REQUEST\n(.*?)\nREQUEST>>>", re.S)
SOURCE_RE = re.compile(r"<source_material>\n(.*?)\n</source_material>", re.S)
TITLE_RE = re.compile(r"^Title:\s*(.+?)$", re.M)


def _user_text(messages: list[dict]) -> str:
    """Only the user message carries the request + source material. The system prompt is instructions that
    contain example markers, so it is deliberately ignored when extracting facts."""
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")


def _extract(text: str) -> tuple[str, str, str]:
    title = (TITLE_RE.search(text) or [None, "Artifact"])
    title = title.group(1).strip() if hasattr(title, "group") else "Artifact"
    req = REQUEST_RE.search(text)
    src = SOURCE_RE.search(text)
    request_text = req.group(1).strip() if req else ""
    source_text = src.group(1).strip() if src else ""
    if "(none supplied" in source_text:
        source_text = ""
    return title, request_text, source_text


def _reply(messages: list[dict]) -> str:
    text = _user_text(messages)
    if MISSING_SENTINEL in text:
        return ("===NEEDS_INPUT===\n"
                "A fact the request depends on is not present in the supplied source material. "
                "Provide it as source_content and try again.\n===END===\n")
    title, request_text, source_text = _extract(text)
    body_bits = []
    if source_text:
        body_bits.append(source_text)
    if request_text:
        body_bits.append(request_text)
    fact_block = html.escape("\n".join(body_bits)) if body_bits else "no facts supplied"
    page = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:48rem;line-height:1.5}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>"
        "<section class=\"facts\"><h2>Details</h2>"
        f"<pre style=\"white-space:pre-wrap\">{fact_block}</pre></section>"
        "</body></html>"
    )
    return ("===ASSUMPTIONS===\n"
            "- Layout chosen by the builder; wording follows the supplied material.\n"
            "===SUMMARY===\n"
            "Built one self-contained page echoing the supplied material.\n"
            "===FILE: index.html===\n"
            + page + "\n===END===\n")


async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)
    messages = body.get("messages", []) if isinstance(body, dict) else []
    content = _reply(messages)
    return JSONResponse({
        "id": "mock", "object": "chat.completion", "model": body.get("model", "mock-echo"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    })


async def healthz(request: Request):
    return JSONResponse({"ok": True, "service": "mock-llm"})


app = Starlette(routes=[
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/healthz", healthz),
])


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


if __name__ == "__main__":
    run()

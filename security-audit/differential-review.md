# Differential review: `main`…`a2b873c` (PR #2)

Strategy: SMALL/FOCUSED. High-risk files read in full. Base `c21b594`. Head `a2b873c`.

## What changed

PR #2 removes the in-tree mock LLM from the default path, adds nh3/tinycss2 sanitizer, killable render children, `deleting_at`, atomic job claim, `check_fields` for card metadata, compose bind to `127.0.0.1`, image digest pin, e2e bind-address tests.

High-risk paths: `html_sanitize.py` (new), `safety.py`, `xlsx.py`, `service.py` (claim/delete/enqueue), `server.py` (CSP, bind), `config.py`, `subprocess_render.py`, `compose.yaml`.

## Blast radius

`run_build` is the only producer of stored bytes. `Service.create`/`edit`/`delete`/`share` are the state machine. Callers: MCP tools and HTTP preview/share/download. A sanitizer miss hits every standalone export. A claim miss hits every concurrent build.

## Tests

New: `test_html_sanitize.py` (Chrome harness), `test_subprocess_render.py`, `test_service.py` delete/enqueue/idempotency, `tests/e2e/test_bind_address.py`. Formula and private-link unit tests exist; they do not cover `127.1`, `[::1]`, or sslip.io.

## Adversarial notes on the diff

The sanitizer and claim/delete work are security-positive versus `main`. The remaining issues are gaps in the new controls, not regressions of a stronger `main` behavior (`main` used regex stripping only).

No removed `fix`/`CVE` commits. Validation was added, not removed.

## Findings attributed to this diff

None of the ranked Medium/Low items are unique regressions versus `main`. Finding 1 (private-link parser) existed in a weaker form on `main` and still exists after `check_fields` was added. Finding 2 (`AM_HOST=0.0.0.0`) is unchanged; compose publish was tightened. Finding 3 (DNS rebinding opt-in) is new surface from FastMCP settings defaulting off.

Confidence: high on sanitizer/xlsx/claim tests; medium on multi-process delete (not executed against a live store).
EOF
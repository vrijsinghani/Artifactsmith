# Security audit of ArtifactSmith PR #2

Independent read-only review. Application code was not changed.

**Audited commit:** `a2b873c56365445d3443efb992eda480dafa593e` on `cursor/remove-mock-llm-review-cleanup-3c14` (PR #2). Subject: Close round-3 should-fix: limits, idempotency, docs, and image scan.

**Method:** Trail of Bits `run-scans.sh` (`--metrics=off`), bandit, gitleaks (full history), pip-audit on the resolved venv set, Trivy on image `artifactsmith-audit:a2b873c` (`sha256:506b1e33e9e383c234102f66cdec4041332ab2640ca5bcaeb21e5a0ae83d1b94`), threat model, sharp-edges on config/compose, differential review vs `main`, project checklist with live payloads. Every candidate went through fp-check. Only survivors are ranked below.

**Tool versions:** semgrep 1.180.0 (OSS; Pro unavailable), bandit 1.9.4, pip-audit 2.10.1, gitleaks 8.30.0, trivy 0.75.0, docker 29.1.3, Python 3.12.3, nh3 0.3.7, tinycss2 1.5.1, openpyxl 3.1.5, Chrome 148.0.7778.96. Raw outputs are under `security-audit/`.

Semgrep Pro did not run. `p/yaml` failed (exit 7) and was excluded from the merge. elttam and Apiiro rulesets were partial (exit 2). Failed/partial/skipped are in `security-audit/semgrep/scans.json`.

## Ranked findings

### Medium — Private-link checks miss IPv6, short IPs, and DNS-to-loopback hosts

`src/artifactsmith/renderers/safety.py:10`, `safety.py:57`, `safety.py:73`.

`URL_RE` stops at `]`, so `http://[::1]/x` is not parsed. `_host_is_private` only understands dotted textual IPs, so `http://127.1/x` and `http://0x7f.0.0.1/x` are not flagged. Hostnames are not resolved, so `http://127.0.0.1.sslip.io/x` and `http://evil.127.0.0.1.nip.io/x` pass.

**Reproduction:** `find_private_links` and `check_fields` returned empty for those five strings (see `security-audit/repro/checklist.jsonl`). `http://127.0.0.1`, `localhost`, `10.0.0.5`, `169.254.169.254`, and `metadata.google.internal` were flagged.

**Why it is exploitable here:** Titles, summaries, assumptions, and `needs_input` are scanned only by `check_fields` (`builder.py:335`). A hostile model can put a loopback URL in the card. MCP clients often linkify `http://…`. The operator's browser then hits a private host. HTML `href` values with `://` are stripped, and ordinary `http://host` bodies are stripped, so this is a metadata / leftover-text bypass of the documented "never point at private hosts" control, not server-side SSRF.

**Fix:** Parse with `urllib.parse` over a regex that accepts IPv6. Treat IPv4-mapped, short, octal, and hex forms as IP addresses. Fail closed on `]`. Optionally resolve DNS or block known rebinding suffixes (`nip.io`, `sslip.io`, `localtest.me`). Run the same check on the text that is actually stored on the card.

### Medium — `artifactsmith serve` listens on all interfaces by default

`src/artifactsmith/config.py:65` (`AM_HOST` default `0.0.0.0`). Bandit B104.

Compose publishes `127.0.0.1:8780` and `127.0.0.1:8781` (`compose.yaml:40`). A bare `artifactsmith serve` does not. Preview and share routes are unauthenticated capability URLs. The API is bearer-only, but it is then reachable on every NIC.

**Why it is exploitable here:** Operators who follow the CLI and skip compose expose MCP and `/p/`, `/dl/`, `/s/` on the LAN. Share links become world-reachable if the host has a public address.

**Fix:** Default `AM_HOST` to `127.0.0.1`. Keep `0.0.0.0` as an explicit opt-in. Refuse to start if the process is listening on a non-loopback address and `AM_ALLOWED_HOSTS` / a reverse-proxy flag is unset.

### Low — MCP DNS-rebinding protection is off until an allow-list is set

`src/artifactsmith/server.py:39`. `enable_dns_rebinding_protection` is true only when `AM_ALLOWED_HOSTS` or `AM_ALLOWED_ORIGINS` is non-empty. Both default to empty.

**Why it is exploitable here:** A browser origin confused onto the API port can call `/mcp` without a Host check. Bearer tokens are not sent automatically by a normal page, so this is defense-in-depth, not a token theft by itself.

**Fix:** Enable the MCP transport check by default for `127.0.0.1` / `localhost`, or require the allow-lists whenever `AM_HOST` is not loopback.

### Low — Compose object store uses `rustfs/rustfs:latest`

`compose.yaml:12`.

The app image pins `python:3.12-slim-bookworm` by digest. The store image does not. A tag move changes the process that holds `AM_STORE_KEY` / `AM_STORE_SECRET` and every artifact version.

**Fix:** Pin `rustfs/rustfs` by digest and verify it in CI the same way the app image is scanned.

### Low — `inspect` can return raw object-store exceptions

`src/artifactsmith/service.py:901`. `manifest_error` is `str(e)[:200]` from `store.get`. A missing object or boto error can leak endpoint, bucket, or key layout to any token with `read`.

**Fix:** Return a fixed `"manifest missing"` string and log the exception server-side.

### Low — `start_workers` re-queues in-flight `building` jobs

`src/artifactsmith/service.py:462`. Every `queued`/`building` row is set back to `queued` and enqueued. The claim `UPDATE … AND status='queued'` is atomic in one process (barrier test: one of two threads got the row). A second process sharing the same SQLite file can reset a live `building` job and claim it. Two workers then upload the same version.

**Fix:** Claim with `UPDATE … WHERE status IN ('queued','building') AND (status='queued' OR started_at < stale)`. Do not reset `building` unless the row is older than the build timeout. Document single-replica SQLite.

## Controls that held

1. HTML/CSS sanitizer: style breakout, entity-encoded `</style>`, `u\72l()`, `@\69mport`, `image-set`, `@font-face`, backslash hrefs, `javascript:`, protocol-relative links. Chrome `--dump-dom` on the standalone export (no CSP) did not fire `onerror`.
2. XLSX: `=`, `+`, `-`, `@` cells and the title stayed `data_type='s'` after openpyxl save/reload, including `007` and `1e2`.
3. Job claim: `BEGIN IMMEDIATE` plus `status='queued'` admitted one worker. Delete refuses while `building` and cancels `queued`. Thread enqueue uses `call_soon_threadsafe`.
4. Limits: 200 KB source, 32k verbatim, 500k model chars, render kill + 512 MiB `RLIMIT_AS`, 50 MiB output, per-token hourly quota.
5. Listed secret shapes in titles/summaries (`sk-`, `ghp_`, PEM, `AKIA`, Slack, `Bearer`) failed `check_fields`.
6. gitleaks: no leaks in 19 commits. pip-audit on the resolved dependency pins: no known vulns.

## False positives discarded

- Semgrep `docker-compose.port-all-interfaces` on `compose.yaml:46`: the host side is `127.0.0.1`, not `0.0.0.0`.
- Semgrep Dependabot missing cooldown: supply-chain hygiene, not a runtime bug.
- Apiiro "obfuscation" on `md_parse._esc`, the `URL_RE` character class, and `Service.list`: ordinary string replace, a regex, and a method name.
- Bandit B608 at `service.py:1067`: `DELETE FROM {tbl}` with `tbl` from a fixed tuple.
- Bandit B112/B110 in `safety.py` and `subprocess_render.py`: `urlparse` skip and pipe close, not a swallowed auth check.
- Trivy 4 CRITICAL / 85 HIGH on Debian packages in the slim image (util-linux mount helpers, curl CLI, leftover `pip` 25.0.1). Python app packages had no High/Critical. Not a demonstrated ArtifactSmith entry-point exploit; treat as image-hardening follow-up.
- Empty `url()` left in an allowlisted inline `style` after nh3 stripped the argument: no host remains, Chrome has nothing to fetch.
- `gho_`, `glpat-`, and raw AWS secret keys missed by `SECRET_PATTERNS`: the code already calls this a heuristic, not a guarantee.

## Assumptions

Single-replica compose on loopback is the intended deploy. Identity is bearer tokens only. The LLM provider is untrusted for content and out of scope as a compromised vendor. No operator answered extra scoping questions; ranking uses those assumptions.
EOF
# Security review, 9 October 2026

Review of ArtifactSmith at
`a2b873c56365445d3443efb992eda480dafa593e`. Application code was not
changed in this review. Status cites commits that are on `main`
(`266404e` and its ancestors).

Scanner notes in this document were produced by the reviewer at
`a2b873c56365445d3443efb992eda480dafa593e`. Run a fresh review with the
commands in `docs/security-review.md`. Those commands do not replay the
historical tool output. Keep raw SARIF, JSON, and scanner logs out of git
(CI artifact or local `security-audit/`).

Tools used at that commit (known): semgrep 1.180.0 via the Trail of Bits
runner (`--metrics=off`), bandit 1.9.4, gitleaks 8.30.0 (full history),
pip-audit 2.10.1 on the whole review venv (not an app-only freeze), trivy
0.75.0 on image `artifactsmith-audit:a2b873c`
(`sha256:506b1e33e9e383c234102f66cdec4041332ab2640ca5bcaeb21e5a0ae83d1b94`).
Also: threat model, sharp-edges on config/compose, differential review vs
`main` as it then was, and the project checklist with live payloads.

Third-party Semgrep rules were cloned at whatever HEAD `run-scans.sh`
fetched that day. Exact clone SHAs were not recorded (unknown). Registry
packs used: `p/owasp-top-ten`, `p/secrets`, `p/python`, `p/dockerfile`,
`p/docker-compose`, `p/github-actions` (registry contents also move;
versions not recorded). Dependency resolution for pip-audit was the
installed review venv, not a locked `uv export` / requirements freeze.

Libraries used in reproductions: Python 3.12.3, nh3 0.3.7, tinycss2 1.5.1,
openpyxl 3.1.5, Chrome 148.0.7778.96, docker 29.1.3.

| Finding | Severity | Status |
|---|---|---|
| Private-link checks miss IPv6, short IPs, and wildcard-DNS hosts | Medium | Mitigated in `3ef385a`. Residual: hosts outside the wildcard-DNS suffix list that resolve to loopback still pass. |
| `artifactsmith serve` listens on all interfaces by default | Medium | Fixed in `3ef385a` |
| MCP DNS-rebinding protection is off until an allow-list is set | Low | Fixed in `3ef385a` |
| `inspect` can return raw object-store exceptions | Low | Fixed in `3ef385a`. `9e9b239` did not change inspect; it only tightened `store.py` bucket-create error handling. |
| `start_workers` re-queues in-flight `building` jobs | Low | Accepted constraint on `main` (`9e9b239`): one process per data directory; restart re-queues interrupted builds. |

## Findings

### Medium: Private-link checks miss IPv6, short IPs, and wildcard-DNS hosts

Status: mitigated in `3ef385a`. Known wildcard-DNS suffixes (`nip.io`,
`sslip.io`, and the rest of `WILDCARD_DNS_SUFFIXES`) are blocked. Any other
domain that resolves to `127.0.0.1` still passes. That is leftover-text
exposure, not server-side SSRF.

At the audited commit: `src/artifactsmith/renderers/safety.py:10`,
`safety.py:57`, `safety.py:73`.

`URL_RE` stopped at `]`, so `http://[::1]/x` was not parsed. `_host_is_private`
only understood dotted textual IPs, so `http://127.1/x` and `http://0x7f.0.0.1/x`
were not flagged. Hostnames were not resolved, so
`http://127.0.0.1.sslip.io/x` and `http://evil.127.0.0.1.nip.io/x` passed.

Reproduction at `a2b873c`: `find_private_links` and `check_fields` returned
empty for those five strings. `http://127.0.0.1`, `localhost`, `10.0.0.5`,
`169.254.169.254`, and `metadata.google.internal` were flagged.

Titles, summaries, assumptions, and `needs_input` are scanned only by
`check_fields` (`builder.py:335`). A hostile model can put a loopback URL in
the card. MCP clients often linkify `http://…`. The operator's browser then
hits a private host. HTML `href` values with `://` are stripped, and ordinary
`http://host` bodies are stripped, so this was a metadata / leftover-text
bypass of the documented "never point at private hosts" control.

`3ef385a` parses with `urllib.parse`, accepts IPv6, and treats IPv4-mapped,
short, octal, hex, and integer forms as IPs. It blocks the listed
wildcard-DNS suffixes. It does not resolve arbitrary public DNS to see
whether the answer is loopback.

### Medium: `artifactsmith serve` listens on all interfaces by default

Status: fixed in `3ef385a`.

At the audited commit: `src/artifactsmith/config.py:65` (`AM_HOST` default
`0.0.0.0`). Bandit B104.

Compose published `${AM_BIND_ADDRESS:-127.0.0.1}:8780` and `:8781`
(`compose.yaml:46` at `a2b873c`). A bare `artifactsmith serve` did not.
Preview and share routes are unauthenticated capability URLs. The API is
bearer-only, but it was then reachable on every NIC.

Operators who follow the CLI and skip compose expose MCP and `/p/`, `/dl/`,
`/s/` on the LAN. Share links become world-reachable if the host has a
public address.

`3ef385a` defaults `AM_HOST` to `127.0.0.1`. Compose and the container image
now set `0.0.0.0` for in-container listen.

### Low: MCP DNS-rebinding protection is off until an allow-list is set

Status: fixed in `3ef385a`.

At the audited commit: `src/artifactsmith/server.py:39`.
`enable_dns_rebinding_protection` was true only when `AM_ALLOWED_HOSTS` or
`AM_ALLOWED_ORIGINS` was non-empty. Both defaulted to empty.

A browser origin confused onto the API port can call `/mcp` without a Host
check. Bearer tokens are not sent automatically by a normal page, so this is
defense-in-depth, not a token theft by itself.

`3ef385a` defaults allowed hosts to `127.0.0.1` and `localhost` on the API
port, which turns the MCP transport check on for plain `serve`.

### Low: `inspect` can return raw object-store exceptions

Status: fixed in `3ef385a`.

At the audited commit: `src/artifactsmith/service.py:901`. `manifest_error`
was `str(e)[:200]` from `store.get`. A missing object or boto error could
leak endpoint, bucket, or key layout to any token with `read`.

`3ef385a` returns the fixed string `store_read_failed` and logs the
exception server-side. `9e9b239` did not touch that inspect path. It
changed `store.py` so `head_bucket` 403/400 no longer trigger
`create_bucket` (only missing-bucket codes do).

### Low: `start_workers` re-queues in-flight `building` jobs

Status: accepted constraint on `main` (`9e9b239`).

At the audited commit: `src/artifactsmith/service.py:462`. Every
`queued`/`building` row was set back to `queued` and enqueued. The claim
`UPDATE … AND status='queued'` is atomic in one process (barrier test: one of
two threads got the row). A second process sharing the same SQLite file could
reset a live `building` job and claim it.

`3ef385a` skipped fresh `building` rows and only re-queued those older than
the build timeout. `9e9b239` changed that on purpose: on restart, every
`queued` and `building` job is re-queued so a mid-run build can finish.
Claim stays `UPDATE … WHERE status='queued'`. The accepted constraint is one
server process per data directory. Do not share the same SQLite file across
processes.

## Controls that held at `a2b873c`

1. HTML/CSS sanitizer: style breakout, entity-encoded `</style>`, `u\72l()`,
   `@\69mport`, `image-set`, `@font-face`, backslash hrefs, `javascript:`.
   Protocol-relative `//host` destinations were rejected (not upgraded).
   Chrome `--dump-dom` on the standalone export (no CSP) did not fire
   `onerror`.
2. XLSX: `=`, `+`, `-`, `@` cells and the title stayed `data_type='s'` after
   openpyxl save/reload, including `007` and `1e2`.
3. Job claim: `BEGIN IMMEDIATE` plus `status='queued'` admitted one worker.
   Delete refuses while `building` and cancels `queued`. Thread enqueue uses
   `call_soon_threadsafe`.
4. Limits: 200 KB source, 32k verbatim, 500k model chars, render kill +
   512 MiB `RLIMIT_AS`, 50 MiB output, per-token hourly quota.
5. Listed secret shapes in titles/summaries (`sk-`, `ghp_`, PEM, `AKIA`,
   Slack, `Bearer`) failed `check_fields`.
6. gitleaks reported no leaks in 19 commits. pip-audit on the review venv
   reported no known vulns (app and tooling packages were not separated).

## Post-audit hardening on `main`

These changes landed after `a2b873c` and are on `main` through `266404e`.

- Public `http://` and `https://` citation links to global hosts are kept in
  HTML, Markdown, PDF, DOCX, and XLSX. Private and loopback hosts and
  dangerous schemes stay blocked. Images, CSS, fonts, and iframes stay
  self-contained (`e39c62a`, `ed43ef6`).
- Markdown sanitization parses with `markdown-it-py` (CommonMark tokens),
  rewrites every link and image destination through the shared URL policy,
  and re-serializes. Text-token escapes are preserved. A fail-closed re-parse
  checks the policy after serialize (`08459a1`, `6e7bab6`).
- Protocol-relative `//host` citations upgrade to `https://` when the host
  is public; private or dangerous `//` destinations are neutralized
  (`1a26899`). At `a2b873c` those destinations were rejected.
- Emission uses the original destination encoding (trim and space→`%20`
  only; no HTML-unescape or percent-decode). Emit hosts must agree across
  de-obfuscated, urllib, and WHATWG-style parses. Userinfo is rejected
  (`266404e`).
- `9e9b239`: WeasyPrint floor raised to `>=70` (object `URLFetcher`);
  LLM replies are read with a 2 MiB stream cap; the same `idempotency_key`
  with a different request fingerprint returns a conflict; restart re-queues
  every `queued`/`building` job under a one-process-per-data-dir constraint.
  `store.py` creates a bucket only on missing-bucket errors, not on 403/400.
- Tailscale ULA `fd7a:115c:a1e0::/48` is treated as private (`66b5f67`).

## False positives discarded

- Semgrep `docker-compose.port-all-interfaces` on compose published ports: the
  host bind is `127.0.0.1` by default (`compose.yaml:46` at `a2b873c`).
- Apiiro "obfuscation" on `md_parse._esc`, the `URL_RE` character class, and
  `Service.list`: ordinary string replace, a regex, and a method name.
- Bandit B608 at `service.py:1067`: `DELETE FROM {tbl}` with `tbl` from a
  fixed tuple.
- Bandit B112/B110 in `safety.py` and `subprocess_render.py`: `urlparse` skip
  and pipe close, not a swallowed auth check.
- Empty `url()` left in an allowlisted inline `style` after nh3 stripped the
  argument: no host remains, Chrome has nothing to fetch.

## Accepted risks and follow-ups

- Trivy 4 CRITICAL / 85 HIGH on Debian packages in the slim image
  (util-linux mount helpers, curl CLI, leftover `pip` 25.0.1). Python app
  packages had no High/Critical. Reason: no demonstrated ArtifactSmith
  entry point. Next: rebuild on a thinner base or drop unused packages.
- Semgrep Dependabot missing cooldown. Reason: supply-chain hygiene, not a
  runtime bug. Next: add a cooldown in `.github/dependabot.yml`.
- `SECRET_PATTERNS` and gitleaks miss shapes such as `gho_`, `glpat-`, and
  raw AWS secret keys. Reason: the code already calls this a heuristic.
  Next: extend the pattern list or add a dedicated secret scanner on cards.

## Assumptions

Single-replica compose on loopback is the intended deploy. Identity is bearer
tokens only. The LLM provider is untrusted for content and out of scope as a
compromised vendor. Ranking used those assumptions.

# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-12

### Added

- MCP server tools: create, status, edit, inspect, export, share, unshare, delete.
- Workspace-scoped hashed tokens with permission sets.
- HTML house-style builder. Attribution for Humanizer and frontend-design is in `NOTICE`.
- Renderers for Markdown, PDF (WeasyPrint), DOCX (python-docx), and XLSX (openpyxl).
- Shared output checks: script and URL strip, private-link and secret rejection, size and time caps.
- Compose stack with an internal object store.
- GitHub Actions: ruff, mypy, pytest with coverage, denylist, Docker build, pip-audit, Trivy, compose smokes.
- Docs: architecture, threat model, operations, formats.
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, issue and PR templates, Makefile, pre-commit.
- Configurable host publish bind (`AM_BIND_ADDRESS`) with LAN and reverse-proxy docs, plus a CI smoke that hits a non-loopback address.
- Request and model-output size caps; idempotency keys tied to the request fingerprint with a conflict on reuse; quota checked when admitting a build.
- `LICENSE`, `NOTICE`, and `licenses/` in the Docker image; Trivy image scan; broader denylist coverage.
- Stronger private-link rejection (IPv6, alternate IPv4 spellings, CGNAT, wildcard DNS). Loopback-only listen by default outside compose. Default MCP Host allow-list for local serve.
- Six HTML style presets (`house`, `bold`, `editorial`, `playful`, `terminal`, `swiss`) and `AM_DEFAULT_STYLE` (#12).

### Changed

- Public `http://` and `https://` links to global hosts are kept in HTML, Markdown, PDF, DOCX, and XLSX so documents can cite outside sources. Private/loopback hosts and dangerous schemes stay blocked. Images, CSS, fonts, and iframes remain self-contained (no remote fetch when an export is opened).
- Markdown link sanitization uses CommonMark tokens (`markdown-it-py`) instead of regexes so titled images, whitespace-in-parens, and titled/whitespace reference destinations are neutralized correctly; text-token escapes are preserved on serialize with a fail-closed re-parse check; href classification de-obfuscates for scheme checks while emission never HTML-unescapes or percent-decodes (so ``&section=`` stays intact); emit hosts must agree across de-obfuscated, urllib, and WHATWG-style parses (userinfo rejected); remote image syntax becomes a normal link; bare URLs are not linkified inside code; reserved private DNS suffixes and IPv4-in-IPv6 forms are treated as private.
- Builder prompts for every format tell the model to cite public http(s) sources as labelled links (outside images become links); README MCP tool table matches the server; create example source supports its request; uninstall uses `docker compose down --volumes --rmi all`.
- `create` defaults: `kind=web_static`; slug derived from `display_name` when omitted (and the reverse). Remote `<img src>` becomes a clickable link in every format. Protocol-relative markdown citations upgrade to `https://`.
- `compose.yaml` restores `env_file: .env` so documented settings (`AM_SHARE_URL`, limits, `*_FILE`) reach the server.
- `compose.yaml` reads LLM and object-store credentials from `.env`. `./scripts/ensure-local-env.sh` fills empty store keys without corrupting a final line that lacks a trailing newline.
- Storage image pinned to `rustfs/rustfs:1.0.1` by digest.
- `AM_LLM_BASE` accepts a host root with or without a trailing `/v1`.
- HTML exports use an nh3 allowlist sanitizer; XLSX cells are written as literals; WeasyPrint (≥70) uses a deny-all URL fetcher.
- Renderers run in killable subprocesses; delete is serialized against new builds; job enqueue is thread-safe.
- Invalid integer/boolean env settings fail closed. `revoke_previews` expires private `/p/` links.
- Unit tests run on Python 3.11 and 3.12. Type checking uses `mypy --strict`.
- HTML sanitizer keeps page styling (CSS variables, `@media`, grid) while blocking remote fetches (#9).
- Phone table labels and a fixed viewport meta survive sanitization (#10).
- `mcp` pinned below 2 so FastMCP keeps working (#13).
- Python 3.14 and Debian 13 base image.

### Removed

- The canned Chat Completions endpoint from the shipped package, the CLI, `compose.yaml`, and the server image. Tests keep a fake chat model under `tests/support/` started by `compose.test.yaml`.

### Security

- Preview origin sends no cookies and sets `script-src 'none'`.
- Compose publishes API and preview on `127.0.0.1` only.
- The process does not send usage data anywhere.
- Configurable MCP Host/Origin allow-lists via `AM_ALLOWED_HOSTS` / `AM_ALLOWED_ORIGINS`.

[Unreleased]: https://github.com/vrijsinghani/artifactsmith/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vrijsinghani/artifactsmith/releases/tag/v0.1.0

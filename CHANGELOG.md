# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `create` `mode=fast` renders a first HTML page from Markdown (`::: ` blocks) in the chosen style; `mode=full` stays the default (`AM_BUILD_MODE`).
- `publish` stores a finished HTML or Markdown page without a model call, through the same safety checks, renderer, and version, share, and preview path as a build.

### Fixed

- An existing public `/s/` link serves the latest done version of that artifact. Passing `version` or `pin` keeps a separate link on one version. The page is still 404 when the served file does not match its recorded sha256.
- A shared or previewed markdown artifact opens as sanitized, house-styled HTML. The stored Markdown stays at `?raw=1` and `document.md`. Existing markdown artifacts render on open; their stored bytes and share hash are unchanged.
- `publish` and `edit` may set a different supported `format` on a new version. The artifact id and slug stay the same. When that version finishes, the artifact's format updates. Older versions keep their own format. An unknown format is rejected.
- A finished HTML document, or a request to publish HTML unchanged, stays on the full build when mode is omitted. Fast mode's HTML refusal tells the caller to use publish or mode=full.
- Only a request to publish that HTML as-is skips fast mode. Mentioning HTML beside "unchanged" or "do not add" stays fast. A fast HTML refusal says to publish HTML as-is with the publish tool or mode=full.
- Automatic charts skip approximate cost columns such as "~$310" and "Est. cost" when a real money column is present, and do not chart an estimate column on its own.
- Chart labels match whole words, so Corporate, Wholesale, and Paid search are names; a blank unit is not money; chart text reads "412 clicks".
- Fast pages stay within a phone width without splitting short words; `AM_FAST_STYLES` can default named styles to fast; drop caps and charts skip code-heavy paragraphs, partial measure names, and non-currency columns.
- Fast pages take on each style's own design. A table draws a bar chart for its one main amount when the page has none; mixed facts, years, IDs, and ranks stay tables.
- Fast layout rendering runs in the killable child with the render timeout and output cap. Bracket runs stay linear, colspan stops at 12, and a table wider than 24 columns or 5,000 cells is clipped. Plain Markdown gains a hero or verdict band, a stat strip, status badges, and a single-line total row on a phone. `callout`, `tip`, and `info` are note aliases. A ` ```html ` fence stays code.
- Share, preview, and download responses set CSP `sandbox allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation` (with `script-src 'none'`) so a left click on an external link opens a new tab.
- Reply parsing keeps marker lines that belong to the page, keeps a complete file that omits `===END===`, reads a summary that follows the file, and refuses a file with no visible text or with page content left after the end marker.
- OpenAI secret checks flag classic `sk-` keys and hyphenated `sk-proj-`, `sk-svcacct-`, and `sk-admin-` keys that contain a digit, and still ignore hyphenated prose such as `sk-learn` and `sk-your-openai-api-key-goes-here`.
- Document `create` `slug` and `display_name` (what they are, when they are required, their limits, and an example) plus `export`, `share`, `unshare`, and `delete`; Markdown export keeps sanitizer-allowed links as `[text](url)`.
- Bearer secret checks skip prose and placeholder shapes (`Bearer token`, `YOUR_API_KEY`, `your-api-key-goes-here`) instead of a character-length floor, and still flag short hex, base62, and padded base64. Secret scans also check a copy with HTML entities decoded and invisible characters removed.
- Single-file HTML (and clearly Markdown) builds recover when the model omits `===FILE: …===` but still emits a complete document. The recovered body still runs through the same sanitizer, checkers, and secret scan.
- PDF and DOCX render inline Markdown emphasis (bold, italic, inline code) instead of raw `**` markers.
- PDF wraps long code lines so they are not clipped at the page edge.
- XLSX stores ints, decimals, currency, and percents as numbers with matching formats; identifier columns and long digit strings stay text.
- XLSX uses the heading above each table as the sheet name (Excel-safe, 31 characters, deduplicated; `History` reserved).

### Changed

- The edit patch call times out after `AM_EDIT_PATCH_TIMEOUT_S` seconds (default 45) and then falls back to a full rewrite.
- `token perms` adds, removes, or lists permissions on an existing token (one audit line per change; the token value stays the same); over-long `summary` and `assumptions` are rejected when the call arrives; a job that fails because the artifact is being deleted clears its stored page.
- HTML and Markdown render in-process; PDF, DOCX, and XLSX stay in a fresh killable child, and XLSX builds its Markdown parser once per file.
- Edits return a find/replace patch (`===PATCH===` with `<<<FIND>>>` / `<<<REPLACE>>>` / `<<<END>>>`) applied to the base file, and fall back to the existing full-file prompt in the same job when the patch does not apply or fails checks. A move must change markup, not CSS order; the patch call times out after `AM_EDIT_PATCH_TIMEOUT_S` seconds. A missing end marker before the next block is closed in place, and a hunk or result that still contains patch markers is rejected.
- HTML sanitizer keeps a drawing-only inline SVG subset so charts survive; scripts, fetches, and active SVG stay blocked.
- CSS sanitizer keeps SVG paint properties so class-colored charts render; `url()` paint is still dropped.
- HTML exports start with a no-fetch CSP meta tag; pages over 2 MB are rejected; SVG roots/elements/text/nesting are capped.
- DOCX/XLSX strip C0 controls (except tab/LF/CR) in titles, sheet names, and body text.
- Blocked Markdown autolinks (including mailto) render as safely fenced inline code instead of vanishing.
- Authority-confused Markdown destinations (separators before `@`, scheme splits) are neutralized before parse.
- Raw HTML URL-bearing attributes on real tags are parsed with an HTML tokenizer and dropped when blocked or confused; public tags stay as written. Shown-as-code `href=` / `src=` snippets are not treated as links.
- Tailscale MagicDNS names (`*.ts.net`) are treated as private hosts when private-link blocking is on.
- Raw HTML `srcset` / `ping` split on ASCII whitespace only (so C0 separators inside a candidate URL stay part of the URL); abrupt HTML comment ends are normalized so following tags are still checked on Python 3.11/3.12.

## [0.1.0] - 2026-10-10

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

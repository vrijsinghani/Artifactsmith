# Threat model

## Assets

Workspace artifacts and their source material.

Bearer tokens, shown once and stored hashed.

The signing key for HMAC-signed `/dl/` download links.

Database-backed private `/p/` preview ids and public `/s/` share ids.

Object-store credentials.

Public share URLs. Anyone with a floating link can read the latest done version. Anyone with a pinned link can read that one version.

## Actors

A token holder in workspace A.

A token holder in workspace B.

Anyone who obtains a `/s/…` share URL.

Anyone who can reach the published ports.

The configured LLM endpoint. Its output is untrusted.

## Controls

| Threat | Control |
| ------ | ------- |
| Cross-workspace read | Token workspace must match the artifact. Knowing an id is not access. |
| Privilege abuse | Perms are create, read, edit, publish, export, share, delete. |
| Accidental publish | `create`, `edit`, and `publish` do not mint `/s/`. `share` is a separate call. |
| Stale or swapped public page | A floating share serves the latest done version and checks that version's sha256 before render. A pinned share stays on one version and its sha256. `unshare` sets `revoked_at` immediately. |
| Token theft | Tokens stored as SHA-256. The operator can revoke. Prefer `NAME_FILE` for secrets. |
| XSS / drive-by JS | HTML passes an nh3 allowlist; CSS passes a tinycss2 allowlist. CSP `script-src 'none'` and `sandbox allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation`. Separate cookieless origin. |
| SSRF / phone-home via output | Public http(s) `<a href>` citations allowed; private/loopback/link-local hosts rejected. Dangerous schemes stripped. Protocol-relative `//host` upgraded to `https://` only when the host is public; otherwise neutralized. Images, CSS `url()`, fonts, and iframes stay self-contained — no remote subresources. Renderers do not fetch (WeasyPrint deny-all fetcher). |
| Secret leakage in pages | Secret-shaped strings fail the build. |
| Oversize / hang | Field size caps, `AM_MAX_OUTPUT_BYTES`, render timeout, build timeout, per-token quota. |
| Guessed preview | Short database ids plus expiry. `/dl/` download links are HMAC-signed and last 15 minutes. `/p/` links are not HMAC-signed. |
| Store exposure | Compose publishes API and preview on `AM_BIND_ADDRESS` (default `127.0.0.1`) only. The object store has no host ports. |
| Prompt injection | The model sees delimited request/source text and is instructed to treat source as data. Output is parsed and sanitized; the process never executes model text. |
| Hallucinated facts | Builds that need a missing fact end as `needs_input` and store no file. Source material is capped; the model is not given tool/web access in this release. |

## Out of scope in this release

Identity beyond bearer tokens.

ACLs inside one workspace.

Scanning of binary payloads for hidden secrets.

A compromised LLM provider. Output is treated as hostile text. Delimiters reduce accidental instruction confusion; they do not stop a determined model from ignoring them.

Availability against a caller who holds a valid create or publish token, beyond the per-token quota.

Multi-process / multi-host SQLite. Run one server process per data directory.

## Operator duties

Bind a reverse proxy with TLS if you expose the service past localhost. Rotate tokens and the signing key on suspicion ([operations.md](operations.md)). Keep `AM_BLOCK_PRIVATE_LINKS=true` unless you have a documented exception. Set `AM_API_URL` / `AM_PREVIEW_URL` / `AM_SHARE_URL` to the addresses clients will open.

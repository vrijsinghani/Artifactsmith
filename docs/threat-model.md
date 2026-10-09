# Threat model

## Assets

Workspace artifacts and their source material.

Bearer tokens, shown once and stored hashed.

The signing key for private preview and download links.

Object-store credentials.

Public share URLs. Anyone with the link can read that exact version.

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
| Privilege abuse | Perms are create, read, edit, export, share, delete. |
| Accidental publish | `create` and `edit` do not mint `/s/`. `share` is a separate call. |
| Stale public page | Shares pin version and sha256. `unshare` sets `revoked_at` immediately. |
| Token theft | Tokens stored as SHA-256. The operator can revoke. Prefer `NAME_FILE` for secrets. |
| XSS / drive-by JS | Scripts stripped. CSP `script-src 'none'`. Separate cookieless origin. |
| SSRF via output | Remote URLs stripped. Private and loopback hosts rejected before render. Renderers do not fetch. |
| Secret leakage in pages | Secret-shaped strings fail the build. |
| Oversize / hang | `AM_MAX_OUTPUT_BYTES`, render timeout, build timeout, per-token quota. |
| Guessed preview | Short ids plus expiry. Download links are HMAC-signed and last 15 minutes. |
| Store exposure | Compose publishes API and preview on `127.0.0.1` only. The object store has no host ports. |

## Out of scope in this release

Identity beyond bearer tokens.

ACLs inside one workspace.

Scanning of binary payloads for hidden secrets.

A compromised LLM provider. Output is treated as hostile text.

Availability against a caller who holds a valid create token, beyond the per-token quota.

## Operator duties

Bind a reverse proxy if you expose the service past localhost. Rotate tokens and the signing key on suspicion ([operations.md](operations.md)). Keep `AM_BLOCK_PRIVATE_LINKS=true` unless you have a documented exception.

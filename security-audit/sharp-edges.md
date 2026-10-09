# Sharp edges: config and compose (`a2b873c`)

## Defaults

| Setting | Default | Zero/empty behavior | Verdict |
|---|---|---|---|
| `AM_HOST` | `0.0.0.0` | Listens on every NIC | Insecure default (Medium) |
| `AM_BLOCK_PRIVATE_LINKS` | true | `false` disables the check | Secure default; boolean cliff if flipped |
| `AM_ALLOWED_LINK_DOMAINS` | empty | No public-host allow-list | OK with sanitizer stripping `http(s)` from bodies |
| `AM_ALLOWED_HOSTS` / `AM_ALLOWED_ORIGINS` | empty | DNS-rebinding protection off | Footgun (Low) |
| `AM_SHARE_TTL_DAYS` | 30 | `0` means until revoked, not "no shares" | Documented; easy to misread |
| `AM_SHARE_TTL_MAX_DAYS` | 365 | Minimum 1 | OK |
| `AM_MAX_BUILDS` / `AM_BUILDS_PER_HOUR` | 2 / 20 | Minimum 1; `0` rejected | OK |
| CLI `token add --perms` | all six perms | Empty list rejected | Privilege footgun (Low) |

`NAME` wins over `NAME_FILE`. Invalid bools/ints raise `ConfigError` (fail closed). Signing key is created `0600` via `mkstemp` + `replace`.

## Compose

`compose.yaml` publishes API and preview on `127.0.0.1` only. Object store has no host ports. Store credentials are a local pair, not the rustfs well-known admin pair. `rustfs/rustfs:latest` is unpinned. `compose.test.yaml` is test-only and points `AM_LLM_BASE` at `fake-chat-llm`.

Semgrep's "port on all interfaces" hit on lines 46–47 is a false positive: the mapping is `127.0.0.1:8780:8780`.

## Adversaries

Scoundrel operator: `AM_BLOCK_PRIVATE_LINKS=false`, `AM_HOST=0.0.0.0`, `AM_SHARE_TTL_DAYS=0`. Lazy operator: runs `artifactsmith serve` and gets a LAN listener. Confused operator: thinks TTL `0` disables sharing.
EOF
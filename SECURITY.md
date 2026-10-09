# Security

If you found a hole in a running ArtifactSmith instance, report it privately.

## Report a vulnerability

Use GitHub private vulnerability reporting on this repository (Security, then Report a vulnerability).

Do not file a public issue for a working exploit, a leaked token that is still valid, or object-store credentials.

We aim to acknowledge a report within 7 days.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Runtime behavior

The process does not send usage data anywhere.

The server does not execute model output.

Renderers do not fetch URLs found in generated content.

[docs/threat-model.md](docs/threat-model.md) lists the controls and their limits.

## Operator checklist

Keep compose host ports on `127.0.0.1` unless a reverse proxy terminates TLS.

Store secrets in `*_FILE` mounts. Do not commit filled env files.

Revoke tokens when a client is retired.

Treat `/s/…` share URLs as public the moment `share` succeeds.

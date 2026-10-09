#!/usr/bin/env bash
# Verify that values set in .env reach the server service / published ports via
# docker compose config. Restores .env afterward so probes cannot poison smoke.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

marker="am-probe-$(openssl rand -hex 4 2>/dev/null || od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
bash scripts/ensure-local-env.sh >/dev/null

backup="$(mktemp)"
cp .env "$backup"
cleanup() {
  cp "$backup" .env
  rm -f "$backup"
}
trap cleanup EXIT

# Probe public URL into the server service environment.
printf '\nAM_SHARE_URL=http://probe.example/%s\n' "$marker" >> .env
# Probe host publish bind into the ports section (use a distinctive loopback alias).
printf 'AM_BIND_ADDRESS=127.0.0.66\n' >> .env

cfg="$(docker compose -f compose.yaml config)"

# Prefer the server service block when present so we do not match unrelated services.
server_cfg="$cfg"
if printf '%s\n' "$cfg" | grep -q '^  server:'; then
  server_cfg="$(printf '%s\n' "$cfg" | awk '
    /^  server:/ {grab=1; print; next}
    grab && /^  [a-zA-Z0-9_-]+:/ {exit}
    grab {print}
  ')"
fi

if ! printf '%s\n' "$server_cfg" | grep -q "http://probe.example/${marker}"; then
  echo "check-compose-env: AM_SHARE_URL from .env did not reach server via compose config" >&2
  exit 1
fi
if ! printf '%s\n' "$cfg" | grep -Eq "127\.0\.0\.66:8780"; then
  echo "check-compose-env: AM_BIND_ADDRESS from .env did not reach published ports via compose config" >&2
  exit 1
fi
echo "check-compose-env: ok (AM_SHARE_URL and AM_BIND_ADDRESS reached compose config)"

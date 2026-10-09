#!/usr/bin/env bash
# Verify that a value set in .env reaches the server service via docker compose config.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

marker="am-probe-$(openssl rand -hex 4 2>/dev/null || od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
bash scripts/ensure-local-env.sh >/dev/null

# Append a unique probe variable; compose must surface it on the server service.
printf '\nAM_SHARE_URL=http://probe.example/%s\n' "$marker" >> .env

cfg="$(docker compose -f compose.yaml config)"
if ! printf '%s\n' "$cfg" | grep -q "http://probe.example/${marker}"; then
  echo "check-compose-env: AM_SHARE_URL from .env did not reach server via compose config" >&2
  exit 1
fi
echo "check-compose-env: ok (AM_SHARE_URL reached server)"

#!/usr/bin/env bash
# Create .env from .env.example if needed, and fill empty object-store keys.
# The main compose file refuses to start when AM_STORE_KEY / AM_STORE_SECRET are unset.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "wrote .env from .env.example"
fi

rand_hex() {
  local nbytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$nbytes"
  else
    od -An -tx1 -N"$nbytes" /dev/urandom | tr -d ' \n'
  fi
}

# Return 0 if NAME is unset or empty after trimming quotes/whitespace.
needs_value() {
  local name="$1"
  local line raw
  line="$(grep -E "^(export[[:space:]]+)?${name}=" .env | tail -n1 || true)"
  if [ -z "$line" ]; then
    return 0
  fi
  raw="${line#*=}"
  raw="${raw%$'\r'}"
  # Strip matching surrounding quotes.
  if [[ "$raw" == \"*\" ]]; then
    raw="${raw:1:${#raw}-2}"
  elif [[ "$raw" == \'*\' ]]; then
    raw="${raw:1:${#raw}-2}"
  fi
  # Trim whitespace.
  raw="$(printf '%s' "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$raw" ]
}

set_or_fill() {
  local name="$1"
  local value="$2"
  local nl=$'\n'
  local ending tmp
  ending=""
  if grep -q $'\r' .env 2>/dev/null; then
    ending=$'\r'
  fi
  if ! needs_value "$name"; then
    return 1
  fi
  if grep -Eq "^(export[[:space:]]+)?${name}=" .env; then
    tmp="$(mktemp)"
    # Replace the last matching assignment; keep export prefix and line ending.
    awk -v name="$name" -v value="$value" -v ending="$ending" '
      BEGIN { last = 0 }
      {
        lines[NR] = $0
        if ($0 ~ "^(export[[:space:]]+)?" name "=") last = NR
      }
      END {
        for (i = 1; i <= NR; i++) {
          if (i == last) {
            if (lines[i] ~ /^export[[:space:]]+/) printf "export %s=%s%s\n", name, value, ending
            else printf "%s=%s%s\n", name, value, ending
          } else {
            print lines[i]
          }
        }
      }
    ' .env >"$tmp"
    mv "$tmp" .env
  else
    printf '%s=%s%s\n' "$name" "$value" "$ending" >> .env
  fi
  return 0
}

wrote=0
if set_or_fill AM_STORE_KEY "$(rand_hex 16)"; then wrote=1; fi
# 32 hex chars (16 bytes) stays under the 40-char secret-key cap used by RustFS/MinIO.
if set_or_fill AM_STORE_SECRET "$(rand_hex 16)"; then wrote=1; fi

chmod 600 .env

if [ "$wrote" -eq 1 ]; then
  echo "generated AM_STORE_KEY / AM_STORE_SECRET in .env"
else
  echo ".env already has object-store credentials"
fi

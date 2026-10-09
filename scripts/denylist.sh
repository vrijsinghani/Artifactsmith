#!/usr/bin/env bash
# Scan tracked files for high-signal secrets and generic private-looking identifiers.
# Exit 1 on hits; exit 2 on scan failures; exit 0 when clean.
# Scans every tracked file (including tests/ and examples/). Fixture strings must
# use short placeholders so they do not match the value-length floors below.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Secret-shaped credentials that must never be committed.
SECRET_RE='BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,}'

# Generic private identifiers: absolute home paths, and env assignments whose
# values look like real credentials (length floor avoids docs/test placeholders).
PRIVATE_RE='(/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|C:\\Users\\[A-Za-z0-9._-]+)|(AM_[A-Z0-9_]*(SECRET|KEY|TOKEN|PASSWORD)|OPENAI_API_KEY)=[A-Za-z0-9+/=_-]{16,}'

hit=0
scan_fail=0
scanned=0
tmp="$(mktemp)"
git ls-files -z >"$tmp"
while IFS= read -r -d '' f; do
  [ -z "$f" ] && continue
  [ "$f" = "scripts/denylist.sh" ] && continue
  scanned=$((scanned + 1))
  set +e
  secret_matches=$(grep -nE -- "$SECRET_RE" "$f" 2>/dev/null)
  secret_status=$?
  private_matches=$(grep -nE -- "$PRIVATE_RE" "$f" 2>/dev/null)
  private_status=$?
  set -e
  if [ "$secret_status" -eq 0 ]; then
    printf '%s (secret):\n%s\n' "$f" "$secret_matches"
    hit=1
  elif [ "$secret_status" -gt 1 ]; then
    echo "denylist: grep failed on $f (exit $secret_status)" >&2
    scan_fail=1
  fi
  if [ "$private_status" -eq 0 ]; then
    printf '%s (private-identifier):\n%s\n' "$f" "$private_matches"
    hit=1
  elif [ "$private_status" -gt 1 ]; then
    echo "denylist: grep failed on $f (exit $private_status)" >&2
    scan_fail=1
  fi
done <"$tmp"
rm -f "$tmp"

if [ "$scan_fail" -ne 0 ]; then
  echo "DENYLIST: scan failure(s) above." >&2
  exit 2
fi
if [ "$hit" -ne 0 ]; then
  echo "DENYLIST: secret-shaped or private identifier(s) found in tracked files above." >&2
  exit 1
fi
echo "denylist: clean (${scanned} tracked files scanned)."

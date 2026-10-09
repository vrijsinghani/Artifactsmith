#!/usr/bin/env bash
# Scan tracked files for high-signal secrets that must not be committed.
# Operator-specific private-reference checks belong in private CI configuration.
# Exit 1 on hits; exit 2 on scan failures; exit 0 when clean.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

RE='BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,}'

hit=0
scan_fail=0
tmp="$(mktemp)"
git ls-files -z >"$tmp"
while IFS= read -r -d '' f; do
  [ -z "$f" ] && continue
  [ "$f" = "scripts/denylist.sh" ] && continue
  case "$f" in
    tests/*|examples/*) continue ;;
  esac
  set +e
  matches=$(grep -nE -- "$RE" "$f" 2>/dev/null)
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    printf '%s:\n%s\n' "$f" "$matches"
    hit=1
  elif [ "$status" -gt 1 ]; then
    echo "denylist: grep failed on $f (exit $status)" >&2
    scan_fail=1
  fi
done <"$tmp"
rm -f "$tmp"

if [ "$scan_fail" -ne 0 ]; then
  echo "DENYLIST: scan failure(s) above." >&2
  exit 2
fi
if [ "$hit" -ne 0 ]; then
  echo "DENYLIST: secret-shaped identifier(s) found in tracked files above." >&2
  exit 1
fi
count="$(git ls-files | wc -l | tr -d ' ')"
echo "denylist: clean (${count} tracked files scanned)."

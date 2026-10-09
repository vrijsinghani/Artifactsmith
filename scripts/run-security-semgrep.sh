#!/usr/bin/env bash
# Run the Trail of Bits Semgrep skill the way its runner expects.
# Third-party rules are moving upstream sources: this passes https git URLs
# so run-scans.sh can clone HEAD. It does not pin those rules.
# Original to ArtifactSmith (MIT).
set -euo pipefail
shopt -s inherit_errexit

ROOT=$(cd "$(dirname "$0")/.." && pwd)
FETCH_UPSTREAM="${FETCH_UPSTREAM:-$ROOT/.cursor/skills/fetch-upstream.sh}"

# Revisions named in docs/security-audit-2026-10-09.md. The 9 October 2026
# run did not record clone SHAs; "unknown" is honest. A later SHA here is a
# comparison target, not a pin.
AUDIT_TOB_RULES="${AUDIT_TOB_RULES:-unknown}"
AUDIT_ELTTAM_RULES="${AUDIT_ELTTAM_RULES:-unknown}"
AUDIT_APIIRO_RULES="${AUDIT_APIIRO_RULES:-unknown}"

TOB_RULES_URL=https://github.com/trailofbits/semgrep-rules.git
ELTTAM_RULES_URL=https://github.com/elttam/semgrep-rules.git
APIIRO_RULES_URL=https://github.com/apiiro/malicious-code-ruleset.git

usage() {
  cat <<'USAGE'
Usage: run-security-semgrep.sh [--summarize SCANS.json]

Default: fetch the Trail of Bits semgrep skill, write rulesets.json with
https third_party URLs, run run-scans.sh, record cloned commits and tool
versions, warn if those commits differ from the audit list, and fail if
scans.json has failed, skipped, coveredNothing, oversized, or partial=true.

  --summarize FILE   run only the scans.json completeness check
USAGE
}

die() {
  echo "run-security-semgrep.sh: $*" >&2
  exit 1
}

# run-scans.sh exits 0 when at least one scan succeeded, even if others
# failed or were skipped. Dispose of every incomplete result before calling
# a review complete.
check_scans_json() {
  local scans=$1
  [ -f "$scans" ] || die "scans.json missing: $scans"
  command -v jq >/dev/null 2>&1 || die "jq is required"
  if ! jq -e '
    (.failed | length) == 0
    and (.skipped | length) == 0
    and (.coveredNothing | length) == 0
    and (.oversized | length) == 0
    and all(.scans[]; .partial != true)
  ' "$scans" >/dev/null; then
    echo "run-security-semgrep.sh: scans.json is not complete:" >&2
    jq '{
      failed: .failed,
      skipped: .skipped,
      coveredNothing: .coveredNothing,
      oversized: .oversized,
      partial: [.scans[] | select(.partial == true) | {lang, ruleset}]
    }' "$scans" >&2
    exit 1
  fi
  echo "run-security-semgrep.sh: scans.json summary is clean"
}

# Process substitution hides the producer's exit status. Write to a file,
# then mapfile, and fail if the producer failed.
collect_stdout() {
  local dest=$1
  shift
  local tmp status
  tmp=$(mktemp)
  set +e
  "$@" >"$tmp"
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    rm -f "$tmp"
    return "$status"
  fi
  mapfile -t "$dest" <"$tmp"
  rm -f "$tmp"
  return 0
}

audit_listed_for() {
  case "$1" in
    *trailofbits*semgrep-rules*) printf '%s' "$AUDIT_TOB_RULES" ;;
    *elttam*semgrep-rules*) printf '%s' "$AUDIT_ELTTAM_RULES" ;;
    *apiiro*malicious-code-ruleset*) printf '%s' "$AUDIT_APIIRO_RULES" ;;
    *) printf 'unknown' ;;
  esac
}

record_run() {
  local output=$1
  local repos=$output/repos
  local rec=$output/revisions.json
  local items='[]'
  local url sha listed name
  command -v jq >/dev/null 2>&1 || die "jq is required"

  if [ -d "$repos" ]; then
    for dir in "$repos"/*; do
      [ -d "$dir/.git" ] || continue
      sha=$(git -C "$dir" rev-parse HEAD)
      url=$(git -C "$dir" remote get-url origin 2>/dev/null || echo unknown)
      listed=$(audit_listed_for "$url")
      name=$(basename "$dir")
      items=$(jq -c --arg name "$name" --arg url "$url" --arg sha "$sha" --arg listed "$listed" \
        '. + [{name:$name, url:$url, commit:$sha, audit_listed:$listed}]' <<<"$items")
      echo "WARNING: third-party Semgrep rules are moving upstream sources." >&2
      echo "WARNING: cloned $url at $sha; audit listed $listed" >&2
      if [ "$listed" != "unknown" ] && [ "$sha" != "$listed" ]; then
        echo "WARNING: RULE REVISION MISMATCH for $url" >&2
        echo "WARNING: expected $listed, got $sha" >&2
      fi
    done
  fi

  jq -n \
    --arg semgrep "$(semgrep --version 2>/dev/null || true)" \
    --arg python "$(python3 --version 2>/dev/null || true)" \
    --arg jqv "$(jq --version 2>/dev/null || true)" \
    --argjson cloned "$items" \
    '{
      semgrep: $semgrep,
      python: $python,
      jq: $jqv,
      third_party_rules: "moving upstream sources; run-scans.sh clones HEAD",
      cloned: $cloned
    }' >"$rec"
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "${1:-}" = "--summarize" ]; then
  [ -n "${2:-}" ] || die "--summarize needs a scans.json path"
  check_scans_json "$2"
  exit 0
fi

[ -f "$FETCH_UPSTREAM" ] || die "fetch helper missing: $FETCH_UPSTREAM"

# Do not use mapfile on a process substitution: a failed fetch would still
# look like success. collect_stdout checks the producer's exit status.
skill_lines=()
collect_stdout skill_lines bash "$FETCH_UPSTREAM" semgrep || die "failed to fetch the semgrep skill"
SKILL=${skill_lines[0]:-}
[ -n "$SKILL" ] || die "fetch-upstream.sh printed no path"
[ -f "$SKILL/scripts/run-scans.sh" ] || die "run-scans.sh missing under $SKILL"

OUTPUT="${OUTPUT:-$PWD/security-audit/semgrep}"
case "$OUTPUT" in
  /*) ;;
  *) OUTPUT="$PWD/$OUTPUT" ;;
esac
mkdir -p "$OUTPUT"

TARGET="${TARGET:-$PWD}"
case "$TARGET" in
  /*) ;;
  *) TARGET=$(cd "$TARGET" && pwd) ;;
esac

jq -n \
  --arg tob "$TOB_RULES_URL" \
  --arg elttam "$ELTTAM_RULES_URL" \
  --arg apiiro "$APIIRO_RULES_URL" \
  '{
    baseline: ["p/owasp-top-ten", "p/secrets", "p/python"],
    python: ["p/python"],
    docker: ["p/dockerfile"],
    yaml: ["p/docker-compose"],
    "github-actions": ["p/github-actions"],
    third_party: [$tob, $elttam, $apiiro]
  }' >"$OUTPUT/rulesets.json"

bash "$SKILL/scripts/run-scans.sh" \
  --target "$TARGET" \
  --output-dir "$OUTPUT" \
  --mode run-all \
  --rulesets "$OUTPUT/rulesets.json"

if [ -f "$SKILL/scripts/merge_sarif.py" ]; then
  python3 "$SKILL/scripts/merge_sarif.py" \
    "$OUTPUT/raw" "$OUTPUT/results/results.sarif" --scans "$OUTPUT/scans.json"
fi

record_run "$OUTPUT"
check_scans_json "$OUTPUT/scans.json"

#!/usr/bin/env bash
# Fetch companion skill files from pinned upstreams. Nothing here is vendored
# in git: rulesets, language notes, and collector scripts are cloned at run time.
set -euo pipefail

readonly TOB_URL=https://github.com/trailofbits/skills.git
readonly TOB_SHA=82fe8226252622fa807643bdca1710901198553a
readonly OA_URL=https://github.com/openai/skills.git
readonly OA_SHA=49f948faa9258a0c61caceaf225e179651397431
readonly CACHE="${ARTIFACTSMITH_SKILL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/artifactsmith-skills}"

usage() {
  cat <<'USAGE'
Usage: fetch-upstream.sh NAME [NAME...]

Prints the directory that holds the fetched files (one line per NAME).

  semgrep-refs           scan-modes.md, scan-workflow.md, rulesets.md
  sharp-edges-refs       language and config notes
  supply-chain-scripts   collect.py, render.py, and helpers
  sarif-extras           jq-queries.md
  differential-extras    methodology, patterns, reporting, adversarial
  fp-check-refs          verification checklists
  openai-best-practices  framework security notes
  openai-threat-model    prompt-template and controls list
  tob                    pinned Trail of Bits skills checkout
  openai                 pinned OpenAI skills checkout
USAGE
}

die() {
  echo "fetch-upstream.sh: $*" >&2
  exit 1
}

sparse_pin() {
  local name=$1 url=$2 sha=$3
  shift 3
  local dest="$CACHE/$name"
  if [ -d "$dest/.git" ] && [ "$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)" = "$sha" ]; then
    printf '%s\n' "$dest"
    return
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$url"
  git -C "$dest" fetch --depth 1 origin "$sha"
  git -C "$dest" sparse-checkout init --no-cone
  if [ "$#" -gt 0 ]; then
    git -C "$dest" sparse-checkout set "$@"
  fi
  git -C "$dest" checkout -q FETCH_HEAD
  printf '%s\n' "$dest"
}

fetch_one() {
  case "$1" in
    tob)
      sparse_pin tob "$TOB_URL" "$TOB_SHA"
      ;;
    openai)
      sparse_pin openai "$OA_URL" "$OA_SHA"
      ;;
    semgrep-refs)
      sparse_pin semgrep-refs "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/semgrep/references \
        plugins/static-analysis/skills/semgrep/workflows
      ;;
    sharp-edges-refs)
      sparse_pin sharp-edges-refs "$TOB_URL" "$TOB_SHA" \
        plugins/sharp-edges/skills/sharp-edges/references
      ;;
    supply-chain-scripts)
      sparse_pin supply-chain-scripts "$TOB_URL" "$TOB_SHA" \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor/scripts
      ;;
    sarif-extras)
      sparse_pin sarif-extras "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/sarif-parsing/resources
      ;;
    differential-extras)
      sparse_pin differential-extras "$TOB_URL" "$TOB_SHA" \
        plugins/differential-review/skills/differential-review/adversarial.md \
        plugins/differential-review/skills/differential-review/methodology.md \
        plugins/differential-review/skills/differential-review/patterns.md \
        plugins/differential-review/skills/differential-review/reporting.md
      ;;
    fp-check-refs)
      sparse_pin fp-check-refs "$TOB_URL" "$TOB_SHA" \
        plugins/fp-check/skills/fp-check/references
      ;;
    openai-best-practices)
      sparse_pin openai-best-practices "$OA_URL" "$OA_SHA" \
        skills/.curated/security-best-practices/references
      ;;
    openai-threat-model)
      sparse_pin openai-threat-model "$OA_URL" "$OA_SHA" \
        skills/.curated/security-threat-model/references
      ;;
    *)
      die "unknown name: $1"
      ;;
  esac
}

if [ "$#" -eq 0 ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  [ "$#" -eq 0 ] && exit 1
  exit 0
fi

for name in "$@"; do
  fetch_one "$name"
done

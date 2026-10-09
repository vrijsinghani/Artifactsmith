#!/usr/bin/env bash
# Fetch pinned upstream skill trees at run time. Does not vendor them in git.
# Trail of Bits material stays in their repo (CC-BY-SA-4.0). This script is
# original to ArtifactSmith (MIT).
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

Trail of Bits (pin 82fe8226252622fa807643bdca1710901198553a):
  semgrep
  sarif-parsing
  differential-review
  sharp-edges
  supply-chain-risk-auditor
  fp-check
  tob                      all of the above (prints the clone root)

OpenAI curated (pin 49f948faa9258a0c61caceaf225e179651397431):
  openai-best-practices    framework notes (SKILL.md is already in git)
  openai-threat-model      prompt template and controls list
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
  local root
  case "$1" in
    tob)
      sparse_pin tob "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/semgrep \
        plugins/static-analysis/skills/sarif-parsing \
        plugins/differential-review/skills/differential-review \
        plugins/sharp-edges/skills/sharp-edges \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor \
        plugins/fp-check/skills/fp-check
      ;;
    semgrep)
      root=$(sparse_pin semgrep "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/semgrep)
      printf '%s\n' "$root/plugins/static-analysis/skills/semgrep"
      ;;
    sarif-parsing)
      root=$(sparse_pin sarif-parsing "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/sarif-parsing)
      printf '%s\n' "$root/plugins/static-analysis/skills/sarif-parsing"
      ;;
    differential-review)
      root=$(sparse_pin differential-review "$TOB_URL" "$TOB_SHA" \
        plugins/differential-review/skills/differential-review)
      printf '%s\n' "$root/plugins/differential-review/skills/differential-review"
      ;;
    sharp-edges)
      root=$(sparse_pin sharp-edges "$TOB_URL" "$TOB_SHA" \
        plugins/sharp-edges/skills/sharp-edges)
      printf '%s\n' "$root/plugins/sharp-edges/skills/sharp-edges"
      ;;
    supply-chain-risk-auditor)
      root=$(sparse_pin supply-chain-risk-auditor "$TOB_URL" "$TOB_SHA" \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor)
      printf '%s\n' "$root/plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor"
      ;;
    fp-check)
      root=$(sparse_pin fp-check "$TOB_URL" "$TOB_SHA" \
        plugins/fp-check/skills/fp-check)
      printf '%s\n' "$root/plugins/fp-check/skills/fp-check"
      ;;
    openai-best-practices)
      root=$(sparse_pin openai-best-practices "$OA_URL" "$OA_SHA" \
        skills/.curated/security-best-practices/references)
      printf '%s\n' "$root/skills/.curated/security-best-practices/references"
      ;;
    openai-threat-model)
      root=$(sparse_pin openai-threat-model "$OA_URL" "$OA_SHA" \
        skills/.curated/security-threat-model/references)
      printf '%s\n' "$root/skills/.curated/security-threat-model/references"
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

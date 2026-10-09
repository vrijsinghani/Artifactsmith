#!/usr/bin/env bash
# Fetch pinned upstream skill trees at run time. Does not vendor them in git.
# Trail of Bits material stays in their repo (CC-BY-SA-4.0). This script is
# original to ArtifactSmith (MIT).
set -euo pipefail
shopt -s inherit_errexit

readonly TOB_URL=https://github.com/trailofbits/skills.git
readonly TOB_SHA=82fe8226252622fa807643bdca1710901198553a
readonly OA_URL=https://github.com/openai/skills.git
readonly OA_SHA=49f948faa9258a0c61caceaf225e179651397431
readonly TOB_RULES_URL=https://github.com/trailofbits/semgrep-rules.git
readonly TOB_RULES_SHA=31390b3a99c04c81522d1b37c8d1900aa2dd4094
readonly ELTTAM_RULES_URL=https://github.com/elttam/semgrep-rules.git
readonly ELTTAM_RULES_SHA=244268562cc92d33f54b8a60a187df5520f91b26
readonly APIIRO_RULES_URL=https://github.com/apiiro/malicious-code-ruleset.git
readonly APIIRO_RULES_SHA=a21246b666f34db899f0e33add7237ed70fab790
readonly CACHE="${ARTIFACTSMITH_SKILL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/artifactsmith-skills}"

usage() {
  cat <<'USAGE'
Usage: fetch-upstream.sh NAME [NAME...]

Prints the directory that holds the fetched files (one line per NAME).
Exits non-zero if a fetch fails or a required file is missing or changed.

Trail of Bits skills (pin 82fe8226252622fa807643bdca1710901198553a):
  semgrep
  sarif-parsing
  differential-review
  sharp-edges
  supply-chain-risk-auditor
  fp-check
  tob                      all of the above (prints the clone root)

Semgrep third-party rulesets (pinned commits):
  semgrep-rulesets         prints three clone directories, one per line

OpenAI curated (pin 49f948faa9258a0c61caceaf225e179651397431):
  openai-best-practices    framework notes (SKILL.md is already in git)
  openai-threat-model      prompt template and controls list
USAGE
}

die() {
  echo "fetch-upstream.sh: $*" >&2
  exit 1
}

# Return 0 only when dest is at sha, the worktree is clean, and each required
# relative path exists and matches the blob at HEAD.
cache_ok() {
  local dest=$1 sha=$2
  shift 2
  local head exp act f
  [ -d "$dest/.git" ] || return 1
  head=$(git -C "$dest" rev-parse HEAD) || return 1
  [ "$head" = "$sha" ] || return 1
  git -C "$dest" diff-index --quiet HEAD -- || return 1
  for f in "$@"; do
    [ -f "$dest/$f" ] || return 1
    exp=$(git -C "$dest" rev-parse "HEAD:$f") || return 1
    act=$(git -C "$dest" hash-object "$dest/$f") || return 1
    [ "$exp" = "$act" ] || return 1
  done
  return 0
}

# Clone or reuse dest at sha.
# Arguments after sha: sparse-checkout paths, then --, then required files
# (relative to dest) that must exist and match HEAD blobs.
sparse_pin() {
  local name=$1 url=$2 sha=$3
  shift 3
  local dest="$CACHE/$name"
  local sparse=() required=()
  local seen_sep=0 arg
  for arg in "$@"; do
    if [ "$arg" = "--" ]; then
      seen_sep=1
      continue
    fi
    if [ "$seen_sep" -eq 0 ]; then
      sparse+=("$arg")
    else
      required+=("$arg")
    fi
  done
  [ "$seen_sep" -eq 1 ] || die "sparse_pin $name: missing -- before required files"
  [ ${#sparse[@]} -gt 0 ] || die "sparse_pin $name: no sparse paths"
  [ ${#required[@]} -gt 0 ] || die "sparse_pin $name: no required files"
  if cache_ok "$dest" "$sha" "${required[@]}"; then
    printf '%s\n' "$dest"
    return 0
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$url"
  git -C "$dest" fetch --depth 1 origin "$sha" || die "fetch failed for $name ($url @$sha)"
  git -C "$dest" sparse-checkout init --no-cone
  git -C "$dest" sparse-checkout set "${sparse[@]}"
  git -C "$dest" checkout -q FETCH_HEAD || die "checkout failed for $name"
  cache_ok "$dest" "$sha" "${required[@]}" || die "required files missing or changed after fetch ($name)"
  printf '%s\n' "$dest"
}

# Full (non-sparse) pin for ruleset repos. Requires at least one file path.
clone_pin() {
  local name=$1 url=$2 sha=$3
  shift 3
  local dest="$CACHE/$name"
  if cache_ok "$dest" "$sha" "$@"; then
    printf '%s\n' "$dest"
    return 0
  fi
  rm -rf "$dest"
  mkdir -p "$CACHE"
  git clone --filter=blob:none "$url" "$dest" || die "clone failed for $name ($url)"
  git -C "$dest" fetch --depth 1 origin "$sha" || die "fetch failed for $name ($url @$sha)"
  git -C "$dest" checkout -q "$sha" || die "checkout failed for $name"
  cache_ok "$dest" "$sha" "$@" || die "required files missing or changed after fetch ($name)"
  printf '%s\n' "$dest"
}

require_dir() {
  local path=$1 label=$2
  [ -d "$path" ] || die "$label directory missing: $path"
}

require_file() {
  local path=$1 label=$2
  [ -f "$path" ] || die "$label missing: $path"
}

fetch_one() {
  local root skill
  case "$1" in
    tob)
      root=$(sparse_pin tob "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/semgrep \
        plugins/static-analysis/skills/sarif-parsing \
        plugins/differential-review/skills/differential-review \
        plugins/sharp-edges/skills/sharp-edges \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor \
        plugins/fp-check/skills/fp-check \
        -- \
        plugins/static-analysis/skills/semgrep/SKILL.md \
        plugins/static-analysis/skills/semgrep/scripts/run-scans.sh \
        plugins/static-analysis/skills/semgrep/scripts/merge_sarif.py \
        plugins/static-analysis/skills/sarif-parsing/SKILL.md \
        plugins/differential-review/skills/differential-review/SKILL.md \
        plugins/sharp-edges/skills/sharp-edges/SKILL.md \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor/SKILL.md \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor/scripts/collect.py \
        plugins/fp-check/skills/fp-check/SKILL.md) \
        || die "tob fetch failed"
      require_dir "$root" "tob"
      require_file "$root/plugins/static-analysis/skills/semgrep/SKILL.md" "semgrep SKILL.md"
      printf '%s\n' "$root"
      ;;
    semgrep)
      root=$(sparse_pin semgrep "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/semgrep \
        -- \
        plugins/static-analysis/skills/semgrep/SKILL.md \
        plugins/static-analysis/skills/semgrep/scripts/run-scans.sh \
        plugins/static-analysis/skills/semgrep/scripts/merge_sarif.py) \
        || die "semgrep fetch failed"
      skill="$root/plugins/static-analysis/skills/semgrep"
      require_file "$skill/SKILL.md" "semgrep SKILL.md"
      require_file "$skill/scripts/run-scans.sh" "run-scans.sh"
      require_file "$skill/scripts/merge_sarif.py" "merge_sarif.py"
      printf '%s\n' "$skill"
      ;;
    sarif-parsing)
      root=$(sparse_pin sarif-parsing "$TOB_URL" "$TOB_SHA" \
        plugins/static-analysis/skills/sarif-parsing \
        -- \
        plugins/static-analysis/skills/sarif-parsing/SKILL.md \
        plugins/static-analysis/skills/sarif-parsing/resources/sarif_helpers.py) \
        || die "sarif-parsing fetch failed"
      skill="$root/plugins/static-analysis/skills/sarif-parsing"
      require_file "$skill/SKILL.md" "sarif-parsing SKILL.md"
      require_file "$skill/resources/sarif_helpers.py" "sarif_helpers.py"
      printf '%s\n' "$skill"
      ;;
    differential-review)
      root=$(sparse_pin differential-review "$TOB_URL" "$TOB_SHA" \
        plugins/differential-review/skills/differential-review \
        -- \
        plugins/differential-review/skills/differential-review/SKILL.md) \
        || die "differential-review fetch failed"
      skill="$root/plugins/differential-review/skills/differential-review"
      require_file "$skill/SKILL.md" "differential-review SKILL.md"
      printf '%s\n' "$skill"
      ;;
    sharp-edges)
      root=$(sparse_pin sharp-edges "$TOB_URL" "$TOB_SHA" \
        plugins/sharp-edges/skills/sharp-edges \
        -- \
        plugins/sharp-edges/skills/sharp-edges/SKILL.md) \
        || die "sharp-edges fetch failed"
      skill="$root/plugins/sharp-edges/skills/sharp-edges"
      require_file "$skill/SKILL.md" "sharp-edges SKILL.md"
      printf '%s\n' "$skill"
      ;;
    supply-chain-risk-auditor)
      root=$(sparse_pin supply-chain-risk-auditor "$TOB_URL" "$TOB_SHA" \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor \
        -- \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor/SKILL.md \
        plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor/scripts/collect.py) \
        || die "supply-chain-risk-auditor fetch failed"
      skill="$root/plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor"
      require_file "$skill/SKILL.md" "supply-chain-risk-auditor SKILL.md"
      require_file "$skill/scripts/collect.py" "collect.py"
      printf '%s\n' "$skill"
      ;;
    fp-check)
      root=$(sparse_pin fp-check "$TOB_URL" "$TOB_SHA" \
        plugins/fp-check/skills/fp-check \
        -- \
        plugins/fp-check/skills/fp-check/SKILL.md) \
        || die "fp-check fetch failed"
      skill="$root/plugins/fp-check/skills/fp-check"
      require_file "$skill/SKILL.md" "fp-check SKILL.md"
      printf '%s\n' "$skill"
      ;;
    openai-best-practices)
      root=$(sparse_pin openai-best-practices "$OA_URL" "$OA_SHA" \
        skills/.curated/security-best-practices/references \
        -- \
        skills/.curated/security-best-practices/references/python-fastapi-web-server-security.md) \
        || die "openai-best-practices fetch failed"
      skill="$root/skills/.curated/security-best-practices/references"
      require_file "$skill/python-fastapi-web-server-security.md" "fastapi reference"
      printf '%s\n' "$skill"
      ;;
    openai-threat-model)
      root=$(sparse_pin openai-threat-model "$OA_URL" "$OA_SHA" \
        skills/.curated/security-threat-model/references \
        -- \
        skills/.curated/security-threat-model/references/prompt-template.md \
        skills/.curated/security-threat-model/references/security-controls-and-assets.md) \
        || die "openai-threat-model fetch failed"
      skill="$root/skills/.curated/security-threat-model/references"
      require_file "$skill/prompt-template.md" "prompt-template.md"
      require_file "$skill/security-controls-and-assets.md" "security-controls-and-assets.md"
      printf '%s\n' "$skill"
      ;;
    semgrep-rulesets)
      clone_pin tob-semgrep-rules "$TOB_RULES_URL" "$TOB_RULES_SHA" README.md \
        || die "trailofbits/semgrep-rules fetch failed"
      clone_pin elttam-semgrep-rules "$ELTTAM_RULES_URL" "$ELTTAM_RULES_SHA" README.md \
        || die "elttam/semgrep-rules fetch failed"
      clone_pin apiiro-malicious-code-ruleset "$APIIRO_RULES_URL" "$APIIRO_RULES_SHA" README.md \
        || die "apiiro/malicious-code-ruleset fetch failed"
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

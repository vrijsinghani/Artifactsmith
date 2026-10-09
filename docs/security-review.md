# How to run the security review skills

Skills that belong in this repo live in `.cursor/skills/`. OpenAI text keeps
`ATTRIBUTION.md` beside the skill and `licenses/Apache-2.0-openai-skills.txt`.
Trail of Bits skills are not copied here (CC-BY-SA-4.0). Fetch them at
run time; the index is `.cursor/skills/trail-of-bits.md`.

| Skill | Where | License |
|---|---|---|
| `semgrep`, `sarif-parsing`, `differential-review`, `sharp-edges`, `supply-chain-risk-auditor`, `fp-check` | fetch from [trailofbits/skills](https://github.com/trailofbits/skills) at the pin in the index | CC-BY-SA-4.0 (upstream) |
| `security-threat-model` | `.cursor/skills/security-threat-model/` | Apache-2.0 |
| `security-best-practices` | `.cursor/skills/security-best-practices/` | Apache-2.0 |
| `artifactsmith-security-checklist` | `.cursor/skills/artifactsmith-security-checklist/` | MIT |

Prerequisites: `git`, `bash`, `jq`, `python3`, Semgrep, bandit, gitleaks,
pip-audit, Trivy, and Docker (for the image scan). `uv` is optional for the
supply-chain collector. Activate `.venv` when this repo has one.

## 1. Semgrep

Install Semgrep. Fetch the skill, then use its `run-scans.sh`. Do not
hand-write `semgrep` lines. Third-party rulesets are cloned at the commits
pinned in `fetch-upstream.sh`.

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh semgrep)
mapfile -t RULE_DIRS < <(bash .cursor/skills/fetch-upstream.sh semgrep-rulesets)
OUTPUT="${OUTPUT:-$PWD/security-audit/semgrep}"
mkdir -p "$OUTPUT"
jq -n \
  --arg tob "${RULE_DIRS[0]}" \
  --arg elttam "${RULE_DIRS[1]}" \
  --arg apiiro "${RULE_DIRS[2]}" \
  '{
    baseline: ["p/owasp-top-ten", "p/secrets", "p/python"],
    python: ["p/python"],
    docker: ["p/dockerfile"],
    yaml: ["p/docker-compose"],
    "github-actions": ["p/github-actions"],
    third_party: [$tob, $elttam, $apiiro]
  }' > "$OUTPUT/rulesets.json"
bash "$SKILL/scripts/run-scans.sh" \
  --target "$(pwd)" \
  --output-dir "$OUTPUT" \
  --mode run-all \
  --rulesets "$OUTPUT/rulesets.json"
python3 "$SKILL/scripts/merge_sarif.py" \
  "$OUTPUT/raw" "$OUTPUT/results/results.sarif" --scans "$OUTPUT/scans.json"
```

Pins (also in `fetch-upstream.sh`):

- trailofbits/semgrep-rules `31390b3a99c04c81522d1b37c8d1900aa2dd4094`
- elttam/semgrep-rules `244268562cc92d33f54b8a60a187df5520f91b26`
- apiiro/malicious-code-ruleset `a21246b666f34db899f0e33add7237ed70fab790`

Fetch `sarif-parsing` and follow its `SKILL.md` to summarize the merged SARIF.
Report `failed`, `skipped`, `coveredNothing`, and `oversized` from `scans.json`.

## 2. Threat model (`security-threat-model`)

Read `docs/architecture.md`, `docs/threat-model.md`, `src/artifactsmith/server.py`,
`service.py`, `cli.py`, and `config.py`. Enumerate MCP tools, HTTP preview /
share / download routes, CLI, and env/config. Treat model output as untrusted.
Fetch the prompt template, then write `<name>-threat-model.md`:

```bash
TM=$(bash .cursor/skills/fetch-upstream.sh openai-threat-model)
```

## 3. Sharp edges

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh sharp-edges)
```

Read `config.py`, `.env.example`, `compose.yaml`, and any `compose*.yaml`.
Probe zero / empty / false defaults (`AM_BLOCK_PRIVATE_LINKS`, share TTL 0,
`AM_HOST`, empty allow-lists). Follow `$SKILL/SKILL.md`.

## 4. Differential review

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh differential-review)
git diff main...HEAD
```

Follow `$SKILL/SKILL.md`. High-risk here: sanitizer, auth, signing, job
claim/delete, store credentials.

## 5. Project checklist

Follow `artifactsmith-security-checklist`. Reproduce each class (sanitizer
payloads in headless Chromium without CSP, openpyxl formula round-trip, race
tests with barriers). Do not pass an item from unit tests alone.

## 6. False-positive check

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh fp-check)
```

Follow `$SKILL/SKILL.md` on every candidate finding before it enters a report.
Keep only what survives. One-line each discarded item.

## 7. Other scanners

Put outputs under `security-audit/` (gitignored) or attach them as a CI artifact.
Do not commit SARIF or JSON.

```bash
mkdir -p security-audit

# bandit 1.9.x on application code
python3 -m bandit -r src -f json -o security-audit/bandit.json
python3 -m bandit -r src

# gitleaks 8.x on full git history
gitleaks detect --source . --report-format json --report-path security-audit/gitleaks.json

# pip-audit 2.x on the installed package (activate .venv first)
python3 -m pip install -e .
python3 -m pip_audit --desc

# Trivy 0.75.x on the built image, not only the filesystem
docker build -t artifactsmith:review -f docker/Dockerfile .
trivy image --severity CRITICAL,HIGH artifactsmith:review
```

Supply-chain collector (fetch, then run). `uv` if you have it; otherwise
`python3`:

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh supply-chain-risk-auditor)
if command -v uv >/dev/null; then
  uv run --no-project "$SKILL/scripts/collect.py" .
else
  python3 "$SKILL/scripts/collect.py" .
fi
```

Record tool versions next to the outputs.

The 9 October 2026 review of `a2b873c56365445d3443efb992eda480dafa593e` is
`docs/security-audit-2026-10-09.md`.

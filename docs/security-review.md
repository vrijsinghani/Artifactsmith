# How to run the security review skills

Skills that belong in this repo live in `.cursor/skills/`. OpenAI text keeps
`ATTRIBUTION.md` beside the skill and `licenses/Apache-2.0-openai-skills.txt`.
Trail of Bits skills are **not** copied here (CC-BY-SA-4.0). Fetch them at
run time; the index is `.cursor/skills/trail-of-bits.md`.

| Skill | Where | License |
|---|---|---|
| `semgrep`, `sarif-parsing`, `differential-review`, `sharp-edges`, `supply-chain-risk-auditor`, `fp-check` | fetch from [trailofbits/skills](https://github.com/trailofbits/skills) at the pin in the index | CC-BY-SA-4.0 (upstream) |
| `security-threat-model` | `.cursor/skills/security-threat-model/` | Apache-2.0 |
| `security-best-practices` | `.cursor/skills/security-best-practices/` | Apache-2.0 |
| `artifactsmith-security-checklist` | `.cursor/skills/artifactsmith-security-checklist/` | MIT |

## 1. Semgrep

Install Semgrep. Fetch the skill, then use its `run-scans.sh`. Do not
hand-write `semgrep` lines. The runner clones third-party ruleset repos at
scan time.

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh semgrep)
OUTPUT="${OUTPUT:-$PWD/security-audit/semgrep}"
mkdir -p "$OUTPUT"
cat > "$OUTPUT/rulesets.json" <<'EOF'
{
  "baseline": ["p/owasp-top-ten", "p/secrets", "p/python"],
  "python": ["p/python"],
  "docker": ["p/dockerfile"],
  "yaml": ["p/yaml"],
  "github-actions": ["p/github-actions"],
  "third_party": [
    "https://github.com/trailofbits/semgrep-rules",
    "https://github.com/elttam/semgrep-rules",
    "https://github.com/apiiro/malicious-code-ruleset"
  ]
}
EOF
bash "$SKILL/scripts/run-scans.sh" \
  --target "$(pwd)" \
  --output-dir "$OUTPUT" \
  --mode run-all \
  --rulesets "$OUTPUT/rulesets.json"
python "$SKILL/scripts/merge_sarif.py" \
  "$OUTPUT/raw" "$OUTPUT/results/results.sarif" --scans "$OUTPUT/scans.json"
```

Fetch `sarif-parsing` and follow its `SKILL.md` to summarize the merged SARIF.
Report `failed`, `skipped`, `coveredNothing`, and `oversized` from `scans.json`.

## 2. Threat model (`security-threat-model`)

Read `docs/architecture.md`, `docs/threat-model.md`, `src/artifactsmith/server.py`,
`service.py`, `cli.py`, and `config.py`. Enumerate MCP tools, HTTP preview /
share / download routes, CLI, and env/config. Treat model output as untrusted.
Fetch the prompt template, then write `<name>-threat-model.md`:

```bash
REFS=$(bash .cursor/skills/fetch-upstream.sh openai-threat-model)
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

## 7. Other scanners this repo expects

- bandit on `src/`
- gitleaks on full git history
- pip-audit on the resolved dependency set (install the package, then audit)
- trivy on the **built Docker image**, not only the filesystem
- supply-chain collector:

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh supply-chain-risk-auditor)
uv run --no-project "$SKILL/scripts/collect.py" .
```

Record tool versions next to the outputs.

Keep raw SARIF, JSON, and scanner logs out of git. Store them as a CI
artifact, or under `security-audit/` on a local machine (that path is in
`.gitignore`).

A dated snapshot of PR #2 at `a2b873c` is `docs/security-audit-2026-10-09.md`.

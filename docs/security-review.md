# How to run the security review skills

The skills live in `.cursor/skills/` and `.agents/skills/` so they travel with
the repo. Third-party text keeps its own license file in each skill folder.

| Skill | Source | License |
|---|---|---|
| `semgrep`, `sarif-parsing` (grouped as `static-analysis`) | [Trail of Bits skills](https://github.com/trailofbits/skills) | CC-BY-SA-4.0 |
| `differential-review` | Trail of Bits | CC-BY-SA-4.0 |
| `sharp-edges` | Trail of Bits | CC-BY-SA-4.0 |
| `supply-chain-risk-auditor` | Trail of Bits | CC-BY-SA-4.0 |
| `fp-check` | Trail of Bits | CC-BY-SA-4.0 |
| `security-threat-model` | [OpenAI curated skills](https://github.com/openai/skills/tree/main/skills/.curated) | Apache-2.0 |
| `security-best-practices` | OpenAI | Apache-2.0 |
| `artifactsmith-security-checklist` | this repo | same as the project (MIT) |

Full license texts: `licenses/CC-BY-SA-4.0-trailofbits-skills.txt`,
`licenses/Apache-2.0-openai-skills.txt`, and `LICENSE` / `LICENSE.txt` next to
each copied skill.

## 1. Semgrep (`static-analysis` / `semgrep`)

Install Semgrep, then use `scripts/run-scans.sh` from the skill. Every command
must pass `--metrics=off`. Do not hand-write `semgrep` lines.

```bash
OUTPUT=/tmp/artifactsmith-semgrep
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
bash .cursor/skills/semgrep/scripts/run-scans.sh \
  --target "$(pwd)" \
  --output-dir "$OUTPUT" \
  --mode run-all \
  --rulesets "$OUTPUT/rulesets.json"
python .cursor/skills/semgrep/scripts/merge_sarif.py \
  "$OUTPUT/raw" "$OUTPUT/results/results.sarif" --scans "$OUTPUT/scans.json"
```

Then follow `sarif-parsing` to summarize `$OUTPUT/results/results.sarif`.
Report `failed`, `skipped`, `coveredNothing`, and `oversized` from `scans.json`.

## 2. Threat model (`security-threat-model`)

Read `docs/architecture.md`, `docs/threat-model.md`, `src/artifactsmith/server.py`,
`service.py`, `cli.py`, and `config.py`. Enumerate MCP tools, HTTP preview /
share / download routes, CLI, and env/config. Treat model output as untrusted.
Write `<name>-threat-model.md` using the skill's prompt template.

## 3. Sharp edges

Read `config.py`, `.env.example`, `compose.yaml`, and any `compose*.yaml`.
Probe zero / empty / false defaults (`AM_BLOCK_PRIVATE_LINKS`, share TTL 0,
`AM_HOST=0.0.0.0`, empty allow-lists). Follow `sharp-edges`.

## 4. Differential review

Against `main` (or the stated base):

```bash
git diff main...HEAD
```

Follow `differential-review`. Write a markdown report. High-risk: sanitizer,
auth, signing, job claim/delete, store credentials.

## 5. Project checklist

Follow `artifactsmith-security-checklist`. Reproduce each class (sanitizer
payloads in headless Chromium without CSP, openpyxl formula round-trip, race
tests with barriers). Do not pass an item from unit tests alone.

## 6. False-positive check

Run `fp-check` on every candidate finding before it enters a report. Keep only
what survives. One-line each discarded item.

## 7. Other scanners this repo expects

- bandit on `src/`
- gitleaks on full git history
- pip-audit on the resolved dependency set (install the package, then audit)
- trivy on the **built Docker image**, not only the filesystem
- supply-chain collector: `uv run .cursor/skills/supply-chain-risk-auditor/scripts/collect.py`

Record tool versions next to the outputs.

An example read-only run against PR #2 (`a2b873c`) is in
`docs/security-audit-2026-10-09.md` with raw SARIF/JSON under `security-audit/`.
EOF
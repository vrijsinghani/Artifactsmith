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
pip-audit, Trivy, and Docker (for the image scan). `docker` and `trivy image`
need permission to the Docker socket (membership in the `docker` group, or
the equivalent). `uv` is optional for an app-only requirements export and for
the supply-chain collector. Activate `.venv` when this repo has one.

## 1. Semgrep

Install Semgrep. Fetch the Trail of Bits skill, then use its `run-scans.sh`.
Do not hand-write `semgrep` lines.

Third-party rules (trailofbits/semgrep-rules, elttam/semgrep-rules,
apiiro/malicious-code-ruleset) are **moving upstream sources**. The runner
requires `third_party` entries to be `https://` git URLs and shallow-clones
HEAD. Those scans are not pinned. Registry packs (`p/…`) also move.

`run-scans.sh` exits 0 if any single scan succeeds. `partial=true`, `failed`,
`skipped`, `coveredNothing`, and `oversized` in `scans.json` must each be
disposed of before calling a review complete. The wrapper below fails if any
of those are present.

Run this step as written:

```bash
bash scripts/run-security-semgrep.sh
```

The wrapper writes `security-audit/semgrep/rulesets.json` with https URLs
(not local paths), runs `run-scans.sh`, writes cloned commits and tool
versions to `security-audit/semgrep/revisions.json`, warns on stderr when a
clone differs from the revisions listed in `docs/security-audit-2026-10-09.md`,
and runs the completeness check. Fetch uses a file plus exit-status check,
not `mapfile` on a process substitution.

Fetch `sarif-parsing` and follow its `SKILL.md` to summarize the merged SARIF.

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
tests with barriers, stored Markdown in pandoc or GFM). Do not pass an item
from unit tests alone.

## 6. False-positive check

```bash
SKILL=$(bash .cursor/skills/fetch-upstream.sh fp-check)
```

Follow `$SKILL/SKILL.md` on every candidate finding before it enters a report.
Keep only what survives. One-line each discarded item.

## 7. Other scanners

Put outputs under `security-audit/` (gitignored) or attach them as a CI artifact.
Do not commit SARIF or JSON.

bandit and pip-audit exit 1 when they report findings. That means "findings
listed", not "the command failed to run". Read the report.

```bash
mkdir -p security-audit

# bandit 1.9.x on application code (exit 1 = findings)
python3 -m bandit -r src -f json -o security-audit/bandit.json
python3 -m bandit -r src

# gitleaks 8.x on full git history
gitleaks detect --source . --report-format json --report-path security-audit/gitleaks.json

# pip-audit 2.x on the app's resolved runtime requirements, not the review venv.
# uv export if you have uv; otherwise a throwaway venv that only installs the app.
# Exit 1 = findings.
if command -v uv >/dev/null; then
  uv export --no-dev --no-emit-project -o security-audit/requirements.app.txt
else
  python3 -m venv security-audit/app-deps
  security-audit/app-deps/bin/pip install -U pip
  security-audit/app-deps/bin/pip install .
  security-audit/app-deps/bin/pip freeze > security-audit/requirements.app.txt
fi
python3 -m pip_audit -r security-audit/requirements.app.txt --desc

# Optional: the review venv (dev tools). Record as environment findings,
# not as application noise.
# python3 -m pip_audit --desc

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
`docs/security-audit-2026-10-09.md`. Run a fresh review with the commands
above; do not treat that document as a replay of the same scanner bytes.

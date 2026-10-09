# Trail of Bits skills (not in this repo)

These skills are CC-BY-SA-4.0. This MIT repository does not copy them.
Fetch the pinned tree at run time, then follow the upstream `SKILL.md`.

**Source:** https://github.com/trailofbits/skills  
**Pin:** `82fe8226252622fa807643bdca1710901198553a`

```bash
ROOT=$(bash .cursor/skills/fetch-upstream.sh NAME)
```

`$ROOT` is that skill's folder (except `tob`, which prints the clone root).

| Name | Upstream path | How to run after fetch |
|---|---|---|
| `semgrep` | `plugins/static-analysis/skills/semgrep` | `bash "$ROOT/scripts/run-scans.sh" --target "$(pwd)" --output-dir "$OUT" --mode run-all --rulesets "$OUT/rulesets.json"` then `python "$ROOT/scripts/merge_sarif.py" "$OUT/raw" "$OUT/results/results.sarif" --scans "$OUT/scans.json"`. Every `semgrep` line must pass `--metrics=off`. |
| `sarif-parsing` | `plugins/static-analysis/skills/sarif-parsing` | Follow `$ROOT/SKILL.md` on the merged SARIF. |
| `differential-review` | `plugins/differential-review/skills/differential-review` | Follow `$ROOT/SKILL.md` on `git diff main...HEAD`. |
| `sharp-edges` | `plugins/sharp-edges/skills/sharp-edges` | Follow `$ROOT/SKILL.md`. Probe config/compose zero and empty defaults. |
| `supply-chain-risk-auditor` | `plugins/supply-chain-risk-auditor/skills/supply-chain-risk-auditor` | `uv run --no-project "$ROOT/scripts/collect.py" .` then `render.py` as the skill says. |
| `fp-check` | `plugins/fp-check/skills/fp-check` | Follow `$ROOT/SKILL.md` on every candidate finding before it enters a report. |

`fetch-upstream.sh tob` fetches all six into one checkout.

---
title: Publish Markdown Style - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Publish Markdown Style - Plan

## Goal Capsule

**Objective:** An agent that already has page text gets a house-styled page, and an agent that sends bare HTML is told that the page will look unstyled.

**Means:** Open the tool text with that choice (KTD1). Keep markdown publish on the existing share renderer, which already applies the recorded style (KTD2). Warn on the publish result when the HTML has almost no CSS (KTD3).

**Authority:** This plan, then the sanitizer, CSP, and the stored-markdown share hash. A path that restyles submitted HTML or rewrites stored markdown bytes is out of bounds.

**Stop:** Do not merge or deploy. Do not change PDF, DOCX, or XLSX rendering. Do not weaken the sanitizer, response CSP, or share-hash check.

## Product Contract

### Summary

`publish` of hand-written HTML is shown exactly as given. The tool text currently tells agents to call `publish` whenever they have a finished page, and mentions only at the end that `style` does not restyle. A live share of that HTML was an unstyled page. Markdown publish already records `style` (house by default) and the share view already renders that style. The docs should say so first, and bare HTML should come back with a warning.

### Problem Frame

A Cursor agent published bare HTML through `publish` and the public page `/s/9x4ik99zu6zsg97u/` had no ArtifactSmith styling. `create` says "If you already have the finished page, call publish instead". The server instructions say the same. `publish` ends with "style is recorded and does not restyle the page you submitted."

### Requirements

- R1. The create tool text, the publish tool text, the server instructions, the README, and `docs/formats.md` open with the choice: a styled page is Markdown passed to `publish` with `format=markdown`, or `create` with `source_content`. Raw HTML in `publish` is only for a page designed by the caller, and it is shown exactly as given.
- R2. Those texts no longer tell the caller to use `publish` instead of `create` for a finished HTML page.
- R3. `publish` with `format=markdown` records `style` and the opened page uses that style the same way the markdown share view does. Omitted `style` is house. The stored primary stays `document.md`.
- R4. `publish` of HTML that has little or no CSS includes `warning` on the result. HTML that already has CSS, and non-HTML formats, omit `warning`. The job still succeeds.

### Acceptance Examples

- AE1. The create docstring, the publish docstring, and `INSTRUCTIONS` contain the styled-page choice and do not contain "call publish instead". Covers R1, R2.
- AE2. Publish `# Pilot` as markdown with `style=bold`. After the job, the stored file is still markdown, and the share HTML contains the bold title gradient. The same markdown with `style` omitted contains the house title rule and not that gradient. Covers R3.
- AE3. Publish `<html><body><p>Hi</p></body></html>`. The result includes `warning` and the job can still finish. Publish HTML that contains a style block of at least 24 non-whitespace characters, and publish markdown, and neither result has `warning`. Covers R4.

## Planning Contract

### Key Technical Decisions

- KTD1. Rewrite the opening of `INSTRUCTIONS`, the `create` docstring, the `publish` docstring, the README "How it works" and `publish` sections, the styles note, the MCP table row, and the first paragraph of `docs/formats.md`. Leave the `mode` note about "publish that HTML as-is" in place: that sentence chooses full versus fast, it does not tell the agent to call `publish`.
- KTD2. Do not render markdown into stored HTML. `publish_new` and `publish_version` already store `style` (`CFG.default_style`, usually house, when omitted). `read_primary_view` already returns that style, and `markdown_share_html` already calls `render_layout`. The test locks that path. PDF, DOCX, and XLSX stay on their own renderers.
- KTD3. Add `unstyled_html_warning` in `src/artifactsmith/publish_common.py`. It returns a sentence only when `format` is html and the combined `<style>` bodies plus `style=""` values have fewer than 24 non-whitespace characters. Attach `warning` to the publish result before the idempotency row is stored, so a replay returns the same field. Do not add it to the fingerprint. Do not fail the job.

### Assumptions

- The live production page was bare HTML. This plan does not fetch that share.
- "Little CSS" is the 24-character threshold. A page with a real rule does not warn. A page whose CSS the sanitizer later strips is out of scope.
- Slack tools were available and were not searched. External research was skipped: the share renderer is already the pattern to follow.

## Implementation Units

### U1. Tool and doc copy

**Goal:** The first sentences an agent reads name the styled-page choice.

**Files:** `src/artifactsmith/server.py`, `README.md`, `docs/formats.md`, `tests/unit/test_tool_copy.py`

**Approach:** Replace the "call publish instead" openings. State that markdown publish applies `style` on open and that raw HTML is exact. Keep share, status, quota, and safety sentences.

**Test scenarios:** AE1, by reading `INSTRUCTIONS`, both docstrings, and the README. `test_readme_mcp_tools_match_server` must still pass.

### U2. Markdown style on open

**Goal:** A published markdown page opens in the requested style.

**Files:** `tests/unit/test_publish.py` (no production change unless the test shows the stored style is ignored)

**Approach:** Publish, run the job, read the primary, render with `markdown_share_html` using the stored style, and also GET the share URL.

**Test scenarios:** AE2. Stored bytes stay markdown. Bold gradient versus house title rule.

### U3. Unstyled HTML warning

**Goal:** Bare HTML publish tells the caller what will happen.

**Files:** `src/artifactsmith/publish_common.py`, `src/artifactsmith/publish_new.py`, `src/artifactsmith/publish_version.py`, `tests/unit/test_publish.py`, `CHANGELOG.md`

**Approach:** One helper. Both publish entry points add `warning` only when the helper returns text.

**Test scenarios:** AE3. A later version of bare HTML also warns. A styled HTML version does not.

## Verification Contract

- `make lint`
- `make typecheck`
- `make test`
- `make denylist`
- `ruff format --check`

## Definition of Done

- R1 through R4 have the tests named above.
- CHANGELOG has one Fixed bullet.
- CI on the pull request is green.
- The pull request is not merged.

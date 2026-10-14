---
title: Infer omitted publish format - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Infer omitted publish format - Plan

## Goal Capsule

**Objective:** A caller who publishes finished Markdown and leaves `format` blank gets a styled page.

**Means:** Infer the publish format from which body was supplied (KTD1).

**Authority:** The user request wins over this plan. This plan wins over older tool copy that says omitted format is always HTML.

**Stop:** Do not merge or deploy. `create` and `edit` keep their current format rules. Do not restyle submitted HTML or rewrite stored Markdown into HTML.

**Finish:** Tests, changelog, and a draft pull request with green CI.

## Product Contract

### Summary

`publish` treats a blank `format` as HTML, so Markdown with no HTML is rejected before the styled-open path from the merged markdown-style change can run. When `format` is omitted, Markdown and no HTML is markdown and is styled on open. HTML alone stays HTML. Both bodies keep today's behavior, which is HTML as the page on a new artifact.

### Problem Frame

Agents omit `format` and pass Markdown. The call is classified as HTML and fails with `html is required when format is html`.

### Requirements

- R1. A non-blank `format` is unchanged: it must be `html`, `markdown`, `pdf`, `docx`, or `xlsx`, and the matching body is still required.
- R2. When `format` is omitted and Markdown is non-empty and HTML is empty, the format is `markdown`. The recorded style is applied when the page is opened. The stored primary stays `document.md`.
- R3. When `format` is omitted and HTML is non-empty and Markdown is empty, the format is `html`. Bare HTML still returns `warning`. The submitted HTML is not restyled.
- R4. When `format` is omitted and both bodies are non-empty, a new artifact stays `html` (HTML is the page; Markdown is scanned and not rendered). A new version keeps the artifact's current format.
- R5. When `format` is omitted and both bodies are empty, a new artifact stays `html` so the error remains `html is required when format is html`. A new version keeps the current format so the existing body error stays.
- R6. The publish tool text, server instructions, README, and `docs/formats.md` say Markdown is styled automatically. They still say `format=markdown` and that raw HTML is shown exactly as given. They say to pass `format` for pdf, docx, or xlsx.
- R7. `create` and `edit` do not infer format from a body.

### Success Criteria

A markdown-only publish with `format` omitted finishes as a house-styled page. An HTML-only publish with `format` omitted stays HTML. CI is green. The pull request is not merged.

### Scope Boundaries

In scope: `publish` for a new artifact and a new version, the agent-facing copy named in R6, tests, and the changelog.

Out of scope: `create`, `edit`, PDF/DOCX/XLSX renderers, the sanitizer, CSP, share hashing, and merging or deploying.

### Key Decisions

- KD1. Markdown with no HTML is markdown even when the existing artifact is another format. Governs R2.
- KD2. HTML with no Markdown is html even when the existing artifact is markdown. Governs R3.
- KD3. Both bodies keep today's behavior: HTML is the page on a new artifact. Governs R4.

### Outstanding Questions

None.

## Planning Contract

### Key Technical Decisions

- KTD1. One helper in `src/artifactsmith/publish_common.py` chooses the format for both `publish_new` and `publish_version`. A non-blank request goes through `requested_format`. A blank request uses the body rules in R2–R5. Whitespace-only text counts as absent.
- KTD2. Both non-empty bodies on a version keep `requested_format(current, None)`. Forcing HTML on that path would change today's version behavior. The new-artifact path is the one that is HTML-primary.
- KTD3. The fingerprint already stores `format`. The inferred value is that field. The result dict still has no `format` key. Tests read `get_artifact` after a successful job, or the version row's format before the job finishes.
- KTD4. Copy keeps the strings `format=markdown` and `exactly as given` so the existing tool-copy test still holds, and adds that Markdown is styled automatically.

### High-Level Technical Design

| Requested format | HTML body | Markdown body | Artifact | Result |
|---|---|---|---|---|
| non-blank | any | any | any | the requested format |
| omitted | empty | present | new or version | markdown |
| omitted | present | empty | new or version | html |
| omitted | present | present | new | html |
| omitted | present | present | version | current format |
| omitted | empty | empty | new | html |
| omitted | empty | empty | version | current format |

### Assumptions

- A string that is only whitespace is the same as a missing body.
- `create` and `edit` stay on their current format rules. The user named `publish`.
- The unstyled-HTML warning text can keep telling the caller about `format=markdown`.
- The quota test must not let a successful inferred publish consume the single build slot it asserts.

### Sequencing

U1 before U2. U2's copy describes the behavior U1 implements.

## Implementation Units

### U1. Infer publish format

**Goal:** Blank `format` follows R2–R5 on both publish paths.

**Requirements:** R1, R2, R3, R4, R5.

**Dependencies:** none.

**Files:**
- `src/artifactsmith/publish_common.py`
- `src/artifactsmith/publish_new.py`
- `src/artifactsmith/publish_version.py`
- `tests/unit/test_publish.py`

**Approach:**
- Add the helper next to `require_body` and call it where each path currently picks `fmt`.
- Drop the local supported-format check in `publish_new` when `requested_format` already rejects an unknown explicit format.
- Leave `require_body`, the warning helper, style selection, and the worker renderer alone.
- Move the markdown-only rejection out of the quota test so that test still raises before any job is queued.

**Patterns to follow:** `requested_format` in `src/artifactsmith/service.py`. `test_publish_markdown_style_renders_on_open` in `tests/unit/test_publish.py`.

**Test scenarios:**
- Markdown only, `format` omitted, new artifact: artifact format is markdown, style is house, no warning, primary is `document.md`, opened HTML contains the house rule and not the bold gradient.
- HTML only, `format` omitted: format is html and bare HTML has `warning`.
- Both bodies, `format` omitted, new artifact: format is html and the primary is `index.html`.
- Version of a markdown artifact, both bodies, `format` omitted, after the first job is done: the new version stays markdown.
- Version of an HTML artifact, markdown only, `format` omitted: the new version is markdown.
- Explicit `format=html` with only Markdown raises `html is required`.
- Explicit `format=pdf` with no Markdown raises `markdown is required when format is pdf`.
- Whitespace HTML plus Markdown is markdown.
- Publishing with neither body still raises `html is required`, and the quota test's single successful HTML publish still succeeds before the next one hits quota.

**Verification:** `pytest tests/unit/test_publish.py -o addopts=`

### U2. Say Markdown is styled automatically

**Goal:** Agent-facing copy matches R6.

**Requirements:** R6, R7.

**Dependencies:** U1.

**Files:**
- `src/artifactsmith/server.py`
- `README.md`
- `docs/formats.md`
- `tests/unit/test_tool_copy.py`
- `CHANGELOG.md`

**Approach:**
- Publish instructions, the publish docstring, the README publish section, and `docs/formats.md` say Markdown is styled automatically when `format` is omitted and no HTML is passed.
- Say that passing HTML as well keeps HTML as the page unless `format` is set, and that pdf, docx, and xlsx need an explicit `format`.
- On a new version, say markdown-only becomes markdown, HTML-only becomes html, and both bodies keep the artifact format.
- Keep `format=markdown` and `exactly as given` in each text the tool-copy test checks.
- Add a changelog Fixed bullet above the existing markdown-style bullet. Do not remove that bullet.
- Do not change the `create` or `edit` format-selection code. The create docstring may point at publish for a styled page, and must still mention `format=markdown` and `exactly as given`.

**Test scenarios:**
- Instructions, create docstring, publish docstring, README, and `docs/formats.md` still contain `format=markdown` and `exactly as given`, and do not contain `call publish instead`.
- The publish docstring says Markdown is styled automatically.
- The publish docstring still mentions `warning`.

**Verification:** `pytest tests/unit/test_tool_copy.py -o addopts=`

## Verification Contract

- `make lint`
- `make typecheck`
- `make denylist`
- `make test`
- `ruff format --check`

## Definition of Done

- U1 and U2 behaviors hold.
- Changelog has the new Fixed bullet.
- Draft pull request against `main` is open and CI is green.
- The pull request is not merged and nothing is deployed.

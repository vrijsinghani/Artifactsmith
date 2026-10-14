---
title: Update display name on a new version - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Update display name on a new version - Plan

## Goal Capsule

**Objective:** After a new version finishes, the catalog card and the share page name that version's title.

**Means:** Update `artifacts.display_name` when the version succeeds, and stamp that name onto the served page's `<title>` and Open Graph title (KTD1).

**Authority:** The user request wins over this plan. This plan wins over the current rule that `display_name` is fixed.

**Stop:** Do not merge or deploy. Do not change the slug unless a caller passes a new slug. A failed version does not rename the artifact.

**Finish:** Tests, changelog, and a draft pull request with green CI.

## Product Contract

### Summary

A new edit or publish can change the page title while `artifacts.display_name` stays on the name from the first version. The catalog card and share unfurl keep the old name. The page's own title is the new one.

### Problem Frame

`/s/ryg84tem92qzp4xm/` shows the card "V11 Appendix B draft answers v2 (for Vikas, not sent)" while the page title is "Appendix B answers for Vinnie (draft)".

### Requirements

- R1. When a new version finishes and the caller passed a non-blank `display_name`, the artifact's `display_name` becomes that name.
- R2. When a new version finishes and the caller did not pass `display_name`, and the page's own title differs from the current `display_name`, the artifact's `display_name` becomes that page title.
- R3. The slug stays the same. A new title does not derive a new slug.
- R4. The catalog card, inspect title, and list title use the updated `display_name`.
- R5. The share page for the latest done version uses that `display_name` as `<title>` and `og:title`.
- R6. A version that fails does not change `display_name`.

### Success Criteria

Publishing or editing a new title, then opening the public share, shows the new name on the card, in `<title>`, and in `og:title`. The slug is unchanged.

### Scope Boundaries

In scope: `edit`, `publish` of a new version, the catalog card, and the HTML served at `/s/` and `/p/` for the latest done version.

Out of scope: renaming the slug, changing a pinned older version's stored bytes, and merging or deploying.

### Key Decisions

- KD1. An explicit `display_name` wins over the page title. Governs R1, R2.
- KD2. The slug is not rewritten from the new title. Governs R3.

### Assumptions

- A blank or whitespace `display_name` counts as omitted.
- The page title is the HTML `<title>`, otherwise the first Markdown `#` heading, including a `content.md` sidecar for PDF, DOCX, and XLSX.
- The rename is applied when the version becomes `done`, in the same update that records the new format.
- An older pinned share keeps the title stored in that version's file. Only the latest done version is stamped with the artifact's current `display_name`.

## Planning Contract

### Key Technical Decisions

- KTD1. Store an optional requested name on the version row (`requested_name`). On success, use it when it is non-blank. Otherwise read the title from the rendered files. Update `artifacts.display_name` only when the chosen name differs.
- KTD2. Do not rewrite stored bytes after the share hash is recorded. Align `<title>` and `og:title` when the share or preview HTML is served.
- KTD3. `publish` stops rejecting a changed `display_name`. `edit` accepts an optional `display_name`. Both include it in the idempotency fingerprint.

### High-Level Technical Design

| Caller name | Page title vs current name | Result when the version is done |
|---|---|---|
| non-blank | any | artifact name becomes the caller name |
| omitted | different | artifact name becomes the page title |
| omitted | same or missing | artifact name stays |
| any | version failed | artifact name stays |

### Sequencing

U1 before U2. U2's copy describes U1.

## Implementation Units

### U1. Rename on a finished version

**Goal:** `edit` and `publish` update `display_name` under R1, R2, R3, and R6, and the latest share HTML uses that name for `<title>` and `og:title`.

**Requirements:** R1, R2, R3, R4, R5, R6.

**Dependencies:** none.

**Files:**
- `src/artifactsmith/db.py`
- `src/artifactsmith/service.py`
- `src/artifactsmith/publish_version.py`
- `src/artifactsmith/server.py`
- `tests/unit/test_publish.py`
- `tests/unit/test_edit_version.py`

**Approach:**
- Add nullable `requested_name` on `versions`.
- Validate a passed name with the existing display-name check before the job is queued.
- On build success, choose the name and update the artifact row. Leave the slug column alone.
- When opening the latest done version as HTML, set `<title>` and `og:title` to the artifact `display_name`.

**Patterns to follow:** `artifacts.format` is updated in `_execute_build` only after the files are stored. `publish_version` already receives `display_name` and currently rejects a change.

**Test scenarios:**
- Publish version 2 with a new `<title>` and no `display_name`. After the job, the artifact name is the new title, the slug is unchanged, `list` and the status card use it, and `GET /s/{id}/` contains that `<title>` and `og:title`.
- Publish version 2 with an explicit `display_name` that differs from the HTML `<title>`. The artifact name, `<title>`, and `og:title` are the explicit name. The slug is unchanged.
- Publish version 2 whose title matches the current name. The artifact name stays.
- A publish that fails the secret check does not change `display_name`.
- Edit whose finished HTML has a new `<title>`, with no `display_name`, updates the artifact name and leaves the slug.
- Edit with an explicit `display_name` uses that name even when the HTML `<title>` differs.

**Verification:** `pytest tests/unit/test_publish.py tests/unit/test_edit_version.py -o addopts=`

### U2. Document the rename

**Goal:** Tool text and the changelog match R1–R3.

**Requirements:** R1, R2, R3.

**Dependencies:** U1.

**Files:**
- `src/artifactsmith/server.py`
- `README.md`
- `CHANGELOG.md`

**Approach:**
- Say that a new version may pass `display_name`, and that an omitted name follows the page title when that title changed.
- Say the slug stays the same.
- Keep the existing `format=markdown` and `exactly as given` sentences.
- Add a changelog Fixed bullet.

**Test scenarios:**
- The publish docstring says a new version can set `display_name` and that the slug stays the same.
- Existing tool-copy assertions still pass.

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

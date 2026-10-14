---
title: Rebase publish readback onto main - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Rebase publish readback onto main - Plan

## Goal Capsule

**Objective:** A reviewer can take the open publish-readback pull request onto current main and still read a published page back, keep its title and note, and open an existing public link on the latest done version.

**Means:** Replay `cursor/publish-readback-note-9550` onto `8dc0bfe` and keep both behaviors where the histories touch the same lines (KTD1).

**Authority:** This plan, then the existing readback behavior on that branch and the floating-share behavior already on main. A resolution that drops either one is out of bounds.

**Stop:** Do not merge or deploy. Do not change the share-hash check, the sanitizer, or CSP. Do not fix the versioned unshare state, and do not add `compose.prod.yaml`, in this pull request.

**Who ships:** The PM reviews the updated pull request and ships it. This work leaves the pull request open and ready for review.

## Product Contract

### Summary

Pull request #41 still carries page readback, title fidelity, safety-change labels, and an optional publish note. Main at `8dc0bfe` serves the latest done version from an existing public link. The branch does not contain that commit, so GitHub reports a conflict. After the replay, both behaviors are present and the checks are green.

### Problem Frame

The branch was cut before the floating-share change. Replaying it onto main stops on two overlapping edits, in the changelog's Fixed list and in the schema migration. Other files changed on both sides in different places. A replay that keeps only one side would drop either the note column or the pinned-share column, or would drop one of the two changelog bullets.

### Requirements

**Both behaviors survive**

- R1. After the replay, the branch contains `8dc0bfe` and the readback commits, and pull request #41's head is that branch.
- R2. `inspect` can still return the stored text of a done version when content is requested, a published title keeps its spaces, an optional note is stored and shown, and a done publish lists the safety edits that changed the page.
- R3. An existing public `/s/` link still serves the latest done version. A pinned link stays on its version. The page is still 404 when the served file does not match its recorded sha256.

**Checks**

- R4. The share-hash check, the HTML sanitizer, and the share CSP are not edited by the conflict resolution.
- R5. Lint, typecheck, unit tests, and the denylist pass on the rebased head. The pull request stays open.

### Actors

- A1. A reviewer who needs pull request #41 to apply on current main.
- A2. A caller who publishes a page and then inspects it.
- A3. A reader who already has a public link.

### Flows

- F1. The branch replays onto `8dc0bfe`. Inspect of a published page still returns requested text, the note, and safety changes, and the original share URL serves a later done version.
- F2. A database created before either column exists gains both the note column and the pinned column when it opens.

### Acceptance Examples

- AE1. A done HTML page is shared with no version, a second done version is stored, and the original `/s/` URL returns the second version. Covers R3.
- AE2. A published page with a note and a title that contains a space is inspected with content requested. The text, the note, and the title are present, and safety changes are listed when the page was edited by the safety pass. Covers R2.
- AE3. Opening a database that predates both columns adds `note` on versions and `pinned` on shares. Covers F2.

### Success Criteria

- Pull request #41 is open, its head contains `8dc0bfe`, and GitHub CI is green.
- The pull request is not merged.

### Scope Boundaries

- Conflict resolution that preserves R2 and R3 is in scope. New product behavior is not.
- The versioned `unshare` state bug, `compose.prod.yaml`, placeholder-secret and edit-error work, and inline raster images are separate pull requests.
- Do not merge or deploy.

#### Deferred to Follow-Up Work

- Return the live share state from `unshare` when a version pin is revoked and the follow-latest link remains.
- Correct the production compose documentation and add a sanitized `compose.prod.yaml`.

### Key Decisions

- KD1. Land the existing pull request by replaying its commits onto current main. Governs R1.

## Planning Contract

### Key Technical Decisions

- KTD1. Where the changelog and the schema migration edit the same lines, keep both additions: the follow-latest bullet and the title/safety bullet, and both column migrations. A fresh database's create statements must include `note` on versions and `pinned` on shares. Dropping either side would fail R2 or R3.
- KTD2. Where README, the MCP tool module, and the service module changed in different regions, keep the replay's automatic combination. After the replay, restore a sentence only if the follow-latest share wording from main or the inspect, note, or safety-change wording from the branch is missing.

### Assumptions

- Updating the open pull request replaces the branch tip with the replayed history. That is the update the request asked for.
- No new product test is required when the existing share tests and the existing readback tests both pass on the combined tree. A failure that is only fallout from the replay is fixed in place.

### Sequencing

U1 produces the combined tree. U2 always runs the Verification Contract checks on that tree. U2 edits a file only when a check fails or a sentence KTD2 names is missing.

## Implementation Units

### U1. Replay the branch and keep both behaviors

**Goal:** The branch contains current main and the readback commits, with both schema columns and both changelog bullets.

**Requirements:** R1, R2, R3, R4

**Dependencies:** none

**Files:**
- `CHANGELOG.md`
- `README.md`
- `src/artifactsmith/db.py`
- `src/artifactsmith/server.py`
- `src/artifactsmith/service.py`

**Approach:**
- Replay the branch onto `8dc0bfe`.
- In the changelog Fixed list, keep the follow-latest bullet and the title, linkify, and safety-change bullet.
- In the schema migration, run the versions `note` addition and the shares `pinned` addition. Confirm the create statements for a new database include both columns.
- Read the share tool text, the inspect tool text, and the README share paragraph. Restore a dropped sentence per KTD2. Do not edit the hash check, sanitizer, or CSP.

**Patterns to follow:** The migration style already in `src/artifactsmith/db.py`, which adds a column only when `PRAGMA table_info` does not list it.

**Test scenarios:**
- Happy path: share a done page with no version, store a later done version, and the original public URL returns the later body. Covers AE1.
- Happy path: publish a page whose title contains a space and whose note is non-empty, request content on inspect, and the text, note, and title are present. Covers AE2.
- Edge: open a database file created before either column. Versions have `note` and shares have `pinned`. Covers AE3.
- Error: replace the stored primary of the served version with different bytes. The public URL is 404 and does not contain the replacement.
- Integration: the inspect result for a shared artifact still includes share state, and a floating link is the one reported when it is live.

**Verification:** The branch tip has `8dc0bfe` as an ancestor. `note` and `pinned` are both in the schema. The README still says a public URL follows the latest done version, and it still describes optional content on inspect.

### U2. Fix replay fallout only

**Goal:** The combined tree passes the repo checks without a product change.

**Requirements:** R4, R5

**Dependencies:** U1

**Files:**
- `tests/unit/test_server.py`
- `tests/unit/test_service.py`
- `tests/unit/test_publish.py`
- `tests/unit/test_db.py`
- any file a failing check names, limited to making the combined tree consistent with R2 and R3

**Approach:**
- Run the unit tests that cover shares and publish readback first, then the full unit suite, lint, typecheck, and the denylist.
- If a failure is a signature or column mismatch introduced by the replay, fix that call or migration so both behaviors stay. Do not weaken an assertion.

**Test scenarios:**
- Happy path: `test_floating_share_serves_the_latest_done_version` and `test_publish_note_is_on_inspect_and_rejected_before_a_row` pass together.
- Edge: `test_note_column_migrates` and `test_migrate_copies_artifact_format_onto_versions` pass on the combined schema.
- Error: `test_floating_share_hash_mismatch_is_404_and_stale_row_hash_is_not` still returns 404.
- Integration: the full unit suite passes, not only the two feature files.

**Verification:** Lint, typecheck, the denylist, and the unit suite pass. No assertion is relaxed.

## Verification Contract

- `make lint`
- `make typecheck`
- `make test`
- `make denylist`
- GitHub CI on the updated pull request: lint and types, unit tests on Python 3.11 and 3.12, docker image, dependency scan, compose smoke, bind-address smoke

## Definition of Done

- U1 and U2 are complete, and abandoned conflict leftovers are not in the diff.
- Pull request #41 is open against main, its head contains `8dc0bfe`, and CI is green.
- The pull request is not merged and nothing is deployed.

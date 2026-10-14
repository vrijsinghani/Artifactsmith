---
title: Unshare state and production compose - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Unshare state and production compose - Plan

## Goal Capsule

**Objective:** After an owner revokes one pinned share, the tool tells them the public link that is still live, and an operator can see how production compose is layered and how to roll back the `pinned` column.

**Means:** Return the remaining share state from `unshare`, correct the operations doc, and add a compose overlay that contains no secret values (KTD1, KTD2).

**Authority:** This plan. The share-hash check, sanitizer, and CSP stay as they are.

**Stop:** Do not merge or deploy. Do not commit secret values. Do not change which links `unshare` revokes.

**Who ships:** The PM reviews the pull request. This work leaves it open.

## Product Contract

### Summary

`unshare` with a version revokes only that pin, then always reports `not_shared` even when the follow-latest link is still public. Operations also says this repo has no `compose.prod.yaml`. Production does layer that file on `compose.yaml`. A rollback of `8dc0bfe` needs the `pinned` column dropped first, because the previous insert is positional.

### Problem Frame

The versioned unshare path updates pinned rows and then returns a fixed `not_shared`. The floating row is untouched, so the caller is told the artifact is private while `/s/{sid}/` still serves the latest done version. The operations page was written when the production compose file was not in the repo. That file is how production is started, and it must not carry secret values.

### Requirements

**Share state**

- R1. `unshare(version=N)` still revokes only pinned links for that version. The follow-latest link stays.
- R2. The return `state` is `shared` when any public link for the artifact is still live, and `not_shared` only when none is. A still-live link includes `url` and `follows`.
- R3. `unshare` with no version, or with version `0`, still revokes every public link and returns `not_shared`.

**Operations**

- R4. `docs/operations.md` says production is `docker compose -f compose.yaml -f compose.prod.yaml`, with secrets and hostnames coming from `.env`.
- R5. `compose.prod.yaml` is in the repo, layers on `compose.yaml`, and contains no secret values, hostnames, or keys.
- R6. Operations documents that rolling back past `8dc0bfe` requires `ALTER TABLE shares DROP COLUMN pinned` on `/data/state.sqlite` before the previous image starts, because that image inserts into `shares` with a positional value list.

### Scope Boundaries

- Do not change the share-hash check, sanitizer, or CSP.
- Do not invent production hostnames or keys. The live compose file is not on this machine; the committed overlay is the sanitized layer, not a copy of an unseen file.
- Do not merge or deploy.

### Success Criteria

- The unit test for a pinned unshare sees `state` `shared` while the floating URL still returns 200.
- Lint, typecheck, the denylist, and the unit suite pass.

## Planning Contract

### Key Technical Decisions

- KTD1. After the revoke, call the same share-state lookup inspect uses. If that lookup is `not_shared`, look for any other live pin before reporting `not_shared`. Do not change the revoke SQL.
- KTD2. `compose.prod.yaml` only sets `restart: unless-stopped` on `server`. Every secret and public URL stays in `.env`, which `compose.yaml` already interpolates. No literal values are added.

### Assumptions

- The live production overlay was not available here. If it has services beyond a restart policy, the owner pastes the current file and this overlay is replaced with a sanitized copy.
- A code change on the current insert cannot make the already-released positional insert tolerate `pinned`. The rollback step is the `DROP COLUMN`.

## Implementation Units

### U1. Remaining share state

**Goal:** Versioned unshare reports the link that is still public.

**Requirements:** R1, R2, R3

**Dependencies:** none

**Files:**
- `src/artifactsmith/service.py`
- `src/artifactsmith/server.py`
- `README.md`
- `CHANGELOG.md`
- `tests/unit/test_server.py`

**Approach:**
- Keep the existing revoke statements.
- Build the return from the remaining live share, floating first.
- Say in the tool text and the README that `state` is the remaining share, not always `not_shared`.

**Patterns to follow:** `_share_state` in `src/artifactsmith/service.py`.

**Test scenarios:**
- Happy path: share with no version, pin version 1, unshare version 1. The result `state` is `shared`, `follows` is `latest`, the floating URL is 200, and the pin URL is 404. Covers R1, R2.
- Edge: unshare with no version after that. `state` is `not_shared` and `revoked_links` counts the floating row. The floating URL is 404. Covers R3.
- Error: unshare a version that has no pin while the floating link is live. `revoked_links` is 0 and `state` is still `shared`.

**Verification:** The new assertions pass, and a full unshare still leaves no public URL.

### U2. Production compose and rollback

**Goal:** The repo shows the production compose layer and the `pinned` rollback.

**Requirements:** R4, R5, R6

**Dependencies:** none

**Files:**
- `docs/operations.md`
- `compose.prod.yaml`
- `CHANGELOG.md`

**Approach:**
- Replace the sentence that says the file is absent.
- Add the overlay with no secret values.
- Document the `DROP COLUMN` command against `/data/state.sqlite`, run before the previous image serves traffic.

**Test scenarios:**
- Test expectation: none -- documentation and a compose overlay with no runtime behavior. The denylist scan is the check that no secret-shaped value was committed.

**Verification:** `compose.prod.yaml` has no assigned secret literals. Operations names the two-file compose command and the rollback SQL.

## Verification Contract

- `make lint`
- `make typecheck`
- `make test`
- `make denylist`

## Definition of Done

- U1 and U2 are complete.
- The pull request is open against main and CI is green.
- The pull request is not merged and nothing is deployed.

---
title: Bare Token, Needs-Input Errors, Bold Sheet - Plan
type: fix
date: 2026-10-15
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Bare Token, Needs-Input Errors, Bold Sheet - Plan

## Goal Capsule

- **Objective:** A realistic ArtifactSmith token fails the secret check with or without `Bearer`, a `needs_input:` check failure keeps its reason without hosts or credentials, and the bold stylesheet has room for the next rule.
- **Means:** Add one labeled pattern on the existing secret views (KTD1). Peel `needs_input:` inside the check-error redactor from #45 (KTD2). Drop newlines between stylesheet rules (KTD3).
- **Authority:** This plan, then the brief. A later idea that flags the named placeholders, drops the `needs_input:` prefix, or raises the 8192-byte cap loses.
- **Execution:** One new pull request from `main` `f6b485a`. Draft. The product owner reviews and merges.
- **Stop:** Do not merge or deploy. Do not change token minting, link policy, or the 8192-byte cap.

## Product Contract

### Summary

A bare `asmb_` token publishes because only the bearer check knows that prefix. Check problems that start with `needs_input:` skip the host and URL redactor and land in `jobs.error`. The bold sheet is 8185 of 8192 bytes.

### Problem Frame

`SECRET_PATTERNS` has no `asmb_` rule. `generic_bearer` runs only after `Bearer`. `_secret_views` already strips zero-width characters before every labeled pattern. `_job_check_error` drops a host or HTML attribute value only when the problem starts with that check name, so a `needs_input:` prefix keeps the private host and `user:password@` URL. `stylesheet()` concatenates rule blocks that still contain newlines. `tinycss2` is already a dependency; serializing with it leaves media-query spaces and saves less than dropping newlines. No CSS minifier package is added.

### Key Decisions

- Realistic body means `asmb_` plus at least 43 `[A-Za-z0-9_-]` characters, the length of `secrets.token_urlsafe(32)`. No digit requirement, so a minted token with no digit still fails. Governs R1 and R2.

### Requirements

**Secret scanner**

- R1. `asmb_` followed by 43 or more urlsafe characters is `possible secret (artifactsmith_token)` when bare, after `Bearer`, inside a code fence, and inside a URL.
- R2. The same token still fails when U+200B, U+200D, U+200C, or U+FEFF is inside it.
- R3. These stay clean: `asmb_...`, `asmb_…` with nothing after the ellipsis, `Bearer <token>`, `YOUR_TOKEN`, `$AM_TOKEN`, `${AM_TOKEN}`.

**Needs-input errors**

- R4. A check failure prefixed `needs_input: ` keeps that prefix and the check name. Hosts, raw URLs, and credentials are absent from the stored job error.
- R5. The redactor still names an unprefixed check and still stores only the exception class for a build failure that is not a content-check message.

**Bold sheet**

- R6. The bold stylesheet stays under 8192 bytes, with more than 7 bytes of room, and its rules are unchanged aside from newlines between rules.

### Scope Boundaries

- Do not flag a body shorter than 43 characters. Existing test tokens stay valid.
- Do not add a second scanner. The new pattern is one `SECRET_PATTERNS` entry, so it sees every existing normalization view.
- The commit denylist gains the same length floor, without lookaround, so a real token cannot be committed. Tests build the body by concatenation.
- `_needs_input` still stores the model's missing sentence. This work redacts check problems, not that sentence.
- Do not raise the 8192 cap. Do not change colors, selectors, or declaration text.

### Acceptance Examples

- AE1. Covers R1–R3. A 43-character body fails on each surface in R1 and R2. Each R3 string produces no secret finding.
- AE2. Covers R4, R5. A job whose build error lists `needs_input:` private hosts `10.1.2.3`, `192.168.4.20`, and `localhost`, plus `https://user:pass@host/path`, stores the check names and none of those values. The existing unprefixed cases stay as they are.
- AE3. Covers R6. Bold's encoded length is below 8192. The phone-width code-box assertions still match.

## Planning Contract

### Key Technical Decisions

- KTD1. Label `artifactsmith_token`. Pattern: `(?<![A-Za-z0-9_])asmb_[A-Za-z0-9_-]{43,}(?![A-Za-z0-9_-])`. Trailing negative lookahead, not `\b`, because a token may end in `-`. The commit denylist uses `(^|[^A-Za-z0-9_])asmb_[A-Za-z0-9_-]{43,}`. A 43-character identifier with this prefix fails the build; that is the residual.
- KTD2. In `_job_check_error`, when a problem starts with `needs_input:`, strip that prefix, run the existing HTML-attribute and host/URL drop, then put `needs_input: ` back. Do not add a second scrubber.
- KTD3. `stylesheet()` returns the same concatenation with newlines removed. Newlines in this sheet sit between rules. `content` values do not contain a newline. `tinycss2.serialize` was measured and left `@media` indentation, so it is not the minifier.

### Assumptions

- Create, edit, and fast layout store content-check failures through `_job_check_error`. Publish's `AMError` path is unchanged.
- Bold was 8185 bytes before this change. Newline removal is the headroom.

### Sequencing

U1, U2, and U3 are independent. One pull request.

## Implementation Units

### U1. ArtifactSmith token pattern

- **Goal:** A realistic `asmb_` token fails, and the named placeholders pass.
- **Requirements:** R1, R2, R3
- **Dependencies:** none
- **Files:** `src/artifactsmith/renderers/safety.py`, `scripts/denylist.sh`, `tests/unit/test_bearer_secrets.py`, `CHANGELOG.md`
- **Approach:** Add the KTD1 pattern to `SECRET_PATTERNS`. Mirror the length floor in `SECRET_RE`. Build fixture bodies by concatenation.
- **Patterns to follow:** `tests/unit/test_bearer_secrets.py` checks `find_secrets`, `check_fields`, and `check_content` for HTML and Markdown.
- **Test scenarios:** Bare token, `Bearer` plus token, fenced token, URL token, and each invisible character inside the token report `artifactsmith_token`. Each R3 string is clean. A 42-character body is clean.
- **Verification:** Those cases pass, and `make denylist` passes.

### U2. Redact needs_input check problems

- **Goal:** Stored job errors keep `needs_input:` and the check name, without hosts or credentials.
- **Requirements:** R4, R5
- **Dependencies:** none
- **Files:** `src/artifactsmith/service.py`, `tests/unit/test_service.py`, `CHANGELOG.md`
- **Approach:** Apply KTD2 in `_job_check_error`.
- **Patterns to follow:** `test_edit_failure_names_the_check_without_source`.
- **Test scenarios:** One edit whose `BuildError` carries the four `needs_input:` problems stores each check name and does not store `10.1.2.3`, `192.168.4.20`, `localhost`, `user:pass`, or `https://user:pass@host/path`. The existing unprefixed cases stay green.
- **Verification:** The stored `jobs.error` matches that.

### U3. Newline-strip the stylesheet

- **Goal:** Bold has room under 8192 bytes without a visual rule change.
- **Requirements:** R6
- **Dependencies:** none
- **Files:** `src/artifactsmith/layout/css.py`, `CHANGELOG.md`
- **Approach:** Apply KTD3. Do not edit the cap or the rule text.
- **Patterns to follow:** `test_body_breaks_only_on_word_boundaries` pins the code-box declarations.
- **Test scenarios:** Existing length and substring assertions stay green. Record bold's byte length before and after in the pull request.
- **Verification:** Bold is under 8192 and the pinned declarations still match.

## Verification Contract

| Check | What it proves |
|---|---|
| `tests/unit/test_bearer_secrets.py` and `tests/unit/test_service.py` | R1–R5 |
| `tests/unit/test_layout.py` | R6 |
| `make denylist` | A contiguous 43-character `asmb_` body is not tracked |
| `make lint`, `make typecheck`, `make test` | Repo gates |

## Definition of Done

- R1 through R6 hold.
- `CHANGELOG.md` has a `### Fixed` line for each fix.
- The pull request is one draft, lists each fix with its tests, and reports the bold sheet size before and after.
- The diff has no unrelated files. It is not merged.

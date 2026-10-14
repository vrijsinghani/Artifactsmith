---
title: Publish Readback and Note - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Publish Readback and Note - Plan

## Goal Capsule

**Objective:** A caller who published a finished page can read that version's text back from their own workspace, sees the title they typed, is not given surprise links on an as-is HTML page, can see what the safety pipeline still changed, and can attach a note that inspect shows beside the generic publish sentence.

**Means:** Opt-in content on the existing inspect call (KTD1). Escape the document title at render and skip HTML autolink only on publish (KTD2, KTD3). List the remaining byte changes after the job finishes (KTD4). Store the note beside the generic sentence (KTD5).

**Authority:** This plan, then the repository's sanitizer, CSP, link policy, auth, and tokens. A shorter path that skips those checks is out of bounds.

**Stop:** Do not merge or deploy. Do not weaken the sanitizer, CSP, link policy, auth, or tokens. Do not change `emit_href`. Do not add a model call. Do not change the CLI.

**Who ships:** The PM reviews the pull request and ships it. This work opens the pull request ready for review.

---

## Product Contract

### Summary

`inspect` returns metadata and hashes, so a caller cannot read back the page they just published. `publish` also rewrites the page: it linkifies bare URLs in HTML, and the document title is written into the page without HTML escaping. The safety wrappers stay. Publish requests show up in inspect only as "Published a finished page." After this change, inspect can return the stored text on request, an as-is HTML page keeps bare URLs as text, the title is stored as given and escaped only in the document, every remaining change is listed once the job is done, and an optional note is stored and shown.

### Problem Frame

Reported 2026-10-14 while publishing `art_cv2yirbmxr3rok`. That artifact is not in this tree. The caller could not check the stored page from `inspect`, saw the title and links change, and could not tell a publish apart from any other publish in history.

The display name is not percent-encoded today. The document title is. `nh3.clean_text` rewrites a space to `&#32;` and escapes markup, and that string is written straight into `<title>`. Escaping it again would store `My&amp;#32;Report`. The only space-to-`%20` rewrite is `emit_href`, which is link policy and stays.

### Key Decisions

- **Read the stored page through `inspect`, not a new tool.** The user named both. The registered tool set is locked to the README table, and optional arguments are the existing shape. Governs R1.

### Requirements

**Readback**

- R1. A caller with `read` can pass `include_content` on `inspect` and receive the stored text of the target version. The default result does not include that text. A different workspace still gets `forbidden: the artifact belongs to a different workspace` and no page bytes.
- R2. Text is included only when that text is at most 2,000,000 characters, the same cap publish already enforces. Above that, the result names `size_bytes` and omits the text. The text is never truncated. The cap applies to the text that would be returned, including `content.md` for PDF, DOCX, and XLSX.
- R3. PDF, DOCX, and XLSX do not return the binary primary. When stored `content.md` exists, that text is returned and the result says the primary is binary. When it does not, the text is omitted.
- R4. A failed store read of the opted-in file sets `content_error` to `store_read_failed` and omits the text. A version that is not done, or a missing `content.md`, omits the text and does not set that error.

**Publish fidelity**

- R5. The title the caller gave is stored and returned as given, including spaces. Those characters are escaped only when written into the document title. The title is not percent-encoded.
- R6. A publish-as-is HTML page does not turn bare URLs in text into links. Markdown publish keeps the existing linkify pass, because that pass is what makes a bare private URL fail closed.
- R7. The safety wrappers stay: the HTML sanitizer, the export CSP meta, remote-image rewrite, external-anchor hardening, and the content checks. Fast-layout table wrappers stay on fast layout and are not added to a publish-as-is page.
- R8. After a publish job is done, `status` and `inspect` list every change that altered the submitted page. The immediate `publish` return stays the queued ids and does not wait for that list.

**Note**

- R9. `publish` accepts an optional `note`. When it is present and non-empty, `inspect` shows it beside `Published a finished page.` Omitting it leaves inspect showing only that sentence.
- R10. A note that is too long, or that fails the secret, private-link, or allow-list checks, is rejected before a version row exists.
- R11. The note is not the sentence a later edit sends to the model as prior user text. That sentence stays `Published a finished page.`
- R12. A second publish of the same page with a different note does not replay the first job. The same idempotency key with a different note is the existing conflict error.

**Docs**

- R13. Tool text for `inspect` and `publish` states the opt-in read, the change list, and the note. The changelog records the behavior.

### Actors

- A1. A caller with `read`, who inspects and polls status.
- A2. A caller with `publish`, who submits a finished page and an optional note.

### Flows

- F1. A2 publishes a page and an optional note, receives queued ids, then polls `status` until done and sees the change list.
- F2. A1 calls `inspect` with `include_content` and receives the stored text plus the note and the change list. Without the flag, A1 still receives the note and the change list, and does not receive page text.

### Acceptance Examples

- AE1. `include_content` true on the caller's own done HTML version returns the stored text. False or omitted does not add a content field. Covers R1.
- AE2. The same call from another workspace is forbidden and contains no page text. Covers R1.
- AE3. Stored text one character over the 2,000,000 character cap names `size_bytes` and omits the text. A page of multi-byte characters that is still under that character cap is returned. Covers R2.
- AE4. An HTML publish whose document title is `My Report` stores and returns that title with the space. A bare `https://example.com/doc` in that page stays text. The done status lists the safety changes that fired, including the CSP meta when the submission lacked it, and does not list `linkified_url`. Covers R5, R6, R7, R8.
- AE5. Note `checked the harbor table` appears on inspect beside `Published a finished page.` A later edit's prior request text is still that sentence. A note that names a private host is rejected before a row exists. Covers R9, R10, R11.

### Scope Boundaries

- `emit_href` and its space-to-`%20` rule stay.
- Markdown linkify stays.
- The sanitizer allowlist, export CSP text, and response CSP stay.
- Fast-layout table wrappers stay. They are not added to publish-as-is HTML.
- `export` stays a signed download URL. It does not return bytes.
- The default `inspect` payload stays metadata, hashes, and the manifest.
- No extra model call. The CLI is unchanged.
- Existing publish rows are not backfilled. A null note still shows only the generic sentence.
- Do not split `service.py` in this change.
- Do not merge or deploy.

### Deferred to Follow-Up Work

- Reading history rows' content in one call. This plan reads the target version only.
- A change list on the immediate `publish` return. That return happens before the worker runs (R8, KTD4).

### Sources

- `src/artifactsmith/server.py` (`inspect`, `publish`, tool docstrings).
- `src/artifactsmith/service.py` (`inspect`, `check_access`, `card`, `status`, `_execute_build` history load).
- `src/artifactsmith/publish_common.py` (`PUBLISH_VERBATIM`, `summary_text`, `insert_version`).
- `src/artifactsmith/publish_new.py` and `src/artifactsmith/publish_version.py` (idempotency fingerprints).
- `src/artifactsmith/renderers/html_sanitize.py` (`sanitize_html_document`, title write, `linkify_html_text` call).
- `src/artifactsmith/renderers/links.py` (`emit_href`).
- `src/artifactsmith/db.py` (`DB._migrate`).
- `tests/unit/test_readme_tools.py` locks the tool-name set to `README.md`.

---

## Planning Contract

### Key Technical Decisions

- KTD1. Add `include_content` to `inspect`, default false. After `get_artifact` has already applied `check_access`, and only for the target version, attach `content` with `file`, `size_bytes`, and `text` when R2 allows. For PDF, DOCX, and XLSX set `primary_binary` and read `content.md` (R3). A store exception on that get becomes `content_error` (R4). Do not base64 the binary. Do not attach content to history rows. The rejected alternative is a new read tool, which would change the locked tool table (Key Decision).
- KTD2. Keep `nh3.clean_text` on the document title so markup is stripped. Unescape that result once, then `html.escape` once at the `<title>` write. Do not escape the `clean_text` result a second time. A space stays a space. `<` is stored as one `&lt;`. Do not change `emit_href`. That write is shared with model HTML and the markdown share view, so those paths get the same unescape-then-escape. Changing `emit_href` would weaken link policy.
- KTD3. Skip `linkify_html_text` on the publish HTML path only, by a flag that defaults to today's behavior. Model HTML and the markdown share view still linkify. Markdown publish still runs `sanitize_markdown` with linkify on (R6). Private-host detection stays in `check_content`, which matches the URL in the raw text. Skipping linkify inside the shared function would also skip it for model HTML.
- KTD4. Keep `publish`'s queued return (R8). During the publish worker, compare the submitted page with the stored text (`index.html`, `document.md`, or `content.md`) and record a label when that stage changed bytes. Do not compare the submitted page with `document.pdf`, `document.docx`, or `document.xlsx`. The labels are `stripped_markup`, `rewrote_image`, `hardened_anchor`, `escaped_title`, `added_csp`, `added_charset`, `added_viewport`, `added_doctype`, `linkified_url`, `added_heading`, and `normalized_document`. `linkified_url` is not emitted for HTML publish. `added_heading` covers the markdown renderer prepending a title. `normalized_document` covers a difference in that text that no earlier label explains. It is not emitted only because the primary is binary. Store the list on the manifest as `safety_changes`. Copy it onto the done `status` card and the `inspect` target even when `include_content` is false. Omit the field when the key is absent (older rows). Include an empty list when a publish compared and nothing differed. Do not mix this list into builder `notes`. Do not block the `publish` call to compute it.
- KTD5. Add nullable `versions.note` with the existing `ALTER TABLE` migration. `insert_version` keeps `verbatim_request` as `Published a finished page.` A non-empty note is stored on the column and copied onto each inspect history row that has one, including when `include_content` is false. Fingerprints in `publish_new` and `publish_version` include the note only when it is non-empty. Blank and omitted leave the note out of the fingerprint, so a retry of a note-less publish still replays. Validate a provided note with `check_fields` before `insert_version`, using `CFG.block_private_links` and `CFG.allowed_link_domains`, and raise `AMError` on any problem so no version row is written. `summary_text` is length-only and is not this check. Do not load the note into the edit history passed to `run_build`. `status` does not show the note.

### Assumptions

- The report said "capped in size" and did not name the number. R2 uses the publish character cap so a page that was accepted can be read back, including multi-byte text.
- `status` does not show the note. `inspect` does.
- Shell additions are listed per missing piece (`added_csp`, `added_charset`, `added_viewport`, `added_doctype`), not as one constant "rebuilt" label.
- Table wrappers are not a publish-as-is mutation.

### High-Level Technical Design

```mermaid
sequenceDiagram
  participant Caller
  participant Publish
  participant Worker
  participant Inspect
  Caller->>Publish: page and optional note
  Publish->>Publish: reject a bad note before insert
  Publish-->>Caller: queued ids
  Publish->>Worker: sanitize, skip HTML autolink
  Worker->>Worker: escape title, record safety_changes
  Caller->>Inspect: include_content optional
  Inspect->>Inspect: workspace check, then read
  Inspect-->>Caller: note and safety_changes always; text only when opted in
```

The queued `publish` dict never contains page bytes or `safety_changes`. Those appear only after the version is done. Content bytes are read only inside `inspect`, after the workspace check, and only when `include_content` is true.

### Sequencing

U1 lands the opt-in read. U2 lands title escaping, the publish autolink skip, and the change list. U3 lands the note, its fingerprint, and the changelog entries for all three. U2 and U3 both edit `service.py`, so they stay in that order.

---

## Implementation Units

### U1. Opt-in content on inspect

- **Goal:** A caller can read the target version's stored text back, under the existing workspace check and the cap in R2.
- **Requirements:** R1, R2, R3, R4, R13
- **Dependencies:** none
- **Files:** `src/artifactsmith/server.py`, `src/artifactsmith/service.py`, `tests/unit/test_service.py`, `README.md`
- **Approach:** Follow KTD1. Mirror the manifest read's `store_read_failed` handling so a content-store failure stays an `AMError`-free inspect result. Update the `inspect` docstring and the README inspect row. Do not add a tool name.
- **Patterns to follow:** `Service.inspect` manifest `try`/`except`, `test_inspect_scrubs_store_errors`, `test_cross_workspace_forbidden`
- **Test scenarios:**
  - Done HTML, `include_content` true, own workspace: result `content.text` equals the stored primary, and `content.file` is that primary name.
  - `include_content` omitted or false: the result has no `content` key and no `content_error`. Other inspect fields stay.
  - Other workspace, `include_content` true: the existing forbidden error, and the content object is not read.
  - Stored text one character over the R2 cap: `size_bytes` is set, `text` is absent.
  - Stored text of multi-byte characters that is still within the R2 character cap: `text` is present.
  - Stored text exactly at the cap: `text` is present.
  - PDF, DOCX, or XLSX with `content.md`: `primary_binary` is true and `text` is the markdown, not the binary.
  - PDF without `content.md`: no `text`, no `content_error`.
  - Version not done: no `text`, no `content_error`.
  - Store `get` raises on the content object: `content_error` is `store_read_failed`, and the rest of inspect still returns.
- **Verification:** Those cases pass, and the README tool-name set is unchanged.

### U2. Title, HTML autolink, and the change list

- **Goal:** An as-is HTML publish keeps the caller's title and bare URLs, and the done job lists every safety change that still fired.
- **Requirements:** R5, R6, R7, R8, R13
- **Dependencies:** U1
- **Files:** `src/artifactsmith/renderers/html_sanitize.py`, `src/artifactsmith/builder.py`, `src/artifactsmith/service.py`, `src/artifactsmith/server.py`, `tests/unit/test_html_sanitize.py`, `tests/unit/test_publish.py`, `README.md`
- **Approach:** Follow KTD2, KTD3, and KTD4. The publish path passes the autolink flag off. Model HTML and `markdown_share_html` keep the default. Compare stages inside the publish worker and write `safety_changes` on the manifest. `status` and `inspect` copy that key when it is present, including when `include_content` is false. Update the `publish` tool docstring in `server.py` so it points at the done status for the list.
- **Patterns to follow:** `sanitize_html_document`, `publish_page` notes list, `test_publish_html_matches_sanitizer_and_shares`, `test_linkifies_bare_public_urls_in_text`
- **Test scenarios:**
  - HTML publish with document title `My Report` stores `<title>My Report</title>`, not `&#32;` or `&amp;#32;`, and inspect `title` is that string.
  - A title containing `<` is stored as escaped text inside `<title>`, and `safety_changes` includes `escaped_title`.
  - A bare `https://example.com/doc` in published HTML stays text. The same string in model HTML or the shared sanitizer default still becomes an anchor. Covers AE4.
  - A private-host URL in published HTML still fails the content check.
  - A submission without the export CSP meta gains `added_csp` on the done status and on inspect, including an inspect call that omits `include_content`. It does not gain `linkified_url`.
  - Remote image rewrite and external-anchor hardening still fire, and each that changes bytes is listed.
  - Markdown publish of a body that does not start with a heading lists `added_heading` when the renderer prepends one, and lists `linkified_url` when a bare public URL was linkified.
  - `publish` returns before the worker finishes and does not include `safety_changes`.
  - An older done version with no manifest key omits `safety_changes` rather than showing an empty list.
  - `llm.call` is not used on publish.
- **Verification:** Publish HTML no longer equals `sanitize_html` of a page that contains a bare URL. It still equals the sanitizer result with autolink off. CSP meta order tests still pass.

### U3. Optional publish note

- **Goal:** A caller can attach a note that inspect shows, without changing the sentence later edits send to the model.
- **Requirements:** R9, R10, R11, R12, R13
- **Dependencies:** U2
- **Files:** `src/artifactsmith/db.py`, `src/artifactsmith/publish_common.py`, `src/artifactsmith/publish_new.py`, `src/artifactsmith/publish_version.py`, `src/artifactsmith/publishing.py`, `src/artifactsmith/server.py`, `src/artifactsmith/service.py`, `tests/unit/test_publish.py`, `README.md`, `CHANGELOG.md`
- **Approach:** Follow KTD5. Thread `note` from the `publish` tool through `admit_publish` into both insert paths. Add the `include_content` flag and the note to the `INSTRUCTIONS` sentence that already says to call `status` and show the card. Update the publish argument docs and add changelog bullets for U1, U2, and U3.
- **Patterns to follow:** `DB._migrate` column adds, `check_fields` in `render_checked_body`, `test_publish_rejects_overlong_metadata_before_store`, the existing idempotency conflict test
- **Test scenarios:**
  - Note `checked the harbor table` is on the inspect history row beside `Published a finished page.`, including when `include_content` is omitted. Covers AE5.
  - Omitted note: that history row has no note field, and `verbatim_request` is still the generic sentence.
  - A note over the summary length cap, a note with a secret, and a note with a private or non-allow-listed host are each rejected before a version row exists.
  - The same page published again with a different note does not replay the first job.
  - A retry of a note-less publish, including one stored before this column, still replays.
  - The same idempotency key with a different note raises the existing conflict error.
  - A later edit's history payload still contains `Published a finished page.` and does not contain the note text.
  - A database created before the column migrates, and inspect of an old row still returns the generic sentence.
- **Verification:** Those cases pass, and `CHANGELOG.md` under `[Unreleased]` names the readback, the title and autolink behavior, and the note.

---

## Verification Contract

| Gate | Command | What it proves |
| --- | --- | --- |
| Lint | `ruff check src tests examples` | Style and import rules |
| Format | `ruff format --check src tests examples` | Formatting matches the repo |
| Types | `mypy --strict src` | The new arguments and the nullable column type-check |
| Unit | `pytest -o addopts= --cov-fail-under=0 tests/unit/test_service.py tests/unit/test_publish.py tests/unit/test_html_sanitize.py tests/unit/test_readme_tools.py` | R1–R13 behavior during the change |
| CI | `make ci` | Lint, strict mypy, the coverage gate, and the denylist |

`make ci` is the completion gate. The scoped pytest command is for the units while they are in progress. Coverage in CI stays at the repo gate (`--cov-fail-under=80` via `make test`).

---

## Definition of Done

- R1–R13 each have a passing test named in U1, U2, or U3.
- Default `inspect` still omits page text.
- HTML publish does not linkify bare URLs, and a private-host URL still fails the content check.
- The document title keeps spaces and is HTML-escaped inside `<title>`.
- A done publish lists `safety_changes` on `status` and `inspect`.
- A note shows on inspect and does not become the next edit's model history.
- README tool names are unchanged. Tool text and `CHANGELOG.md` match the behavior.
- Abandoned experiments are not in the diff.
- The pull request is open, ready for review, and not merged.

---

## System-Wide Impact

- `inspect` and `publish` gain optional arguments. The tool-name set does not change. `INSTRUCTIONS` in `server.py` today says to call `status` and show the card. U3 adds the `include_content` flag and the note to that sentence.
- `versions` gains a nullable `note`. Old rows stay null. No backfill.
- `sanitize_html_document`'s title write changes for every HTML path, including model HTML and the markdown share view. The change is escaping, not a new wrapper.
- Done `status` grows `safety_changes` only when the manifest has that key. Create and edit jobs do not write it.
- A content read that runs before `check_access`, or from `_read_stored` with only an id, would cross workspaces. U1 must not add that path.

## Risks & Dependencies

- **Workspace leak.** Reading the primary from a helper that does not take a principal skips `check_access`. Mitigation: the new get lives in `Service.inspect` after `get_artifact`. AE2 locks it.
- **Shared title write.** Escaping `<title>` changes model HTML and share HTML, not only publish. Mitigation: the escape is the safe render. Update any test that expected a raw `<` inside `<title>`.
- **Autolink flag default.** A default of off would drop linkify for model HTML. Mitigation: default on, publish passes off, and U2 tests both.
- **Private URL after the skip.** HTML autolink is not what blocks private hosts. Mitigation: U2 keeps a private-host publish test on the content check.
- **Silent byte changes.** A rebuild that differs without a label would hide a change. Mitigation: KTD4's `normalized_document` label, asserted when bytes differ and no earlier label fired.
- **Note in the model prompt.** Replacing `verbatim_request` would send the note on the next edit. Mitigation: the column is separate, and U3 asserts the edit history text.


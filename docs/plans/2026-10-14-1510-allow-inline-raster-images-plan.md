---
title: Allow Inline Raster Images - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Allow Inline Raster Images - Plan

## Goal Capsule

**Objective:** An owner can publish a page that contains screenshots as inline images, and a reader opening the share sees those images.

**Means:** Accept a narrow base64 raster `data:` URL on `img` `src` and `srcset` only, and keep every other `data:` URL blocked (KTD1, KTD2).

**Authority:** This plan, then the repository's sanitizer, CSP, and link policy. A shorter path that skips those checks is out of bounds.

**Stop:** Do not merge or deploy. Do not add an MCP tool. Do not change `classify_href` so that `data:` becomes allowed in general. Do not widen CSP. Do not change `emit_href`. Do not embed bitmaps in PDF, DOCX, or XLSX.

**Who ships:** The PM reviews the pull request and ships it. This work opens the pull request ready for review.

## Product Contract

### Summary

Publish of an HTML page that contains a screenshot as `data:image/png` or `data:image/jpeg` fails, so the owner has to republish without the pictures. After this change, a valid base64 raster on an `img` displays in the built page and in the share, and every other `data:` URL still fails.

### Problem Frame

On 13 Oct 2026, three publish jobs for "Call tracking actions" failed with `blocked or authority-confused HTML src: data:image/jpeg;base64,/9j/4AAQ...` and the same for a 1x1 PNG. `classify_href` returns `blocked` for every `data:` URL. The pre-sanitize and post-sanitize sweeps both use that classification, and the blocked-src message is not a hard problem, so the sanitizer runs and then the post-check still rejects the page. The sanitizer's own `img` filter already intended to keep `data:image/(png|jpeg|gif|webp|avif)`, and the export and share CSP already allow `img-src data:`.

### Requirements

**Images that display**

- R1. An HTML page whose `img` `src` is `data:image/(png|jpeg|jpg|gif|webp|avif);base64,` plus valid base64 of that raster type publishes. The stored page and the share HTML still contain that `img` and that `src`.
- R2. The same six types are accepted as `img` `srcset` candidates, including a descriptor (`1x`, `2x`, `100w`) and more than one candidate. The comma that follows `;base64,` is part of the URL, not a candidate break.
- R3. A markdown page whose image syntax is `![alt](data:image/<type>;base64,...)` for those same types stores the image syntax, and the share HTML contains an `img` with that `src` and the alt text.

**Images that stay blocked**

- R4. `data:` on `href`, `iframe`, `object`, `embed`, form actions, CSS `url()`, SVG, and any non-`img` element stays blocked. `classify_href` still returns `blocked` for every `data:` URL, including a valid raster.
- R5. `data:image/svg+xml`, `data:text/html`, and a `javascript:` URL stay blocked on `img` and on links. A job that contains one of those on an `img` `src` or `srcset` does not store a page.
- R6. A `data:` URL that is the wrong shape (missing `;base64,`, bad alphabet, bad padding, whitespace, a charset parameter, or percent-encoding), whose decoded bytes do not match the declared type, or whose decoded size is over 512 KiB, fails the job before a page is stored. The message names the rejection and does not include the payload beyond a short prefix.
- R7. An `http:` or `https:` image stays a link, not a fetched subresource. Putting `srcset` on the allowlist does not start emitting remote `srcset` candidates.

**Unchanged defenses**

- R8. Authority-confusion checks still reject a confused URL. A confused value is not accepted just because it sits on an `img`.
- R9. Share and preview response CSP stay byte-for-byte as they are, including `img-src data:`. Export CSP stays as it is. `script-src`, `object-src`, `font-src`, and `media-src` are not widened.
- R10. The changelog records that inline raster screenshots on `img` can publish. Existing sanitizer probes stay green.

### Actors

- A1. An owner publishing or building an HTML or markdown page.
- A2. A reader opening the share, including on a 390px-wide phone.

### Flows

- F1. Owner publishes HTML with a valid PNG `img` `src`. The job finishes. The share shows the image.
- F2. Owner publishes HTML whose `img` `src` is `data:text/html` or a broken base64 raster. The job fails with a clear message and no version is stored.
- F3. Owner publishes markdown with `![shot](data:image/png;base64,...)`. The share HTML contains that `img`.

### Acceptance Examples

- AE1. Each of png, jpeg, jpg, gif, webp, and avif, in `src` and in `srcset`, survives HTML sanitize and `check_content`, and a tiny PNG whose base64 is `iVBORw0KGgo=` still survives. Covers R1, R2.
- AE2. `data:image/svg+xml`, `data:text/html`, `javascript:alert(1)` as a URL, and `data:image/png;base64,...` on an `<a href>` do not publish and do not remain as a live URL in a successful page. Covers R4, R5.
- AE3. `data:image/png;base64,@@@@` and a decoded payload larger than 512 KiB fail before storage, and the error text is not the whole payload. Covers R6.
- AE4. A published HTML page with a valid inline PNG returns share HTML that contains the `img`, and the response CSP is unchanged. At 390px the image is visible in the page. Covers R1, R9.
- AE5. Markdown `![shot](data:image/avif;base64,...)` and the `jpg` spelling become an `img` in the share HTML. A remote `https://` image in markdown is still a link, not an `img`. Covers R3, R7.

### Success Criteria

- The existing sanitizer corpus stays green.
- `make lint`, `make typecheck`, `make test`, and `make denylist` pass.

### Scope Boundaries

- No new MCP tool, no README tool-table change, and no builder-prompt change.
- PDF, DOCX, and XLSX keep showing the alt text for a non-http image. They do not gain embedded bitmaps.
- Raw `<img>` inside a markdown file is not the share path. The layout parser has HTML disabled and drops `html_inline` except `<br>`. Markdown screenshots use image syntax.
- `emit_href` stays trim and space-to-`%20` only.
- Do not stack this work on the unpublished publish-readback branch. Branch from current `main`.

### Key Decisions

- KD1. Screenshots display only as raster `data:` images on `img`. Remote `http(s)` images stay links. Governs R1, R7.
- KD2. A bad inline image fails the job instead of publishing a page that silently lost the screenshot. Governs R5, R6.

### Dependencies

- nh3 (`nh3>=0.2.18`) already filters attributes. markdown-it-py's default `validateLink` allows `data:image/(gif|png|jpeg|webp)` and rejects `jpg` and `avif`.

### Outstanding Questions

None.

### Sources

- Live publish errors on art_utdbd3lmbg2r2e, 13 Oct 2026.
- `src/artifactsmith/renderers/links.py` (`_DANGEROUS_PREFIXES`, `classify_href`).
- `src/artifactsmith/renderers/md_html.py` (`href_src_attr_ok`, `_url_attr_ok`, `_filter_attrs`).
- `src/artifactsmith/renderers/html_sanitize.py` (`_DATA_IMAGE`, `_attribute_filter`, `rewrite_remote_images_to_links`).
- `src/artifactsmith/builder.py` (`_hard_problems`, `render_checked_body`).
- `src/artifactsmith/layout/inline.py` and `src/artifactsmith/layout/parse.py`.
- `src/artifactsmith/renderers/html_export.py` `EXPORT_CSP` and `src/artifactsmith/server.py` `CSP`.
- Installed `markdown_it/common/normalize_url.py` `GOOD_DATA_RE`.

## Planning Contract

### Key Technical Decisions

- KTD1. Leave `classify_href` unchanged. `data:` stays `blocked` for every caller that is not the `img`-scoped check. The HTML attribute sweep is tag-blind today; allowing `data:` inside `classify_href` would also accept it on `href` and the other URL attributes. Governs R4.
- KTD2. One predicate, in `src/artifactsmith/renderers/raster_data.py`, decides whether a single URL is an allowed raster. The HTML gate, the nh3 attribute filter, markdown policy, and the layout renderer all call it. Do not keep the looser `_DATA_IMAGE` regex as a second rule. Governs R1, R6.
- KTD3. Split `srcset` so the comma after `;base64,` stays inside the URL. The `srcset` attribute is kept only when every candidate passes the predicate. An `http(s)`, relative, or failing candidate drops the whole attribute. That matches today, where `srcset` is not on the `img` allowlist, and it does not start emitting remote candidates. Governs R2, R7.
- KTD4. Problems prefixed `inline image rejected` are hard. `render_checked_body` raises them before sanitize. A valid raster produces no problem, so the sanitizer keeps it and the post-check passes. On `img` `src` and `srcset`, `javascript:`, `data:image/svg+xml`, and `data:text/html` are that hard prefix, not the soft blocked message. Other `blocked or authority-confused` problems, including those three on a non-`img` element, stay soft and are still stripped by the sanitizer. Governs R5, R6.
- KTD5. The layout parser's `validateLink` stays the library default for non-`data:` URLs and additionally accepts `jpg` and `avif` only when the predicate passes. Do not set that parser's `validateLink` to always true. Markdown sanitize may keep an image token only when the predicate passes; any other image token still fails the destination policy. Governs R3, R4.

### Technical Design

The predicate accepts a URL only when the trimmed value equals its de-obfuscated view, the header is `data:image/<type>;base64,` with `<type>` in `png`, `jpeg`, `jpg`, `gif`, `webp`, `avif` (case-insensitive header, case-sensitive payload), the payload is standard base64, `base64.b64decode(..., validate=True)` succeeds, the decoded length is at most 512 KiB, and the magic bytes match the type. PNG is `\x89PNG\r\n\x1a\n`. JPEG is `\xff\xd8\xff`. GIF is `GIF87a` or `GIF89a`. WEBP is `RIFF` at 0 and `WEBP` at 8. AVIF is an `ftyp` box whose brand is `avif` or `avis`.

`_filter_attrs` learns the element name. On `img` `src`, a URL that passes the predicate is kept. On `img` `srcset`, the attribute is kept only when every candidate passes. On `img` `src` and `srcset`, a failing `data:` URL, and a `javascript:` URL, add `inline image rejected: ...`. Any other URL attribute keeps today's `href_src_attr_ok` path.

`rewrite_remote_images_to_links` keeps an `img` whose `src` passes the predicate, including a valid `srcset`. A remote `src` still becomes an anchor. An `img` with only a valid `srcset` is kept.

Markdown `![](data:...)` that passes the predicate stays an image token. `layout/inline.py` emits `<img alt="..." src="...">` for that token. `iter_inline_segments` keeps today's alt-text behavior for PDF, DOCX, and XLSX.

The markdown pre-check walks image destinations. A `data:` destination that fails the predicate is `inline image rejected`. A public `http(s)` destination is unchanged.

### Assumptions

- The per-image cap is 512 KiB decoded (524288 bytes). One screenshot should not be able to fill the existing 2,000,000-character document cap. A separate 1.5 MB decoded page sum is not added: base64 expansion makes the existing character cap stricter than that sum, so the sum would never fire.
- `jpeg` and `jpg` both mean JPEG magic bytes.
- Magic-byte mismatch is a rejection, not merely an alphabet check. `script-src 'none'` already stops script execution; the magic check stops a non-image payload from being labeled as a raster. Dimension headers inside an otherwise small file are not parsed in this change.
- Invalid images fail the job. Dropping them and still publishing was the other reading of the request; it would hide a missing screenshot.
- The payload is one line of standard base64. Whitespace, a charset parameter, and percent-encoding are rejected even if a browser would strip them.

### Sequencing

U1 first. U2 and U3 both depend on U1. U3's share check depends on U2.

## Implementation Units

### U1. Shared raster predicate and the HTML gate

**Goal:** A valid raster `data:` URL on `img` `src` and `srcset` passes the HTML check and the sanitizer. An invalid one fails the job first.

**Requirements:** R1, R2, R4, R5, R6, R7, R8.

**Files:** `src/artifactsmith/renderers/raster_data.py`, `src/artifactsmith/renderers/md_html.py`, `src/artifactsmith/renderers/html_sanitize.py`, `src/artifactsmith/builder.py`, `tests/unit/test_raster_data.py`, `tests/unit/test_html_sanitize.py`, `tests/unit/test_md_html_followup.py`.

**Approach:** Add the predicate and the `srcset` splitter. Pass the tag into the attribute filter. Replace `_DATA_IMAGE` call sites in the HTML sanitizer with the predicate. Add `srcset` to the `img` allowlist and keep it only when every candidate passes. Treat `inline image rejected` as hard. Leave `classify_href` and both CSP strings alone.

**Test scenarios:**
- Each of the six types passes as `src` and as a `srcset` candidate with a descriptor. Two candidates in one `srcset` both survive.
- `data:image/png;base64,iVBORw0KGgo=` still survives `sanitize_html_document`.
- `srcset` whose base64 contains no extra comma is not split into a bare payload.
- `data:image/svg+xml`, `data:text/html`, and `javascript:` on `img` `src` or `srcset` each produce a hard `inline image rejected` problem and store no page. The same three on `<a href>` still report the soft `blocked or authority-confused` message.
- The same URLs on `<a href>` still report `blocked or authority-confused` and do not use the raster exception.
- An `https://` `srcset` does not remain on the sanitized `img`.
- A confused `img` `src` (whitespace before `://` or a similar existing confusion case) is still rejected.
- `classify_href("data:image/png;base64,iVBORw0KGgo=")` is still `blocked`.
- Export CSP and the preview `CSP` constant are unchanged.

**Verification:** `pytest -o addopts= --cov-fail-under=0 tests/unit/test_raster_data.py tests/unit/test_html_sanitize.py tests/unit/test_md_html_followup.py tests/unit/test_links.py`

### U2. Markdown image syntax and the share page

**Goal:** A markdown screenshot using image syntax is still in the stored markdown and shows up as an `img` on the share page.

**Requirements:** R3, R7.

**Files:** `src/artifactsmith/renderers/md_sanitize.py`, `src/artifactsmith/layout/parse.py`, `src/artifactsmith/layout/inline.py`, `src/artifactsmith/layout/css.py`, `src/artifactsmith/renderers/md_html.py`, `tests/unit/test_raster_data.py`, `tests/unit/test_server.py`.

**Approach:** Keep a markdown image token when the predicate passes, and let the destination policy allow only those image tokens. Extend the layout parser's `validateLink` for `jpg` and `avif` without setting it always-true. Emit an escaped `img` from the layout image branch. Add `img{max-width:100%;height:auto}` to the house layout stylesheet so a screenshot wider than the phone viewport scales down and a small image is not stretched. The body already uses `overflow-x:clip` and has no `img` rule. Scan markdown image destinations in the pre-check so a bad `data:` image is a hard problem. Leave `iter_inline_segments` showing alt text for non-http images.

**Test scenarios:**
- `![shot](data:image/png;base64,<valid>)` survives `sanitize_markdown` as an image, and `![shot](data:text/html,x)` does not publish as a live image.
- `jpg` and `avif` image syntax become an `img` in `markdown_share_html` output. `data:text/html` does not.
- A markdown `https://` image is still an anchor, not an `img`.
- A `data:` URL inside a code span stays code. The keep-corpus case with a data image inside inline code stays text.

**Verification:** `pytest -o addopts= --cov-fail-under=0 tests/unit/test_raster_data.py tests/unit/test_server.py tests/unit/test_keep_corpus.py`

### U3. Publish, share at 390px, and changelog

**Goal:** A real publish of an inline PNG completes, the share page shows it, and the changelog names the change.

**Requirements:** R1, R9, R10.

**Files:** `CHANGELOG.md`, `tests/unit/test_publish.py`, `tests/unit/test_server.py`.

**Approach:** Publish a small HTML document whose only image is a valid 1x1 PNG `data:` URL. Assert the job reaches done and `GET /s/{sid}/` contains the `img` and the unchanged CSP. Repeat for a markdown image. Open that share in a 390px-wide browser and confirm the image is visible. Add a changelog bullet under Unreleased.

**Test scenarios:**
- `publish` of HTML with the 1x1 PNG does not raise, and the share body contains `data:image/png;base64`.
- `publish` of HTML with `data:text/html`, `data:image/svg+xml`, or `javascript:` on an `img` `src` raises before a done version exists.
- The share response `Content-Security-Policy` still contains `img-src data:` and `script-src 'none'`.
- Browser check at 390px: a screenshot wider than 390px is visible, its rendered box sits inside the viewport, and the page scroll width does not exceed the client width. The 1x1 PNG still has a non-zero rendered size.

**Verification:** The unit tests above, then `make lint`, `make typecheck`, `make test`, and `make denylist`.

## Verification Contract

From the repo root, with `.venv` active:

- `pytest -o addopts= --cov-fail-under=0 tests/unit/test_raster_data.py tests/unit/test_html_sanitize.py tests/unit/test_md_html_followup.py tests/unit/test_links.py tests/unit/test_publish.py tests/unit/test_server.py tests/unit/test_keep_corpus.py`
- `make lint`
- `make typecheck`
- `make test`
- `make denylist`

CI on the pull request is the final gate (`ruff`, `mypy --strict src`, pytest with coverage fail-under 80, `bash scripts/denylist.sh`).

## Definition of Done

- U1, U2, and U3 test scenarios pass.
- `classify_href` still returns `blocked` for `data:` URLs, and both CSP constants are unchanged.
- Changelog has an Unreleased note.
- The diff does not contain an abandoned second parser or a loosened `validateLink = lambda: True` on the layout parser.
- The pull request is open against `main` and ready for review. It is not merged.

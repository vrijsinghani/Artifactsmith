---
title: Render Checkboxes, Phone Tables, and Titles - Plan
type: fix
date: 2026-11-26
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Render Checkboxes, Phone Tables, and Titles - Plan

## Goal Capsule

Objective: A reader of a PDF, a fast HTML page, a bold full-mode page on a phone, or a Word file sees one title, a real checkbox mark, and every table column without swiping sideways.

Means: Patch the existing PDF HTML list renderer, the fast-mode task span, the bold full-mode HTML after sanitizing, and the Word heading loop (KTD1-KTD4).

Authority: The four GitHub issues and the user's spec outrank this plan. Do not change sanitizer behavior. Do not drop links, images, or charts. One pull request. Do not merge.

Stop: Stop if stacking a 4-column table changes the 1280px table.

Execution: Test the three text bugs first, then the phone layout. Ship from the implementation branch. The maintainer merges.

## Product Contract

### Summary

Four rendering bugs, one change set. PDF task lines stop printing the literal bracket text. Fast mode shows a box and hides the words "Done" and "Not done" from sight. Bold full-mode pages turn a table of four or more columns into stacked cards on a phone and leave the desktop table alone. Word drops a body heading that repeats the document title, the same way PDF already does.

### Problem Frame

Readers hit the bracket text, the words "Not done", a sideways table, or the title twice. Each path already has a neighboring fix: the web parser knows task items, fast mode already stacks some tables, and PDF already skips a matching first heading.

### Requirements

- R1. In PDF HTML, a Markdown item `- [ ]` renders an unchecked box glyph and `- [x]` renders a checked box glyph. The literal `[ ]` and `[x]` text is absent.
- R2. A unit test of `blocks_to_simple_html()` covers both states in R1.
- R3. Fast-mode HTML for a task item shows a visible box glyph. The words "Done" and "Not done" stay in the document for screen readers and are visually hidden.
- R4. For fast-mode source `- [ ] a` and `- [x] b`, the rendered visible text contains neither plain "Not done" nor plain "Done".
- R5. A bold full-mode page with a table of four or more columns is readable at 390px with no sideways scroll. Every column is on the card.
- R6. That same table at 1280px looks as it does today.
- R7. The pull request includes a 390px screenshot and a 1280px screenshot of R5 and R6.
- R8. A Word file whose body starts with a heading equal to the document title does not print that title twice. The first two paragraphs are not both the title.
- R9. Sanitizer rules, links, images, and charts stay as they are. `make ci` passes.

### Key Decisions

- KD1. Use the glyphs ☐ and ☑. Governs R1, R3. The issues allow those glyphs, and the user required them.
- KD2. Keep the screen-reader words. Governs R3. The user required the words to remain, hidden.
- KD3. Drop the duplicate body heading and keep the document title. Governs R8. That is what PDF already does.

### Scope Boundaries

Out of scope: task-list changes in Word or spreadsheets, stacking tables in styles other than bold full mode, stacking tables with fewer than four columns, sanitizer edits, new dependencies, and refactors of neighboring renderers.

### Assumptions

- The PDF skip rule is the one to copy: the first block is a level-1 heading whose text matches the title, case-insensitive, after trim. A later heading, a level-2 heading, or a different string stays.
- "Visible text" means text a sighted reader sees, so words inside the visually hidden span do not count.
- Full-mode HTML is model-written. A prompt change would not make R5 true. A deterministic rewrite after sanitizing, only for bold full mode, is the assumption this plan executes.
- The bold shared stylesheet must stay under 8192 bytes. A visually hidden rule that would cross that cap belongs in the per-page extra sheet instead.
- Liberation Sans, the font the PDF image installs, has no ☐ or ☑. DejaVu Sans does, and it is already on the developer machine. The PDF image may add the existing apt package `fonts-dejavu-core` beside `fonts-liberation`. That is a font already used for these glyphs, not a new Python library.

## Planning Contract

### Key Technical Decisions

- KTD1. Rewrite task markers only when emitting PDF list HTML. Leave `parse_blocks()` item strings unchanged so Word and spreadsheets keep their current text. A leading `[ ]`, `[x]`, or `[X]` becomes the glyph plus the rest of the item through the existing inline HTML helper. Put `DejaVu Sans` first in the PDF body font stack, and install `fonts-dejavu-core` in `docker/Dockerfile` next to `fonts-liberation`, so WeasyPrint can draw U+2610 and U+2611. Governs R1, R2.
- KTD2. In the fast parser, put the glyph in the existing check span with `aria-hidden="true"`, and the words in a second span. Hide that second span by clipping it off screen (absolute, 1px, overflow hidden). Do not use `display:none` or `visibility:hidden`, or a screen reader would lose the words. Add that class to the fast stylesheet. Governs R3, R4.
- KTD3. After `sanitize_html` on a bold full-mode HTML body, mark each table whose column header row has four or more cells. The column header row is the last row in `thead`, or the first row when there is no `thead`. Count a cell's colspan. Copy that row's cell text into `data-label` on the body cells in the same column. Append one media query, max-width 40rem, using the same card rules fast mode already uses for `table.stack`, aimed at the class this rewrite adds. Do not edit the sanitizer. Tables under four columns are left alone, so a 1280px layout has no new rule that applies. Governs R5, R6. A prompt-only fix was the alternative and loses, because the model does not reliably emit the card CSS and the screenshot could not be produced from a prompt.
- KTD4. In the Word renderer, skip the first parsed block when it is a level-1 heading matching the title under the PDF predicate. Do not mutate the caller's block list. Governs R8.

### Sequencing

U1, U2, and U4 are independent. U3 depends on nothing in those units. Land them in one branch.

## Implementation Units

### U1. PDF task glyphs

Goal: PDF HTML shows ☐ and ☑.

Requirements: R1, R2.

Files: `src/artifactsmith/renderers/md_parse.py`, `docker/Dockerfile`, `tests/unit/test_md_parse.py`.

Approach: In the list branch of `blocks_to_simple_html()`, replace a leading task marker before `_inline_html`.

Test scenarios: `- [ ] one` contains ☐ and does not contain `[ ]`. `- [x] two` contains ☑ and does not contain `[x]`. A plain `- bullet` is unchanged. Emphasis and links inside a task item still render.

Verification: `pytest tests/unit/test_md_parse.py`.

### U2. Fast-mode hidden status words

Goal: Sighted readers see the box, not the words.

Requirements: R3, R4.

Files: `src/artifactsmith/layout/parse.py`, `src/artifactsmith/layout/css.py`, `tests/unit/test_layout.py`.

Approach: Emit the glyph and a visually hidden word span. Confirm `stylesheet("bold")` stays under 8192 bytes.

Test scenarios: Render `- [ ] a` and `- [x] b`. Visible text has ☐, ☑, `a`, and `b`, and not the plain words. The words remain in the HTML inside the hidden span. A non-task list item has no check span.

Verification: `pytest tests/unit/test_layout.py`.

### U3. Bold full-mode phone cards

Goal: Four-column tables read at 390px and match today at 1280px.

Requirements: R5, R6, R7.

Files: `src/artifactsmith/builder.py` (call site only), one new small module next to the HTML render path, `tests/unit/test_house_style.py` or a sibling unit test.

Approach: Apply KTD3 only when format is html, mode is full, and style is bold. Copy fast-mode card CSS rather than inventing a second layout. Screenshot the transformed page at 390px and 1280px for the pull request.

Test scenarios: A 4-column table gains the card class and `data-label` values. A 3-column table is unchanged. The new CSS is inside a max-width media query. An anchor, an image, and a chart in the same page are still present.

Verification: the unit test above, then headless Chrome screenshots at both widths.

### U4. Word duplicate title

Goal: The title is printed once.

Requirements: R8.

Files: `src/artifactsmith/renderers/docx_renderer.py`, `tests/unit/test_pdf_docx_xlsx_render.py`.

Approach: Skip the matching first h1 using the PDF predicate, including case and surrounding space.

Test scenarios: Body `# Sample kickoff meeting notes` with that display name yields one leading paragraph of the title, and the next paragraph is not the title. A different first heading is kept. A matching heading after a paragraph is kept.

Verification: `pytest tests/unit/test_pdf_docx_xlsx_render.py`.

## Verification Contract

Run `make ci` from the repo root with the project virtualenv active. It is the gate for R9.

Targeted proof before the full gate:

- `pytest tests/unit/test_md_parse.py tests/unit/test_layout.py tests/unit/test_pdf_docx_xlsx_render.py` plus the U3 unit module once it exists
- Headless Chrome at 390px and 1280px for the bold four-column fixture. Save both screenshots for the pull request body.

No release validator and no eval suite apply.

## Definition of Done

- R1 through R9 hold.
- U1 through U4 are in one pull request whose body contains `Fixes #69`, `Fixes #72`, `Fixes #78`, and `Fixes #79`.
- The 390px and 1280px screenshots are attached to that pull request.
- CI is green. The pull request is open and not merged.
- Abandoned experiments are not in the diff.

---
title: "Fix four rendering bugs - Plan"
date: 2026-11-26
type: fix
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Fix four rendering bugs - Plan

## Goal Capsule

**Objective:** A reader sees a checkbox on a task, a Word file that states its title once, and a bold phone page whose wide table can be read without swiping.

**Means:** Glyphs on the PDF and fast HTML paths, the PDF heading rule copied into Word, and the existing phone card rules applied to bold full HTML (KTD1, KTD2, KTD3, KTD4).

**Authority:** Each issue's Done when or Test text wins. The request's constraints win over extra cleanup.

**Execution:** One pull request, four commits, one commit per issue, ready for review. The owner merges.

**Stop:** Stop if a change removes links, images, charts, or tables, or if a fast-mode table that scrolls today is retargeted.

## Product Contract

### Summary

PDF and fast HTML task lists show an unchecked or checked box. Word drops a leading heading that repeats the document title. A bold full-mode page with a table of four or more columns reads at 390 px as stacked cards, and at 1280 px it still looks like today's table.

### Problem Frame

Hosted pages from 17 Oct 2026 print `- [ ]` as the characters `[ ]` in PDF, print the words "Not done" and "Done" in fast HTML, repeat the title in Word, and hide the right-hand columns of a wide bold table until the reader swipes. Fast HTML already has a phone card treatment for some tables. Full HTML is model-written and does not receive that treatment. PR #76 already stopped the PDF from repeating a matching leading heading.

### Requirements

**Task lists**

- R1. A PDF built from Markdown `- [ ]` and `- [x]` shows an unchecked box and a checked box. The characters `[ ]` are not in the PDF HTML.
- R2. A fast HTML page built from `- [ ] a` and `- [x] b` shows a visible box for each item. The words "Not done" and "Done" are available to a screen reader and are not visible plain text.

**Titles and tables**

- R3. When the body's first block is a level-1 heading equal to the document title, Word keeps the document title and drops that heading, so the first two paragraphs are not both the title. A level-2 heading, a heading after a paragraph, and a later repeat stay.
- R4. On a bold full-mode page, a table of four or more columns is readable at 390 px with no sideways scroll, using the stacked cards fast mode already uses for tables that stack. At 1280 px the table looks as it does today.

### Key Decisions

- KD1. Task marks are text boxes, not form controls. Governs R1, R2.
- KD2. Bold full-mode tables of four or more columns use stacked cards. Governs R4. The issue allows another layout that shows every column. The request names the card layout, and that layout already lives inside a max-width query, so a desktop table can stay a table.

### Scope Boundaries

- Word, Markdown, and spreadsheet task lists stay as they are.
- Fast mode keeps its current rule for which tables scroll and which become cards. `tests/unit/test_layout.py` locks a four-column, six-row table as a scroller.
- Full-mode pages in styles other than bold are unchanged.
- `publish` of caller-supplied HTML is unchanged. It has no build style.
- The pull request is not merged here.

### Deferred to Follow-Up Work

- Bring the checked-in `docs/layout-samples/` HTML back in line with the current table rules. Those samples are not the acceptance test.

### Sources

- Issues #69, #72, #78, and #79. #72's owner comment points at the fast-mode card layout.
- PDF heading skip: `src/artifactsmith/renderers/md_parse.py` `blocks_to_simple_html`, covered by `tests/unit/test_md_parse.py`.
- Fast task text: `src/artifactsmith/layout/parse.py` `_item`. No stylesheet rule targets `asl-check`.
- Word title: `src/artifactsmith/renderers/docx_renderer.py` always writes the title, then every heading.
- Card CSS: `src/artifactsmith/layout/css.py` `_TAIL`, media `max-width:40rem`, class `stack`, `data-label`. Decision: `src/artifactsmith/layout/tables.py` `render_table`. A table wider than four columns, or with more than eight rows, or with at least three columns and at least six rows, scrolls. A shorter four-column table stacks.
- Full HTML is the model document sanitized in `sanitize_html`. Fast HTML is `render_layout`. `clip` is not in the CSS allowlist in `src/artifactsmith/renderers/css_sanitize.py`. `attr(data-label)` is allowed. Bold `stylesheet()` must stay under 8192 bytes.
- Slack tools were available and were not searched.

## Planning Contract

### Key Technical Decisions

- KTD1. Recognize a task mark only as the leading prefix of a PDF list item inside `blocks_to_simple_html`. `md_parse.py` does not load Markdown plugins. Unchecked is □ (U+25A1) and checked is ■ (U+25A0), for `[ ]`, `[x]`, and `[X]`. The PDF image installs only Liberation Sans, and that face has neither ☐ nor ☑, so those two characters would paint as the same missing glyph.
- KTD2. Keep the words "Not done" and "Done" in a separate screen-reader element. Hide that element with allowlisted properties in `sheet_extra` (absolute, 1px by 1px, overflow hidden). `clip` would be stripped. Leave the glyph in `asl-check`. Do not add the rule to the shared sheet that the 8192-byte tests measure.
- KTD3. Word drops a heading only when it is the first block, level 1, and its text matches the title the way the PDF HTML builder already matches (strip, then casefold). A later repeat, a level-2 heading, and a different heading stay.
- KTD4. After bold full HTML is sanitized, rewrite each top-level table of four or more columns. If the table has no `thead`, move the first row of header cells into one. Set `data-label` from those headers, repeating a colspan across the columns it covers. Add the `stack` class and append the phone card rules from `_TAIL`, plus `white-space:normal` on the stacked cells and concrete `--line` and `--muted` colors on `table.stack` so a page with no style variables still shows a card edge and a column label. Do not change `render_table`. A prompt-only change cannot satisfy the screenshot test. Changing the fast threshold would miss full HTML and would fight the existing scroll test. The card rules sit under `max-width:40rem`, so 1280 px stays a table.

### Assumptions

- A1. The screen-reader element class is chosen during implementation. The words occur only inside it.
- A2. Column count is the first row, with `colspan` added in. A table that already has `stack` and `data-label` is left alone.
- A3. The phone query stays `max-width:40rem`, matching fast mode. 390 px is inside it. 1280 px is outside it.
- A4. The appended card rules come after the page's own style. They set `white-space:normal` on stacked cells and define `--line` and `--muted` on `table.stack`, per KTD4.
- A5. Screenshots use a representative bold full HTML fixture, not a live model call.

### High-Level Technical Design

```mermaid
flowchart TD
  html[Full HTML body] --> style{style bold and mode full?}
  style -->|no| keep[Leave tables as sanitized]
  style -->|yes| count{first row has 4 or more columns?}
  count -->|no| keep
  count -->|yes| cards[Add stack and data-label]
  cards --> phone{viewport max-width 40rem?}
  phone -->|yes| stacked[Stacked cards, no sideways scroll]
  phone -->|no| table[Same table as today]
```

### Sequencing

U1, U2, and U3 do not depend on each other. U4 is last so the screenshots show the finished page. Each unit is one commit, in that order: #69, #78, #79, #72.

## Implementation Units

### U1. PDF task boxes

**Goal:** PDF HTML turns a leading task mark into ☐ or ☑ and drops the literal bracket text.

**Requirements:** R1. KD1. KTD1.

**Dependencies:** none.

**Files:** `src/artifactsmith/renderers/md_parse.py`, `tests/unit/test_md_parse.py`

**Approach:** In the list branch of `blocks_to_simple_html`, treat a leading `[ ]`, `[x]`, or `[X]` as a box plus the rest of the item. Use the marks in KTD1. Leave every other list item on the current inline path.

**Execution note:** Add the failing unit test first, then the list-item change.

**Patterns to follow:** The leading-heading skip already in `blocks_to_simple_html`, and `test_pdf_html_skips_only_matching_leading_h1`.

**Test scenarios:**

- Happy path: `- [ ] one` and `- [x] two` produce □ and ■, and the HTML does not contain `[ ]`.
- Edge: a bullet whose text is not a task mark still renders that text. `[x]` and `[X]` both check. A task mark after other words stays literal.
- Error: none. Bad Markdown stays a normal list item.

**Verification:** The new test passes, and the existing PDF HTML tests in the same file still pass.

### U2. Fast HTML task boxes

**Goal:** Fast HTML shows □ and ■, and the words "Not done" and "Done" are not visible plain text.

**Requirements:** R2. KD1. KTD2.

**Dependencies:** none.

**Files:** `src/artifactsmith/layout/parse.py`, `src/artifactsmith/layout/css.py`, `tests/unit/test_layout.py`

**Approach:** `_item` keeps the words and puts the glyph outside the screen-reader element. The hide rule goes in `sheet_extra`. Rendering still goes through `render_layout`, which already sanitizes CSS.

**Execution note:** Add the failing render test first. Confirm the new rule is still present after `sanitize_html_document`.

**Patterns to follow:** `sheet_extra` for rules that must not grow `stylesheet()`. `render_layout` in `tests/unit/test_layout.py`.

**Test scenarios:**

- Happy path: `render_layout` of `- [ ] a` and `- [x] b` contains □ and ■. After the screen-reader elements are removed, the remaining text has neither "Not done" nor "Done".
- Edge: a plain bullet has no box and no those words. The hide rule uses only allowlisted properties and survives sanitizing. `stylesheet("bold")` stays under 8192 bytes.
- Integration: when headless Chrome is present, at 390 px the visible text of that page does not contain those words as plain text. Skip that check when Chrome is absent.

**Verification:** The new test passes, and `test_fast_pages_fit_a_phone_width` still passes when Chrome is present.

### U3. Word title once

**Goal:** Word does not print the title twice when the body's first heading matches it.

**Requirements:** R3. KTD3.

**Dependencies:** none.

**Files:** `src/artifactsmith/renderers/docx_renderer.py`, `tests/unit/test_pdf_docx_xlsx_render.py`

**Approach:** Skip that one heading with the same match the PDF HTML builder uses. Keep writing the document title. Do not change PDF behavior.

**Execution note:** Add the failing docx assertion first.

**Patterns to follow:** `test_pdf_html_skips_only_matching_leading_h1` and `test_pdf_html_preserves_other_headings`. Open the docx with python-docx and read paragraph text, as `tests/unit/test_pdf_docx_xlsx_render.py` already does.

**Test scenarios:**

- Happy path: title "Sample kickoff meeting notes" and a body whose first heading is that string. The first two paragraphs are not both that title. The title still appears once.
- Edge: a different case or surrounding spaces still counts as a match. A different first heading, a level-2 heading with the same words, a heading after a paragraph, and a second heading that repeats the title all stay.
- Error: an empty title does not add a title paragraph and does not drop the body heading.

**Verification:** The new test passes, and the existing docx tests in that file still pass.

### U4. Bold full-mode phone tables

**Goal:** A bold full-mode table of four or more columns is cards at 390 px and the same table as today at 1280 px.

**Requirements:** R4. KD2. KTD4.

**Dependencies:** none.

**Files:** `src/artifactsmith/renderers/html_tables.py`, `src/artifactsmith/builder.py`, `tests/unit/test_html_tables.py`

**Approach:** Add a small rewriter and call it from the full HTML finish path only when the style is bold. Fast builds do not call it. Count columns per A2. Build the header row per KTD4. Append the card rules per A3 and A4. Leave links, images, and cell text in place. Keep the module short.

**Execution note:** Prove the rewriter with a fixture before wiring it. Then open the wired page in Chrome at 390 px and 1280 px and save both screenshots for the pull request.

**Patterns to follow:** The `stack` rules in `src/artifactsmith/layout/css.py` `_TAIL`. `sanitize_html` as the step that must run before the rewrite, so the added CSS has to pass the allowlist. The Chrome debug helper in `tests/unit/test_layout.py` is the pattern for the 390 px scroll check.

**Test scenarios:**

- Happy path: a bold full HTML document with a four-column table gains `stack`, a `data-label` on each body cell, and the phone card CSS, including `white-space:normal` and concrete `--line` and `--muted`. A table whose header is a row of `th` cells and has no `thead` gets a `thead` and labels from that row. At 390 px the page's scroll width fits the viewport and the header row is not shown as a table head. At 1280 px the header row is shown and the table is not laid out as cards.
- Edge: a three-column table is not rewritten. A second four-column table on the same page is rewritten. A table that already has `stack` and `data-label` is not doubled. A cell that contains a link still contains that link.
- Error: house, editorial, playful, terminal, and swiss full HTML are not rewritten. A fast-mode render of a four-column, six-row table still scrolls, matching the existing layout test.
- Integration: screenshots at 390 px and 1280 px of the bold fixture are produced for the pull request.

**Verification:** The unit tests pass. Both screenshots exist. At 390 px the fixture does not scroll sideways. At 1280 px it is still a table.

## Verification Contract

| Check | Command | Proves |
|---|---|---|
| Unit tests | `make test` | U1 through U4, including the existing PDF, layout, and docx tests |
| Lint | `make lint` | Ruff on `src`, `tests`, and `examples` |
| Types | `make typecheck` | `mypy --strict src` |
| Full CI | `make ci` | Lint, types, tests, and the denylist |
| Phone and desktop | Chrome at 390 px and 1280 px | R4, plus the two screenshots |

CI unit jobs also run `pytest --cov=artifactsmith --cov-fail-under=80`. New code has to stay above that floor.

## Definition of Done

- U1 through U4 meet their verification lines.
- `make ci` passes.
- The pull request body says what changed and how it was verified for #69, #72, #78, and #79, and it contains the lines `Fixes #69`, `Fixes #72`, `Fixes #78`, and `Fixes #79`.
- The 390 px and 1280 px screenshots are attached to the pull request.
- Links, images, charts, and tables are still present.
- The pull request is not merged.
- Abandoned experiment code is not left in the diff.

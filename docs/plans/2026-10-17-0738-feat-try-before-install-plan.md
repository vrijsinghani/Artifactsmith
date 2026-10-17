---
title: "Try it before you install - Plan"
date: 2026-10-17
type: feat
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Try it before you install - Plan

## Goal Capsule

**Objective:** A visitor meets ArtifactSmith as an open-source MIT server first. The hosted copy is a way to see it work before installing, at `/try`.

**Means:** New copy on the hosted page, card, shared-page footer, and user-facing errors. `GET /trial` redirects to `/try`. Existing form posts, verify links, tokens, and caps stay.

**Authority:** The replacement copy in the repositioning note wins over the old trial wording. Env var names and internal identifiers stay.

**Execution:** One pull request, ready for review, branched from `main` at `72f2273`.

**Stop:** Stop if a public URL contains a loopback, localhost, or private address, if `GET /trial/verify` stops working, or if HTTP bearer auth starts accepting a missing token.

## Product Contract

### Problem Frame

The hosted page is titled "ArtifactSmith trial" and says "Start a trial" and "20 free builds". It does not link the repo. Shared pages say "Trial page." People read that as a paid trial. The project is an open-source MCP server. The hosted copy is only a way to see it work before installing.

### Requirements

- R1. `GET /try` is the hosted page. Its title is `Try ArtifactSmith before you install`. The heading is `Try it before you install`. The body is the replacement copy: what ArtifactSmith is, then the GitHub link with "Open source, MIT", then the hosted-copy paragraph (Claude Haiku 5.5, capped at the build limit, shared links last 30 days), then the email form.
- R2. A plain `GET /trial` returns 301 with `Location: /try`. `POST /trial`, `GET /trial/verify`, and `POST /trial/verify` keep their handlers. Magic links still use `/trial/verify`.
- R3. Open Graph and Twitter tags use the new title and description. `og:url` is the public origin plus `/try`. The card image stays at `/static/og-trial.png`, 1200x630, and its third line is `Open source (MIT)  ·  Try it before you install`. Preview tags stay off when `AM_API_URL` is not a public `https` origin. The page stays `noindex`.
- R4. The shared-page footer says `Made with ArtifactSmith (open source).` linked to `https://github.com/vrijsinghani/Artifactsmith`, then the existing Report abuse link.
- R5. User-facing errors no longer say "trial", "free trial", "free builds", or "no card". The cap, spots, used-up, closed, publish, model, email subject, and oversized-input strings use the replacement wording. Internal names, table names, and `AM_TRIAL_*` stay.
- R6. The README, after the opening pitch, links `https://artifactsmith.neuralami.ai/try` with the "Try it before you install" line. The hosted section is retitled and the prose matches the new strings. Env names stay. The public home page shows that same try link after the GitHub link.
- R7. Tests cover the redirect, the matching tool-free page copy, a store-free error path that returns the new wording, and a legacy `POST /trial`. A 390px screenshot of `/try` goes in the pull request.
- R8. Every URL written into a public page, redirect, or card is a public `https` URL or a same-origin path. No loopback, localhost, or private address.

### Scope Boundaries

Do not rename env vars, database columns, token kind `trial`, or the cookie name. Do not change rate limits, spend holds, or bearer auth. Do not edit historical changelog entries or old plan files. Do not publish the registry, edit production, or change SWAG. The live root is a static file in front of the app; this change serves the updated home page from the app and does not replace that production file.

### Key Decisions

- `/try` is the canonical page. `/trial` remains a permanent redirect for GET only, so bookmarks and the old card URL still land on the new page.
- The CSRF cookie path is `/try` for the new form and `/trial` for verify and a legacy `POST /trial`. A single path of `/try` would not be sent to `/trial/verify`, and those links must keep working. (Directive from the note was "change the cookie path to `/try`"; that alone breaks verify.)
- The build cap in the used-up error and on the page is `AM_TRIAL_BUILD_LIMIT`, which defaults to 20, so the default text matches the copy and a changed limit does not lie.
- The card filename stays `og-trial.png`. Pixels change. The old URL keeps working.

## Planning Contract

### Key Technical Decisions

- KTD1. Register `GET` and `POST /try` on the existing pages app, and bypass bearer auth for `/`, `/try`, and `/try/` the same way `/trial` is already public. `POST /` stays on the bearer app.
- KTD2. `GET /trial` and `GET /trial/` return `RedirectResponse("/try", status_code=301)`. The location is a path, not an absolute URL taken from the request.
- KTD3. Issue `am_trial_csrf` with `Path=/`. `/try` and `/trial/verify` share no longer prefix, and a cookie limited to `/try` is not sent when an old verify link posts. The new form posts to `/try`. `POST /trial` still accepts the same cookie.
- KTD4. Replace the PNG bytes in place. Do not add an image library to the runtime.
- KTD5. Serve `static/home.html` at `GET /` with the page's own script-free CSP. The GitHub button stays first. The try link is `https://artifactsmith.neuralami.ai/try`. The "no hosted version" sentence is rewritten so it does not deny that copy.

### Assumptions

`AM_API_URL` on the hosted copy is `https://artifactsmith.neuralami.ai`. Tests keep using `https://trial.example`. The public root file in front of the app is not in this repo and is not edited here.

## Implementation Units

### U1. Hosted page, redirect, and copy

**Goal:** `/try` shows the new page, and the old GET redirects.

**Requirements:** R1, R2, R3, R5, R8.

**Dependencies:** none.

**Files:** `src/artifactsmith/web/trial_pages.py`, `src/artifactsmith/server.py`, `src/artifactsmith/trial.py`, `src/artifactsmith/service.py`, `src/artifactsmith/llm.py`, `src/artifactsmith/web/static/og-trial.png`, `tests/unit/test_trial.py`, `tests/unit/test_browser_forms.py`, `tests/unit/test_llm.py`.

**Approach:** One page renderer. Redirect only GET `/trial`. Keep verify URLs. Swap the user-facing strings listed in the note. Regenerate the PNG with the same size and the new third line.

**Test scenarios:**

- `GET /trial` with redirects off is 301 and `Location: /try`, and the body has no private host.
- `GET /try` with `https://trial.example` contains the new title, heading, GitHub link before the hosted paragraph, "Open source, MIT", the new og/twitter tags, `og:url` ending in `/try`, and `noindex`. It does not contain "free builds", "Start a trial", "free trial", or "no card".
- A private `AM_API_URL` omits preview tags.
- `POST /try` still mints a token. `POST /trial` with a `/trial` cookie still mints one. `POST /trial/verify` still shows the token once.
- The used-up error, closed error, and publish error use the new wording and name the public repo.
- The PNG is 1200x630 and public with no bearer challenge.

**Verification:** `pytest tests/unit/test_trial.py tests/unit/test_browser_forms.py tests/unit/test_llm.py`

### U2. Footer, README, and home page

**Goal:** Shared pages and the two front doors lead with the repo, then the try link.

**Requirements:** R4, R6, R8.

**Dependencies:** U1.

**Files:** `src/artifactsmith/trial.py`, `src/artifactsmith/web/routes.py`, `src/artifactsmith/web/static/home.html`, `pyproject.toml`, `README.md`, `CHANGELOG.md`, `docs/operations.md`, `tests/unit/test_trial.py`.

**Approach:** Footer link uses the repo URL. README line goes after the pitch. Home HTML is the current public page plus the try link after "View on GitHub", served at `GET /`.

**Test scenarios:**

- A hosted share footer contains the repo link and "Made with ArtifactSmith (open source)." and does not contain "Trial page."
- `GET /` is 200, contains the GitHub URL before `https://artifactsmith.neuralami.ai/try`, and contains no "trial", "free builds", loopback, or private address.
- `GET /static/other.png` is still 401.

**Verification:** `pytest tests/unit/test_trial.py` and a 390px screenshot of `/try`.

## Verification Contract

- `make lint` and `make typecheck` if present, otherwise `ruff check` and `mypy`.
- `pytest tests/unit/test_trial.py tests/unit/test_browser_forms.py tests/unit/test_llm.py`
- Phone screenshot at 390px of `/try`, saved for the pull request.

## Definition of Done

- `/try` matches the replacement copy and links the repo above the fold.
- `GET /trial` is a 301 to `/try`. Posts and verify links still succeed.
- Card, footer, and user-facing errors use the new wording.
- README and the in-app home page link the try URL after the repo.
- Tests pass. The pull request is ready and includes the 390px screenshot.
- Bearer auth, quotas, and production are unchanged.

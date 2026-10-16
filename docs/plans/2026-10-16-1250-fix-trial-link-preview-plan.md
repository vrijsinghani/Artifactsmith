---
title: "Trial link preview - Plan"
date: 2026-10-16
type: fix
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Trial link preview - Plan

## Goal Capsule

**Objective:** A person who pastes the public trial URL on X, Reddit, or Slack sees a preview card naming ArtifactSmith, what it does, and 20 free builds. The trial page stays out of search indexes.

**Means:** Open Graph and Twitter Card tags on `/trial`, plus one public 1200x630 PNG (KTD1, KTD2).

**Authority:** The request's tag list, image words, noindex rule, and public-URL rule win over a nicer card or a new landing page.

**Execution:** One pull request, ready for review, branched from `main` at `b1a6d6e`.

**Stop:** Stop if the image requires a bearer token, if `og:url` or `og:image` contains a loopback, localhost, or private address, or if the robots meta no longer says noindex.

The executor implements the change, reviews it, and opens that pull request.

## Product Contract

### Problem Frame

X shows `https://artifactsmith.neuralami.ai/trial` as plain text. The page is HTML from the app, with a robots `noindex` tag and no preview tags. Crawlers that build cards read those tags and then fetch `og:image` as its own request.

### Requirements

- R1. When `AM_API_URL` is `https` and its hostname is a DNS name, every HTML response from `/trial` includes `og:title`, `og:description`, `og:url`, and `og:image`, plus `twitter:card` of `summary_large_image`, `twitter:title`, `twitter:description`, and `twitter:image`. When that origin is `http`, an IP, `localhost`, or a loopback or private address, omit those preview tags.
- R2. `og:url` is the public trial URL. `og:image` and `twitter:image` are the absolute `https` URL of the card. None of those values is a loopback, localhost, or private address.
- R3. The card is a 1200x630 PNG whose words are ArtifactSmith, "Agent output in, real page or PDF out", and "20 free builds". It is plain. It is served at `/static/og-trial.png` with no authentication, `Content-Type: image/png`, status 200, and a public cache header.
- R4. The trial page keeps its existing robots `noindex` meta. My pages does not gain the trial card tags.
- R5. There is no public page at `/`. Do not add one.

### Scope Boundaries

Share-page `og:title` stamping stays as it is. The HTML page CSP stays `img-src 'none'`; the card is not drawn in the page. Hacker News does not render an Open Graph card, so this change does not add HN-specific markup. The image words stay the three lines above even if `AM_TRIAL_BUILD_LIMIT` changes later.

## Planning Contract

### Key Technical Decisions

- KTD1. Serve only `/static/og-trial.png`, by sending that exact path to the pages app before bearer auth. The pages app already applies the host allow-list. A path under `/trial/` would also be public, and it would mix a file with the HTML routes. A `/static/` prefix would publish every later file dropped beside the card. The nginx and Caddy snippets already proxy `/`, so the proxy does not need a new location. The page `Content-Security-Policy` does not apply to the crawler's separate GET of the image. Do not copy `security_headers` onto the PNG: that helper sets `Cache-Control: no-store` and `Cross-Origin-Resource-Policy: same-origin`. The PNG response is `image/png`, `Cache-Control: public, max-age=86400`, `X-Content-Type-Options: nosniff`, and no `WWW-Authenticate`.
- KTD2. Build `og:url`, `og:image`, and the Twitter image from `AM_API_URL` (`CFG.api_url`) only when that origin is `https` and its hostname is a DNS name. If it is `http`, an IP, `localhost`, or a loopback or private address, omit the preview tags. The image route itself stays available. Production uses `https://artifactsmith.neuralami.ai`, so the tags there are `https://artifactsmith.neuralami.ai/trial` and `https://artifactsmith.neuralami.ai/static/og-trial.png`.
- KTD3. Commit the PNG. Do not draw it on each request, and do not add an image library to the runtime. The title is ArtifactSmith. The description is "Agent output in, real page or PDF out. 20 free builds." Twitter title and description match those. Colors follow the page: white, `#1c1917`, and a short `#0b5cab` rule.

### Assumptions

`AM_API_URL` on the hosted trial is `https://artifactsmith.neuralami.ai`. Tests set `https://trial.example` and assert that origin. Slack, Reddit, and X read the tags above. Hacker News will still show a title and a URL.

## Implementation Units

### U1. Trial preview tags and public card

**Goal:** `/trial` advertises a public card, and the card file is fetchable with no token.

**Requirements:** R1, R2, R3, R4, R5.

**Dependencies:** none.

**Files:** `src/artifactsmith/web/pages.py`, `src/artifactsmith/web/trial_pages.py`, `src/artifactsmith/server.py`, `src/artifactsmith/web/static/og-trial.png`, `pyproject.toml`, `tests/unit/test_trial.py`, `README.md`, `CHANGELOG.md`.

**Approach:**

- Add an optional head fragment to `_shell`. Trial HTML passes the preview tags. Login and catalog pages pass nothing.
- Escape attribute values. `og:url` is the public origin plus `/trial`. The image URL is that origin plus `/static/og-trial.png`.
- In `ApiApp`, send the exact path `/static/og-trial.png` to the pages app. Register one GET route that returns the committed PNG.
- Generate the PNG once and commit it. Check width and height from the IHDR chunk in the test, not by eye alone.
- List `static/og-trial.png` in `[tool.setuptools.package-data]` for `artifactsmith.web`. The route reads that file with `importlib.resources`. The Docker image runs `pip install .` and does not keep the source tree, so a path next to the checkout is not enough.

**Patterns to follow:** `security_headers` and `_shell` in `src/artifactsmith/web/pages.py`. The `/trial` bypass in `ApiApp.__call__`. Trial `TestClient` cases in `tests/unit/test_trial.py` that set `CFG.api_url`.

**Test scenarios:**

- GET `/trial` with `CFG.api_url` of `https://trial.example` contains each tag in R1. `og:url` is `https://trial.example/trial`. Both image tags are `https://trial.example/static/og-trial.png`. The body still has `content=noindex` and does not contain `127.0.0.1`, `localhost`, or `192.168.`.
- GET `/trial` with `CFG.api_url` of `http://127.0.0.1:8780` contains no `og:url` and no `og:image`.
- GET `/app/login` does not contain `og:image` or `summary_large_image`.
- GET `/static/og-trial.png` with no `Authorization` header returns 200, `content-type` `image/png`, a PNG signature, IHDR width 1200 and height 630, a public `Cache-Control`, and no `WWW-Authenticate`.
- GET `/static/og-trial.png` on a Host outside `AM_ALLOWED_HOSTS` is rejected the same way other pages are.
- GET `/static/other.png` is not served as a public file.

**Verification:** The new tests fail before the tags and route exist, and pass after.

## Verification Contract

Run the trial page tests in `tests/unit/test_trial.py`, then `make ci`. The image route must open the PNG through the installed package, because `docker/Dockerfile` installs with `pip install .`.

## Definition of Done

- R1 through R5 hold.
- The pull request is ready for review, not a draft, and includes the rendered card image.
- Abandoned generation scripts are not left in the diff. The committed PNG is the card.

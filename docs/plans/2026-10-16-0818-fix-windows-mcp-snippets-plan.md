---
title: "fix: Windows-safe trial MCP snippets"
date: 2026-10-16
type: fix
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# fix: Windows-safe trial MCP snippets

## Goal Capsule

Windows Claude Desktop users and Cursor users can paste the trial token page and connect to the public MCP server. The Desktop snippet follows the current mcp-remote workaround for clients that split spaces inside args. Cursor keeps its native URL and headers form. The page also names Anthropic's custom-connector route in one short line. The token is still shown once, the page still names one public MCP URL, and the layout still fits a 390px-wide phone.

The executor finishes the change, reviews it, and opens a ready-for-review pull request from `main` at `ddd019c`.

Stop if a snippet arg that mcp-remote will receive still contains `Bearer ` with a space, if the live token is copied into a snippet, or if the page shows a second server URL.

## Product Contract

### Requirements

- R1. The Claude Desktop snippet uses mcp-remote with args `--header` and `Authorization:${AUTH_HEADER}`, and `"env": {"AUTH_HEADER": "Bearer YOUR_TOKEN"}`. No mcp-remote arg contains `Bearer ` with a space.
- R2. The Cursor snippet stays Cursor's native `url` plus `headers` form. It does not use mcp-remote.
- R3. The token page has one short line for Anthropic's public-server route: Claude Desktop, Customize, then Connectors, then Add custom connector, the public MCP URL, and an Authorization request header.
- R4. The token is shown once, in the readonly field. Snippets keep the `YOUR_TOKEN` placeholder.
- R5. The page names only the configured public MCP URL (`AM_API_URL` plus `/mcp`). On the hosted trial that origin is `https://artifactsmith.neuralami.ai`.
- R6. The token page still wraps inside a 390px-wide viewport.

### Scope Boundaries

Claude Code's HTTP snippet and the generic text snippet stay. Login limits, sign-up caps, and the magic-link flow stay. The help-center URL is cited in the pull request, not printed as a second link on the page.

## Planning Contract

### Key Technical Decisions

- KTD1. Use the env workaround from the current mcp-remote README (`Authorization:${AUTH_HEADER}`, no space around the colon; `AUTH_HEADER` is `Bearer YOUR_TOKEN`). A header file would also avoid the space, and it would keep a real secret out of the process list, but this page never prints the live token. The placeholder is what the user pastes. `npx -y` stays; that arg has no space.
- KTD2. Leave the Cursor block on `url` and `headers`. It already has no args, so the space bug does not apply.
- KTD3. The connector sentence uses the help center's words for an individual plan: Customize, Connectors, Add custom connector, and an Authorization request header. Source: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp

### Assumptions

The connector URL is the same public origin the snippets already use, not a hardcoded host. Production shows `https://artifactsmith.neuralami.ai/mcp` when `AM_API_URL` is `https://artifactsmith.neuralami.ai`.

## Implementation Units

### U1. Windows-safe Desktop snippet and connector line

Files: `src/artifactsmith/web/trial_pages.py`, `tests/unit/test_trial.py`, `README.md`, `CHANGELOG.md`.

Change the Desktop JSON to the README workaround. Add the one connector sentence on the token page. Update the existing token-page assertions. Add a test that parses every mcp-remote snippet and rejects any arg containing `Bearer `.

## Verification Contract

Run the token-page tests in `tests/unit/test_trial.py`, then `make ci`. Open the token page at a 390px viewport and confirm the sentence and snippets wrap with no sideways page scroll.

## Definition of Done

- R1 through R6 hold on the rendered token page.
- The new arg test fails on the old Desktop snippet and passes after the change.
- The pull request is ready for review, not draft, and its body cites the Anthropic help-center URL.

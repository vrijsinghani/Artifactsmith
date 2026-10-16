---
title: Hosted trial - Plan
type: feat
date: 2026-10-16
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Hosted trial - Plan

## Goal Capsule

- **Objective:** A stranger can open `/trial`, verify an email, and receive one trial workspace token that can run a bounded number of builds. Trial spend cannot pass a daily cap, and internal tokens keep today's limits.
- **Means:** Reuse hashed workspace tokens, the login-limit address helper, stdlib SMTP, and the `disposable-email-domains` blocklist. Reserve worst-case model cost in the same transaction as the job, then settle to measured usage.
- **Authority:** The brief, then the additions in this session. A later idea that lets a trial caller pick the model, call `publish`, or fill every build slot loses.
- **Execution:** One draft pull request from `main` `a5e2d6a`.
- **Stop:** Do not merge or deploy. No payments, dashboards, or OAuth.

## Product Contract

### Requirements

- R1. `/trial` takes an email, sends one magic link, and shows a new token once plus an MCP snippet that uses `YOUR_TOKEN`.
- R2. One trial per normalized email. Gmail dots and `+` tags collapse. Disposable domains from `disposable-email-domains` are rejected. An address is limited to `AM_TRIAL_SIGNUPS_PER_IP` (default 3) per day.
- R3. Trial tokens are ordinary revocable tokens with kind `trial` and no `publish` permission. `publish` is refused even if the permission is added later.
- R4. Lifetime builds default to 20 (`AM_TRIAL_BUILD_LIMIT`). Trial hourly limit defaults to 10 (`AM_TRIAL_BUILDS_PER_HOUR`). `AM_BUILDS_PER_HOUR` stays 20 for every other token.
- R5. Trial builds always use `AM_TRIAL_MODEL` (default `anthropic/claude-haiku-5-5`) and send a 16,000-token output cap. The caller's model name is ignored.
- R6. Each trial build reserves 3 calls of 200,000 input plus 16,000 output tokens at `AM_TRIAL_PRICE_IN` / `AM_TRIAL_PRICE_OUT` (default 0.10 / 0.50 per 1M) against `AM_TRIAL_DAILY_USD` (default 10). The hold settles to measured usage. Internal tokens never consult the cap.
- R7. At most one trial build runs at a time, so the other `AM_MAX_BUILDS` slots stay available to internal jobs.
- R8. `AM_TRIAL_ENABLED=false` (the default) closes sign-up and blocks trial builds. SMTP without `AM_SMTP_HOST` and `AM_SMTP_FROM` does not send mail.
- R9. Trial tool responses include `builds_remaining`. The exhausted-quota error links to the README. The daily-cap error says it resets at 00:00 UTC.
- R10. Trial share links last at most 30 days. Served trial HTML gets `noindex` and a report-abuse mailto from `AM_TRIAL_ABUSE_EMAIL`. Trial workspaces with no build for 30 days are deleted.

### Scope Boundaries

- No billing and no paid tier.
- Internal tokens, their hourly quota, and their model choice stay as they are.
- Public errors do not include hostnames, raw URLs, or mailbox credentials.

## Planning Contract

### Key Technical Decisions

- KTD1. Magic links and trial tokens are stored as SHA-256 only. The raw token is rendered once and is not written into the MCP snippet.
- KTD2. Cost is integer micro-dollars. The reserve and the job insert share one SQLite transaction. Missing usage settles at the reserved amount so a silent provider cannot drop the hold.
- KTD3. Workers take an internal job before a trial job, and a second trial job waits while one trial build is in progress.
- KTD4. `disposable-email-domains` supplies the blocklist. Mail uses the stdlib `smtplib`. No new auth framework.

## Implementation Units

### U1. Trial sign-up and token

- **Files:** `src/artifactsmith/trial.py`, `src/artifactsmith/web/trial_pages.py`, `src/artifactsmith/web/routes.py`, `src/artifactsmith/server.py`, `src/artifactsmith/db.py`, `src/artifactsmith/config.py`
- **Verification:** one email, disposable domain, IP limit, kill switch, magic link shown once.

### U2. Quotas, model, spend, slots

- **Files:** `src/artifactsmith/service.py`, `src/artifactsmith/llm.py`
- **Verification:** lifetime quota, separate hourly limits, publish refusal, forced model and `max_tokens`, reserve then settle, one trial slot.

### U3. Served page chrome and retention

- **Files:** `src/artifactsmith/server.py`, `src/artifactsmith/service.py`
- **Verification:** share expiry cap, footer and noindex, purge after 30 idle days.

## Verification Contract

Unit tests in `tests/unit/test_trial.py` and `tests/unit/test_llm.py`. `make denylist` stays clean. CI runs lint, types, and the suite.

## Definition of Done

- R1–R10 hold.
- The pull request lists the new env vars and the end-to-end check.
- The diff does not change the internal hourly default.

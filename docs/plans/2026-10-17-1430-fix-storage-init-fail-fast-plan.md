---
title: "Fail fast when storage is down - Plan"
date: 2026-10-17
type: fix
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Fail fast when storage is down - Plan

## Goal Capsule

**Objective:** An operator whose storage service is down gets a failed startup within about a minute, with the underlying error type and a check they can act on.

**Means:** A short-timeout init client, a 60-second deadline beside the attempt cap, and an unwrapped cause in the existing failure message (KTD1, KTD2, KTD3, KTD4).

**Authority:** The numbered fix and the two README sentences in the request win over looser wording.

**Execution:** One pull request, ready for review, branched from `main` at `8f2965a`.

**Stop:** Stop if the server client timeouts or retry count change, if the endpoint value is printed, or if the README gains a slogan or the word trial.

## Product Contract

### Summary

`storage-init` will exit 1 within 75 seconds when storage is unreachable, name the underlying error type, and tell the operator to check the storage service and `AM_STORE_ENDPOINT`. The server keeps its current storage client. The compose smoke job stops storage and requires that exit inside 150 seconds. The README requirements line and the post-edit restart sentence match the request.

### Problem Frame

With storage unreachable, `storage-init` takes about 88 minutes to exit. Each of 60 attempts takes about 88 seconds because the shared client uses a 10-second connect timeout and three attempts on both `head_bucket` and `create_bucket`. The failure JSON then reports `StoreError`, which is the wrapper `ensure_bucket` raises, not the connection error underneath. The quickstart also tells people to put a model key in `.env` after the stack is already up, and does not say to recreate the containers. The requirements line says a model key is required, while the quickstart reaches a share URL without one.

### Requirements

- R1. With storage stopped, `storage-init` exits 1 within 75 seconds and names the underlying error type, such as `ConnectTimeoutError` or `EndpointConnectionError`, instead of `StoreError`.
- R2. `storage-init` uses its own storage client: 2-second connect timeout, 5-second read timeout, and no retries. The server keeps its current client settings.
- R3. The attempt loop stops at 60 attempts or 60 seconds, whichever comes first.
- R4. The final message says to check that the storage service is running and that `AM_STORE_ENDPOINT` points at it. It does not print the endpoint value.
- R5. Unit tests cover the init client, the server client, the deadline, and the unwrapped cause.
- R6. The compose smoke job stops storage and requires `storage-init` to exit 1 within 150 seconds.
- R7. The requirements line is: `Requirements: Docker Engine with the Compose plugin. A model key (OpenAI-compatible API or local endpoint) is needed to build pages; publishing ready-made Markdown or HTML works without one.`
- R8. After the paragraph that says the server reads model settings from `.env`, the README says to run `docker compose up -d` again so the change takes effect.
- R9. The new README sentences are plain and factual. They contain no hook, no slogan, and not the word trial.

### Acceptance Examples

- AE1. Covers R1, R3, R4. Given storage is stopped, when `storage-init` runs, then it exits 1 within 75 seconds, the cause is `ConnectTimeoutError` or `EndpointConnectionError`, and the message names the storage service and `AM_STORE_ENDPOINT` without the endpoint value.
- AE2. Covers R2. Given the server builds a store, when its client is created, then the connect timeout is 10 seconds, the read timeout is 60 seconds, and `retries` `max_attempts` stays 3. That value is three retries after the first request, which is the current setting.
- AE3. Covers R6. Given the compose smoke stack is up, when storage is stopped, then the job fails unless `storage-init` exits 1 inside 150 seconds and names an underlying connection error.

### Scope Boundaries

- The server client settings stay as they are today.
- The endpoint value is not printed.
- Publish, the empty-key error, and the hosted page are unchanged.
- `make smoke` is not given a matching down-storage step.

#### Deferred to Follow-Up Work

- Mirror the stopped-storage check in `make smoke` if local smoke and CI should stay identical.

### Sources

- `src/artifactsmith/cli.py` `cmd_storage_init` loops `STORAGE_INIT_ATTEMPTS` (60), records `type(exc).__name__`, sleeps 1 second, and prints `{"error": "object store not ready", "attempts", "cause"}`.
- `src/artifactsmith/store.py` builds one client with `connect_timeout` 10, `read_timeout` 60, and `retries` `max_attempts` 3. In current botocore, that `max_attempts` key counts retries after the first request, so 3 is three retries and 0 is none. `total_max_attempts` of 1 is the same no-retry outcome on a different key. `ensure_bucket` calls `head_bucket`, then `create_bucket` on a generic error, and raises `StoreError` from the create failure.
- `src/artifactsmith/service.py` constructs `Store()` for the server.
- `tests/unit/test_cli.py` mocks `Store` and expects 60 attempt lines plus cause `ConnectionError` when the mock raises `ConnectionError`. It also forbids the host and `http://` in the output.
- `.github/workflows/ci.yml` job `compose smoke` starts the test stack, runs the end-to-end modules, and tears down. It does not stop storage.
- `README.md` requirements line and the paragraph after the quickstart publish example.
- `tests/unit/test_readme_tools.py` asserts the requirements line contains `OpenAI-compatible API key`. That assertion has to change with R7.
- Single-pass behavior trace: a missing store config raises `RuntimeError` before the loop. A connection failure becomes `StoreError`, so today's cause is `StoreError`. The head failure's type name is only inside the wrapper message. Botocore's client `retries` `max_attempts` counts retries after the first request (`config.py`: 0 means no retries; `args.py` adds one when converting that key to `total_max_attempts`).

## Planning Contract

### Key Technical Decisions

- KTD1. **Init client is the same `Store` constructor with different timeouts.** `storage-init` passes a 2-second connect timeout, a 5-second read timeout, and `retries` `max_attempts` 0, which is the first request and no retries. `Store()` with no arguments keeps 10 seconds, 60 seconds, and `max_attempts` 3, which is what the server uses. A copied client in the CLI was rejected because path-style addressing and the signature version would drift from `ensure_bucket`.
- KTD2. **The 60-second deadline is checked before each attempt on a monotonic clock.** An in-flight call is bounded by the init timeouts in KTD1, so the process stays inside R1 without cancelling a socket. The 1-second sleep stays between attempts that are still allowed.
- KTD3. **The reported cause walks `StoreError.__cause__` until the exception is not `StoreError`.** A `StoreError` with no cause stays `StoreError`. The wrapper text is not parsed for a type name.
- KTD4. **The guidance in R4 replaces the current JSON `error` string.** `attempts` is how many attempts started, not always 60. `cause` is the name from KTD3. Stderr stays the attempt lines. The endpoint value is not added to either stream.

### High-Level Technical Design

```mermaid
flowchart TD
  start[Start] --> gate{Attempts left and under 60 seconds}
  gate -->|no| fail[Exit 1 with unwrapped cause and guidance]
  gate -->|yes| call[ensure_bucket on the init client]
  call -->|ready| ok[Exit 0]
  call -->|error| again{Another attempt still allowed}
  again -->|yes| pause[Sleep 1 second]
  pause --> gate
  again -->|no| fail
```

### Assumptions

- A1. No retries means botocore client `retries` `max_attempts` of 0. That key counts retries after the first request. `total_max_attempts` of 1 is the same outcome and is not required.
- A2. R1's 75 seconds is the `storage-init` process bound: the 60-second deadline plus one in-flight attempt under KTD1. R6's 150 seconds is the CI kill, including container start. CI does not treat compose startup time as part of the 75-second bound.
- A3. A stopped compose service may raise `ConnectTimeoutError` or `EndpointConnectionError`. CI accepts either, and rejects a cause of only `StoreError`.
- A4. The restart sentence in R8 follows the paragraph after the no-key share, because that is when `.env` is edited on an already running stack. The first quickstart block already runs `docker compose up -d --build`.

### Sequencing

U1 before U2. U2, U3, and U4 can land in one pull request. U3 depends on U2's exit behavior.

## Implementation Units

### U1. Init and server storage clients

**Goal:** `storage-init` builds a short-timeout client, and the server client stays on the current settings.

**Requirements:** R2, R5. AE2.

**Dependencies:** none.

**Files:** `src/artifactsmith/store.py`, `tests/unit/test_store.py`

**Approach:** Add optional timeout and attempt arguments on `Store`, defaulting to the current server values. `ensure_bucket` stays one method. Do not add a second bucket implementation.

**Patterns to follow:** The existing `boto3.client` call in `Store.__init__`, and the fake-client tests in `tests/unit/test_store.py`.

**Test scenarios:**

- Happy path: `Store()` with config and credentials stubbed passes connect timeout 10, read timeout 60, and `max_attempts` 3 into the boto config. That 3 stays the current retry count, not a total-attempt rewrite. Existing versioning tests still pass.
- Edge: constructing `Store` with the init values passes connect timeout 2, read timeout 5, and `max_attempts` 0. Signature version and path-style addressing stay the same as the server client.
- Error: a 403 from `head_bucket` still raises `StoreError` and does not call `create_bucket`.
- Integration: none beyond the captured boto config. Live storage is U3.

**Verification:** The captured server config matches today's numbers, and the captured init config matches R2.

### U2. Deadline, unwrapped cause, and final message

**Goal:** The init loop stops at the attempt cap or the deadline, and the failure JSON names the underlying error plus the operator check.

**Requirements:** R1, R3, R4, R5. AE1.

**Dependencies:** U1.

**Files:** `src/artifactsmith/cli.py`, `tests/unit/test_cli.py`

**Approach:** `cmd_storage_init` builds the init client from U1. It records a monotonic deadline of 60 seconds and stops when that deadline or `STORAGE_INIT_ATTEMPTS` is reached. The cause follows KTD3. The JSON `error` string follows KTD4. Keep `cli.py` under 300 lines. If the loop would cross that, move only the init command into a small module and leave the other commands in place.

**Patterns to follow:** The current attempt line on stderr, the JSON success and failure payloads, and the tests that forbid the host and `http://` in combined output.

**Test scenarios:**

- Happy path: the existing flaky mock fails once and then returns ready. The command exits 0 on attempt 2 while the clock stays under 60 seconds. The success payload is unchanged.
- Error: a `StoreError` whose cause is `ConnectTimeoutError` exits 1 with cause `ConnectTimeoutError`. The same shape with `EndpointConnectionError` reports that name. The error text tells the operator to check that the storage service is running and that `AM_STORE_ENDPOINT` points at it. The output does not contain the endpoint host or `http://`.
- Edge: a `StoreError` with no cause reports `StoreError`. Mocked sleep and a clock that does not advance 60 seconds still prints 60 attempt lines, matching the current fast unit test.
- Edge: after the first failure the monotonic clock jumps past 60 seconds. The command exits 1 with `attempts` 1 and does not print attempt 2.
- Integration: none. The live stopped-storage run is U3.

**Verification:** The unit tests above pass, and a full unit run still covers the token commands in the same file.

### U3. Compose smoke when storage is stopped

**Goal:** CI stops storage and fails unless `storage-init` exits 1 within 150 seconds and names the underlying error.

**Requirements:** R1, R6. AE3.

**Dependencies:** U2.

**Files:** `.github/workflows/ci.yml`, `tests/unit/test_cli.py`

**Approach:** In the `compose smoke` job, after the end-to-end steps and before tear down, stop the `storage` service. Run `storage-init` with `--no-deps` so Compose does not start storage again. Kill the step at 150 seconds. Require exit code 1, a cause of `ConnectTimeoutError` or `EndpointConnectionError`, and the R4 guidance. Do not require the 75-second process bound inside this step, per A2. Add a unit test that the workflow text contains the stop and the 150-second bound so the step cannot disappear without a test failure.

**Patterns to follow:** The existing `compose smoke` steps in `.github/workflows/ci.yml`, and `test_compose_disables_storage_init_healthcheck_only` which reads `compose.yaml` as text.

**Test scenarios:**

- Happy path: the workflow test finds a compose smoke step that stops `storage`, runs `storage-init` with `--no-deps`, and uses a 150-second limit.
- Error: the same test fails if the stop, the `--no-deps` run, or the 150-second limit is removed.
- Integration: on a machine with Docker, stopping storage and running init exits 1 inside 150 seconds and names `ConnectTimeoutError` or `EndpointConnectionError`. That run is the CI job, not a unit test.

**Verification:** The workflow unit test passes. The compose smoke job is the live proof of AE3.

### U4. README requirements and restart sentence

**Goal:** The requirements line and the `.env` restart sentence match the request.

**Requirements:** R7, R8, R9.

**Dependencies:** none.

**Files:** `README.md`, `tests/unit/test_readme_tools.py`

**Approach:** Replace the requirements line with the R7 sentence. After the paragraph that says the server reads `AM_LLM_BASE`, `AM_LLM_KEY` or `OPENAI_API_KEY`, and `AM_DEFAULT_MODEL` from `.env`, add the sentence to run `docker compose up -d` again so the change takes effect. Update the requirements assertion that currently looks for `OpenAI-compatible API key`. Keep the rest of `test_readme_states_requirements_client_setup_and_stdio_limits` green, including the check that the connect section does not contain the word trial.

**Patterns to follow:** The factual sentences already in that README section.

**Test scenarios:**

- Happy path: the last non-empty line before `## Quickstart` equals the R7 sentence. The model-settings paragraph is followed by a sentence that says to run `docker compose up -d` again so the change takes effect.
- Error: the requirements assertion fails if the line still says an API key is required with no exception for publishing.
- Edge: the edited sections do not contain the word trial, and the connect-a-client checks in the same test still pass.

**Verification:** `test_readme_states_requirements_client_setup_and_stdio_limits` passes with the new sentences.

## Verification Contract

| Check | Command | Proves |
|---|---|---|
| Unit tests | `make test` | U1, U2, U4, and the workflow text check in U3 |
| Lint | `make lint` | Ruff on `src`, `tests`, and `examples` |
| Types | `make typecheck` | `mypy --strict src` |
| Stopped storage | compose smoke job in `.github/workflows/ci.yml` | AE3, including exit 1 within 150 seconds |

CI unit jobs also run `pytest --cov=artifactsmith --cov-fail-under=80`. New branches need coverage at that floor.

## Definition of Done

- U1 through U4 meet their verification lines.
- With storage stopped, `storage-init` exits 1 within 75 seconds and names the underlying error.
- The server client settings are unchanged.
- The final message does not print the endpoint value.
- The README matches R7 and R8 and does not add a slogan or the word trial.
- Abandoned experiment code is not left in the diff.
- The pull request is ready for review.

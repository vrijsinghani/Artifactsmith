---
title: Keep binary publish formats and name little CSS - Plan
type: fix
date: 2026-10-14
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Keep binary publish formats and name little CSS - Plan

## Goal

A new version of a pdf, docx, or xlsx artifact keeps that format when `format` is omitted and the page is markdown. The bare-HTML warning says `little CSS` when a style block is present.

## Requirements

- R1. Omitted format plus only markdown infers markdown for an html or markdown artifact, and for a new artifact.
- R2. The same call on a pdf, docx, or xlsx artifact keeps the current format.
- R3. An explicit `format` still wins.
- R4. A warning for HTML with a short style block does not say `has no CSS`.

## Decision

Inference runs only when the current format is html, markdown, or absent. The warning uses the same 24-character gate. Non-empty CSS under that gate is `little CSS`. Empty CSS is `no CSS`.

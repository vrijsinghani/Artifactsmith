"""Admit a finished page without calling the model.

The worker loads ``publish_json`` and runs :func:`artifactsmith.builder.publish_page`,
the same size, secret, private-link, sanitize, render, and final-sweep path as
model output.
"""

from __future__ import annotations

from typing import Any

from .publish_common import assumption_list, load_publish_payload, note_text, optional_text, summary_text
from .publish_new import publish_new
from .publish_version import publish_version
from .service import AMError, Service

__all__ = ["admit_publish", "load_publish_payload"]


def admit_publish(
    svc: Service,
    principal: dict[str, Any],
    *,
    html: str | None,
    markdown: str | None,
    slug: str | None,
    display_name: str | None,
    artifact_id: str | None,
    base_version: int | None,
    format: str | None,
    summary: str | None,
    assumptions: list[str] | None,
    workspace: str | None,
    idempotency_key: str | None,
    style: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    """Queue a publish job. Safety failures surface later as the job error."""
    svc.require(principal, "publish")
    html_text = optional_text(html, "html")
    md_text = optional_text(markdown, "markdown")
    summary_text_value = summary_text(summary)
    assumption_values = assumption_list(assumptions)
    note_value = note_text(note)
    idem = svc._validate_idempotency_key(idempotency_key)
    target = (artifact_id or "").strip()
    if target or base_version is not None:
        if not target or base_version is None:
            raise AMError("artifact_id and base_version are both required to publish a new version")
        return publish_version(
            svc,
            principal,
            artifact_id=target,
            base_version=int(base_version),
            html=html_text,
            markdown=md_text,
            summary=summary_text_value,
            assumptions=assumption_values,
            format=format,
            workspace=workspace,
            idempotency_key=idem,
            style=style,
            display_name=display_name,
            note=note_value,
        )
    return publish_new(
        svc,
        principal,
        html=html_text,
        markdown=md_text,
        summary=summary_text_value,
        assumptions=assumption_values,
        slug=slug,
        display_name=display_name,
        format=format,
        workspace=workspace,
        idempotency_key=idem,
        style=style,
        note=note_value,
    )

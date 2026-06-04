"""Redaction preview support for BaseScope."""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from maivn_shared import (
    REDACTION_PREVIEWED_ENRICHMENT_PHASE,
    PrivateData,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,
)
from pydantic import JsonValue

from maivn._internal.utils.reporting import get_current_reporter

# MARK: Types


class _RedactionClient(Protocol):
    def preview_redaction(
        self,
        *,
        payload: RedactionPreviewRequest,
    ) -> RedactionPreviewResponse: ...


# MARK: Redaction Preview


def preview_redaction(
    scope: object,
    message: object,
    *,
    known_pii_values: list[str | PrivateData] | None = None,
    private_data: dict[str, object] | None = None,
) -> RedactionPreviewResponse:
    """Execute a redaction preview against the server."""
    message_obj: object = message
    if not isinstance(message_obj, RedactedMessage):
        raise TypeError("preview_redaction requires a RedactedMessage")

    client = _resolve_client(scope)
    request = RedactionPreviewRequest(
        message=message_obj,
        private_data=_resolve_private_data(scope, private_data),
        known_pii_values=known_pii_values,
    )
    response = client.preview_redaction(payload=request)
    _emit_enrichment(scope, response)
    return response


# MARK: Client Resolution


def _resolve_client(scope: object) -> _RedactionClient:
    """Resolve a Client instance from the scope or its agents."""
    client = getattr(scope, "client", None)
    if client is not None:
        return cast(_RedactionClient, client)

    api_key = getattr(scope, "api_key", None)
    if isinstance(api_key, str) and api_key.strip():
        from ..client import Client

        resolved_client = Client(api_key=api_key)
        if hasattr(scope, "client"):
            setattr(scope, "client", resolved_client)  # noqa: B010 - Pydantic field.
        return cast(_RedactionClient, resolved_client)

    agents = getattr(scope, "agents", []) or []
    for agent in cast(Sequence[object], agents):
        client = getattr(agent, "client", None)
        if client is not None:
            return cast(_RedactionClient, client)
        api_key = getattr(agent, "api_key", None)
        if isinstance(api_key, str) and api_key.strip():
            from ..client import Client

            resolved_client = Client(api_key=api_key)
            if hasattr(agent, "client"):
                setattr(agent, "client", resolved_client)  # noqa: B010 - Pydantic field.
            return cast(_RedactionClient, resolved_client)

    raise ValueError(
        "preview_redaction requires a configured Client or api_key "
        + "on the Agent or Swarm entry agent"
    )


# MARK: Private Data Merging


def _resolve_private_data(
    scope: object,
    private_data: object,
) -> dict[str, JsonValue] | None:
    """Merge scope-level and call-level private data."""
    private_data_obj: object = private_data
    if private_data_obj is not None and not isinstance(private_data_obj, dict):
        raise TypeError("private_data must be a dictionary or None")

    merged: dict[str, JsonValue] = {}
    scope_private_data = getattr(scope, "private_data", None)
    if isinstance(scope_private_data, dict):
        merged.update(cast(Mapping[str, JsonValue], scope_private_data))
    if isinstance(private_data_obj, dict):
        merged.update(cast(Mapping[str, JsonValue], private_data_obj))
    return merged or None


# MARK: Enrichment Reporting


def _emit_enrichment(
    scope: object,
    response: RedactionPreviewResponse,
) -> None:
    """Report redaction preview results to the current reporter."""
    reporter = get_current_reporter()
    if reporter is None:
        return

    try:
        reporter.report_enrichment(
            phase=REDACTION_PREVIEWED_ENRICHMENT_PHASE,
            message="Redaction preview completed.",
            scope_id=str(getattr(scope, "id", "")),
            scope_name=cast(str | None, getattr(scope, "name", None)),
            scope_type="swarm" if hasattr(scope, "agents") else "agent",
            redaction={
                "inserted_keys": list(response.inserted_keys),
                "added_private_data": dict(response.added_private_data),
                "merged_private_data": dict(response.merged_private_data),
                "redacted_message_count": response.redacted_message_count,
                "redacted_value_count": response.redacted_value_count,
                "matched_known_pii_values": list(response.matched_known_pii_values),
                "unmatched_known_pii_values": list(response.unmatched_known_pii_values),
            },
        )
    except TypeError:
        reporter.report_enrichment(
            phase=REDACTION_PREVIEWED_ENRICHMENT_PHASE,
            message="Redaction preview completed.",
        )


__all__ = ["preview_redaction"]

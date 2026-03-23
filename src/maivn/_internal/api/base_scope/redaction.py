"""Redaction preview support for BaseScope."""

from __future__ import annotations

from typing import Any

from maivn_shared import (
    REDACTION_PREVIEWED_ENRICHMENT_PHASE,
    PrivateData,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,
)

from maivn._internal.utils.reporting import get_current_reporter

# MARK: Redaction Preview


def preview_redaction(
    scope: Any,
    message: RedactedMessage,
    *,
    known_pii_values: list[str | PrivateData] | None = None,
    private_data: dict[str, Any] | None = None,
) -> RedactionPreviewResponse:
    """Execute a redaction preview against the server."""
    if not isinstance(message, RedactedMessage):
        raise TypeError("preview_redaction requires a RedactedMessage")

    client = _resolve_client(scope)
    request = RedactionPreviewRequest(
        message=message,
        private_data=_resolve_private_data(scope, private_data),
        known_pii_values=known_pii_values,
    )
    response = client.preview_redaction(payload=request)
    _emit_enrichment(scope, response)
    return response


# MARK: Client Resolution


def _resolve_client(scope: Any) -> Any:
    """Resolve a Client instance from the scope or its agents."""
    client = getattr(scope, "client", None)
    if client is not None:
        return client

    api_key = getattr(scope, "api_key", None)
    if isinstance(api_key, str) and api_key.strip():
        from ..client import Client

        resolved_client = Client(api_key=api_key)
        if hasattr(scope, "client"):
            scope.client = resolved_client
        return resolved_client

    for agent in getattr(scope, "agents", []) or []:
        client = getattr(agent, "client", None)
        if client is not None:
            return client
        api_key = getattr(agent, "api_key", None)
        if isinstance(api_key, str) and api_key.strip():
            from ..client import Client

            resolved_client = Client(api_key=api_key)
            if hasattr(agent, "client"):
                agent.client = resolved_client
            return resolved_client

    raise ValueError(
        "preview_redaction requires a configured Client or api_key "
        "on the Agent or Swarm entry agent"
    )


# MARK: Private Data Merging


def _resolve_private_data(
    scope: Any,
    private_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge scope-level and call-level private data."""
    if private_data is not None and not isinstance(private_data, dict):
        raise TypeError("private_data must be a dictionary or None")

    merged: dict[str, Any] = {}
    if isinstance(scope.private_data, dict):
        merged.update(scope.private_data)
    if isinstance(private_data, dict):
        merged.update(private_data)
    return merged or None


# MARK: Enrichment Reporting


def _emit_enrichment(
    scope: Any,
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
            scope_id=scope.id,
            scope_name=scope.name,
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

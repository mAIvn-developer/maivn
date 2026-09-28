"""SDK exception hierarchy."""

from __future__ import annotations

from typing import Any, cast


class MaivnSDKError(Exception):
    """Base class for SDK-owned failures.

    Terminal invocation failures retain public identifiers so callers can query
    the failed session's audit and usage without replaying the invocation.
    Failures before session creation leave both identifiers unset.
    """

    session_id: str | None = None
    root_event_id: str | None = None
    error_code: str | None = None
    correlation: dict[str, str | int] | None = None


class ConfigurationError(MaivnSDKError):
    """Raised when SDK configuration is incomplete or invalid."""


class MissingCredentialError(ConfigurationError):
    """No configured source supplies a credential; distinct from an unreadable source."""


class AsyncContextError(MaivnSDKError):
    """Raised when a sync facade is called from an active event loop."""


class ResourceRegistrationError(MaivnSDKError):
    """Raised when a declared scope resource cannot be registered or bound."""


class DurableBoundaryRefusedError(MaivnSDKError):
    """Raised when the platform's durable privacy boundary refused the run.

    Terminal by nature: the run ended because a payload on its way to the durable log
    still carried a private value in some form. `refused_path` names WHERE - a
    projection name and a field path, value-free by construction at the boundary that
    produced it - so a caller can act on it without the value ever travelling.

    A subclass of `MaivnSDKError` on purpose: every existing handler keeps working, and
    a surface that wants to say something better than the raw code (Studio does) can
    classify by TYPE instead of matching on message text.
    """

    def __init__(self, detail: str, *, refused_path: str | None = None) -> None:
        """Create the boundary refusal, carrying the structural path if one was sent."""
        self.refused_path = refused_path
        super().__init__(detail)


class MaivnHTTPError(MaivnSDKError):
    """Raised for typed non-2xx mAIvn API responses.

    Every API error reply is one envelope: `detail` (the message, kept here as
    `reason`), `code`, and optionally `fields` (request fields that failed
    validation), `retry_after` (seconds) and `context` (code-specific data,
    kept here as `detail`).
    """

    def __init__(  # noqa: PLR0913 - one keyword per envelope member.
        self,
        *,
        status_code: int,
        reason: str,
        code: str | None = None,
        detail: Any | None = None,
        fields: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        """Create an HTTP error."""
        self.status_code = status_code
        self.code = code
        self.error_code = code
        self.reason = reason
        self.detail = detail
        self.fields = list(fields or [])
        self.retry_after = retry_after
        message = f'mAIvn API returned {status_code}: {reason}'
        diagnostics = [_field_diagnostic(field) for field in self.fields]
        diagnostics = [diagnostic for diagnostic in diagnostics if diagnostic]
        if diagnostics:
            message += ' - ' + '; '.join(diagnostics)
        super().__init__(message)


def _field_diagnostic(field: object) -> str:
    if not isinstance(field, dict):
        return ''
    entry = cast('dict[str, object]', field)
    text = entry.get('message')
    if not isinstance(text, str) or not text:
        return ''
    name = entry.get('field')
    return f'{name}: {text}' if isinstance(name, str) and name else text


__all__ = [
    'AsyncContextError',
    'ConfigurationError',
    'DurableBoundaryRefusedError',
    'MaivnHTTPError',
    'MaivnSDKError',
    'ResourceRegistrationError',
]

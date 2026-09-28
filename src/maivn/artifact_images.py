"""Caller-local image bytes for composing exact invocation attachments.

Never return these bytes as a model tool outcome. Artifact toolsets consume them
locally and return an opaque composition handle instead.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from maivn_contracts.artifacts import ArtifactRef


class ArtifactImageUnavailableError(ValueError):
    """A value-free failure to resolve an invocation-owned image."""

    def __init__(self) -> None:
        """Construct a value-free public composition failure."""
        super().__init__('The selected artifact image is unavailable for composition.')


@dataclass(frozen=True, slots=True)
class ResolvedArtifactImage:
    """Authorized local bytes; never a model-facing message or output contract."""

    content: bytes = field(repr=False)
    artifact_ref: ArtifactRef = field(repr=False)
    mime_type: str
    private: bool


image_resolver: ContextVar[Callable[[str], Awaitable[ResolvedArtifactImage]] | None] = ContextVar(
    'maivn_artifact_image_resolver',
    default=None,
)


async def resolve_artifact_image(artifact_id: str) -> ResolvedArtifactImage:
    """Download an exact image already selected by this invocation's trusted references."""
    resolver = image_resolver.get()
    if resolver is None:
        raise ArtifactImageUnavailableError
    return await resolver(artifact_id)


__all__ = ['ArtifactImageUnavailableError', 'ResolvedArtifactImage', 'resolve_artifact_image']

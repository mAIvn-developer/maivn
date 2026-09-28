"""Exact invocation ref registry for the local image composition bridge."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, NoReturn, cast

from maivn_contracts.artifacts import OrdinaryArtifactRef
from maivn_contracts.tools import ToolOutcomeVariant
from pydantic import TypeAdapter, ValidationError

from maivn.artifact_images import (
    ArtifactImageUnavailableError,
    ResolvedArtifactImage,
    image_resolver,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

    from maivn_contracts.artifacts import ArtifactRef

    from maivn._internal.artifacts import ArtifactsClient
    from maivn._internal.models import StreamEvent
    from maivn._internal.private_artifacts import PrivateArtifactsClient

_OUTCOME: TypeAdapter[ToolOutcomeVariant] = TypeAdapter(ToolOutcomeVariant)
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class InvocationArtifactImages:
    """Ref selectors are checked again by the appropriate authenticated download API."""

    def __init__(
        self, *, ordinary: ArtifactsClient, private: PrivateArtifactsClient | None, session_id: str
    ) -> None:
        self._ordinary = ordinary
        self._private = private
        self._session_id = session_id
        self._refs: dict[str, ArtifactRef] = {}
        self._conflicts: set[str] = set()

    @property
    def references(self) -> tuple[ArtifactRef, ...]:
        """Expose registered exact selectors without local bytes or download credentials."""
        return tuple(self._refs.values())

    def register(self, refs: Sequence[ArtifactRef]) -> None:
        """Accept typed caller attachments or receipts from completed authenticated intake."""
        for ref in refs:
            key = ref.artifact_id
            if key in self._conflicts:
                continue
            previous = self._refs.get(key)
            if previous is not None and previous != ref:
                self._refs.pop(key)
                self._conflicts.add(key)
            else:
                self._refs[key] = ref

    def observe(self, event: StreamEvent) -> None:
        """Read only the canonical completion outcome field of authenticated SSE."""
        if event.event_type not in {'system_tool_complete', 'system_tool_error'}:
            return
        raw = event.payload.get('outcome')
        if not isinstance(raw, dict):
            return
        raw = cast('Mapping[str, object]', raw)
        try:
            outcome = _OUTCOME.validate_python(
                {'result': None, **raw} if raw.get('status') == 'ok' else raw
            )
        except ValidationError:
            return
        self.register(outcome.artifact_refs or ())

    @contextmanager
    def activate(self) -> Generator[None]:
        """Bind resolution only inside this invocation's local execution task."""
        token = image_resolver.set(self.resolve)
        try:
            yield
        finally:
            image_resolver.reset(token)

    async def resolve(self, artifact_id: str) -> ResolvedArtifactImage:
        """Refuse invented IDs, invalid kinds, ambiguous refs and inaccessible exact bytes."""
        ref = self._refs.get(artifact_id)
        if (
            ref is None
            or ref.kind != 'image'
            or ref.mime_type not in {'image/png', 'image/jpeg'}
            or artifact_id in self._conflicts
        ):
            _unavailable()
        try:
            if isinstance(ref, OrdinaryArtifactRef):
                if ref.size_bytes > _MAX_IMAGE_BYTES:
                    _unavailable()
                raw = (await self._ordinary.adownload(ref)).content
            else:
                if self._private is None:
                    _unavailable()
                raw = await self._private.adownload(ref, session_id=self._session_id)
            if (
                not raw
                or len(raw) > _MAX_IMAGE_BYTES
                or self._refs.get(artifact_id) != ref
                or artifact_id in self._conflicts
            ):
                _unavailable()
            return ResolvedArtifactImage(
                content=raw,
                artifact_ref=ref,
                mime_type=ref.mime_type,
                private=not isinstance(ref, OrdinaryArtifactRef),
            )
        except Exception:  # noqa: BLE001 - prevent transport details crossing model outcomes.
            raise ArtifactImageUnavailableError from None


def _unavailable() -> NoReturn:
    raise ArtifactImageUnavailableError

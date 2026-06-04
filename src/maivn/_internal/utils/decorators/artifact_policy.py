# pyright: strict
"""Compose-artifact argument policy decorator."""

from __future__ import annotations

from collections.abc import Callable

from ._attach import attach_arg_policy
from ._introspection import validate_arg_target
from ._types import ArgPolicy, ComposeArtifactApproval, ComposeArtifactMode, DecoratedObjectT

# MARK: Public Decorators


def compose_artifact_policy(
    arg_name: str,
    *,
    mode: ComposeArtifactMode = "allow",
    approval: ComposeArtifactApproval = "none",
) -> Callable[[DecoratedObjectT], DecoratedObjectT]:
    """Declare compose_artifact usage policy for a specific tool argument."""

    def decorator(obj: DecoratedObjectT) -> DecoratedObjectT:
        validate_arg_target(
            obj,
            arg_name,
            decorator_name="compose_artifact_policy",
            walk_wrappers=False,
            include_model_fields=False,
        )
        policy = normalize_compose_artifact_policy(
            arg_name=arg_name,
            mode=mode,
            approval=approval,
        )
        attach_arg_policy(obj, policy)
        return obj

    return decorator


# MARK: Normalization


def normalize_compose_artifact_policy(
    *,
    arg_name: str,
    mode: ComposeArtifactMode,
    approval: ComposeArtifactApproval,
) -> ArgPolicy:
    if mode not in {"forbid", "allow", "require"}:
        raise ValueError(f"Unsupported compose_artifact mode: {mode}")
    if approval not in {"none", "explicit"}:
        raise ValueError(f"Unsupported compose_artifact approval: {approval}")
    return {
        "arg_name": arg_name,
        "policy": "compose_artifact",
        "mode": mode,
        "approval": approval,
    }


__all__ = [
    "compose_artifact_policy",
    "normalize_compose_artifact_policy",
]

"""Policy and execution-control helpers for ToolifyService.

Handles merging, normalization, and registration of:
- Execution controls (await_for, reevaluate)
- Argument policies (compose_artifact)
"""

from __future__ import annotations

from typing import Any, Literal, cast

from maivn_shared import BaseDependency
from maivn_shared.domain.entities.dependencies import (
    AwaitForDependency,
    ReevaluateDependency,
)

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.utils.dependency_utils import normalize_dependencies

ComposeArtifactMode = Literal["forbid", "allow", "require"]
ComposeArtifactApproval = Literal["none", "explicit"]


# MARK: Dependency Helpers


def get_dependency_signature(dependency: BaseDependency) -> str:
    """Return a stable string signature for a dependency."""
    normalized = normalize_dependencies([dependency])
    if normalized:
        return normalized[0]
    return str(dependency)


def has_matching_dependency(items: list[Any], dep_signature: str) -> bool:
    """Check if a matching dependency exists in the list."""
    for item in items:
        if isinstance(item, BaseDependency):
            if get_dependency_signature(item) == dep_signature:
                return True
    return False


def add_dependency_to_target(
    target: Any,
    attr: str,
    dependency: BaseDependency,
    dep_signature: str,
) -> None:
    """Add dependency to target if not already present."""
    items = list(getattr(target, attr, []) or [])
    if not has_matching_dependency(items, dep_signature):
        items.append(dependency)
        setattr(target, attr, items)


# MARK: Execution Control Helpers


def normalize_execution_control(
    control: AwaitForDependency | ReevaluateDependency,
) -> dict[str, Any]:
    """Normalize an execution control to a comparable dict."""
    payload = control.model_dump(mode="json")
    payload.pop("arg_name", None)
    payload.pop("name", None)
    return payload


def has_matching_control(
    items: list[Any],
    control: AwaitForDependency | ReevaluateDependency,
) -> bool:
    """Check if a matching execution control exists in the list."""
    normalized = normalize_execution_control(control)
    for item in items:
        if isinstance(item, AwaitForDependency | ReevaluateDependency):
            if normalize_execution_control(item) == normalized:
                return True
    return False


def add_execution_control_to_target(
    target: Any,
    control: AwaitForDependency | ReevaluateDependency,
) -> None:
    """Register an execution control on a target object."""
    existing_controls = list(getattr(target, "__maivn_execution_controls__", []) or [])
    if not has_matching_control(existing_controls, control):
        existing_controls.append(control)
        target.__maivn_execution_controls__ = existing_controls

    metadata = dict(getattr(target, "metadata", {}) or {})
    normalized = normalize_execution_control(control)
    execution_controls = metadata.setdefault("execution_controls", {})
    if not isinstance(execution_controls, dict):
        execution_controls = {}
        metadata["execution_controls"] = execution_controls
    key = control.dependency_type
    current_items = execution_controls.get(key, [])
    merged = list(current_items) if isinstance(current_items, list) else []
    if normalized not in merged:
        merged.append(normalized)
    execution_controls[key] = merged
    target.metadata = metadata


def collect_execution_controls(obj: Any) -> dict[str, list[dict[str, Any]]]:
    """Collect execution controls from an object's metadata attributes."""
    controls = list(getattr(obj, "__maivn_execution_controls__", []))
    controls.extend(getattr(obj, "__maivn_pending_execution_controls__", []))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for control in controls:
        if not isinstance(control, AwaitForDependency | ReevaluateDependency):
            continue
        grouped.setdefault(control.dependency_type, [])
        normalized = normalize_execution_control(control)
        if normalized not in grouped[control.dependency_type]:
            grouped[control.dependency_type].append(normalized)

    return grouped


# MARK: Arg Policy Helpers


def normalize_arg_policy(policy: Any) -> dict[str, str] | None:
    """Normalize an arg policy dict, returning None if invalid."""
    if not isinstance(policy, dict):
        return None

    arg_name = policy.get("arg_name")
    if not isinstance(arg_name, str) or not arg_name.strip():
        return None

    mode = policy.get("mode", "allow")
    approval = policy.get("approval", "none")
    policy_key = policy.get("policy", "compose_artifact")
    if not isinstance(mode, str) or mode not in {"forbid", "allow", "require"}:
        return None
    if not isinstance(approval, str) or approval not in {"none", "explicit"}:
        return None
    if not isinstance(policy_key, str) or not policy_key.strip():
        return None

    return {
        "arg_name": arg_name.strip(),
        "policy": policy_key.strip(),
        "mode": cast(ComposeArtifactMode, mode),
        "approval": cast(ComposeArtifactApproval, approval),
    }


def merge_arg_policies(source: Any) -> dict[str, dict[str, dict[str, str]]]:
    """Merge arg policies from a list or dict source."""
    merged: dict[str, dict[str, dict[str, str]]] = {}

    if isinstance(source, list):
        for item in source:
            normalized = normalize_arg_policy(item)
            if normalized is None:
                continue
            arg_name = normalized["arg_name"]
            policy_key = normalized["policy"]
            merged.setdefault(arg_name, {})[policy_key] = {
                "mode": normalized["mode"],
                "approval": normalized["approval"],
            }
        return merged

    if isinstance(source, dict):
        for arg_name, value in source.items():
            if not isinstance(arg_name, str) or not isinstance(value, dict):
                continue
            normalized_policies: dict[str, dict[str, str]] = {}
            for policy_key, raw_policy in value.items():
                normalized = normalize_arg_policy(
                    {
                        "arg_name": arg_name,
                        "policy": policy_key,
                        **(raw_policy if isinstance(raw_policy, dict) else {}),
                    }
                )
                if normalized is None:
                    continue
                normalized_policies[normalized["policy"]] = {
                    "mode": normalized["mode"],
                    "approval": normalized["approval"],
                }
            if normalized_policies:
                merged[arg_name] = normalized_policies

    return merged


def collect_arg_policies(obj: Any) -> dict[str, dict[str, dict[str, str]]]:
    """Collect arg policies from an object's metadata attributes."""
    policies = list(getattr(obj, "__maivn_arg_policies__", []))
    policies.extend(getattr(obj, "__maivn_pending_arg_policies__", []))
    return merge_arg_policies(policies)


def add_arg_policy_to_target(target: Any, policy: dict[str, Any]) -> None:
    """Register an arg policy on a target object."""
    normalized = normalize_arg_policy(policy)
    if normalized is None:
        return

    existing_policies = list(getattr(target, "__maivn_arg_policies__", []) or [])
    if normalized not in existing_policies:
        existing_policies.append(normalized)
        target.__maivn_arg_policies__ = existing_policies

    metadata = dict(getattr(target, "metadata", {}) or {})
    arg_policies = metadata.get("arg_policies")
    merged_arg_policies = merge_arg_policies(arg_policies) if isinstance(arg_policies, dict) else {}
    arg_name = normalized["arg_name"]
    policy_key = normalized["policy"]
    current_map = merged_arg_policies.setdefault(arg_name, {})
    current_map[policy_key] = {
        "mode": normalized["mode"],
        "approval": normalized["approval"],
    }
    metadata["arg_policies"] = merged_arg_policies
    target.metadata = metadata


# MARK: Dynamic Registration Helpers


def register_dependency_on_targets(
    dependency: BaseDependency,
    *,
    obj: Any,
    tool: BaseTool,
    tool_id: str,
    dependency_repo: Any,
) -> None:
    """Register a dependency dynamically after tool creation."""
    if dependency is None:
        return

    dep_signature = get_dependency_signature(dependency)

    add_dependency_to_target(obj, "_dependencies", dependency, dep_signature)
    add_dependency_to_target(tool, "dependencies", dependency, dep_signature)

    if not tool_id:
        return

    repo_items = list(dependency_repo.list_dependencies(tool_id))
    if not has_matching_dependency(repo_items, dep_signature):
        try:
            dependency_repo.add_dependency(tool_id, dependency)
        except Exception:
            pass


def register_execution_control_on_targets(
    control: AwaitForDependency | ReevaluateDependency,
    *,
    obj: Any,
    tool: BaseTool,
) -> None:
    """Register an execution control on both obj and tool."""
    if control is None:
        return

    add_execution_control_to_target(obj, control)
    add_execution_control_to_target(tool, control)


def register_arg_policy_on_targets(
    policy: dict[str, Any],
    *,
    obj: Any,
    tool: BaseTool,
) -> None:
    """Register an arg policy on both obj and tool."""
    if not isinstance(policy, dict):
        return

    add_arg_policy_to_target(obj, policy)
    add_arg_policy_to_target(tool, policy)


__all__ = [
    "ComposeArtifactApproval",
    "ComposeArtifactMode",
    "add_arg_policy_to_target",
    "add_dependency_to_target",
    "add_execution_control_to_target",
    "collect_arg_policies",
    "collect_execution_controls",
    "get_dependency_signature",
    "has_matching_control",
    "has_matching_dependency",
    "merge_arg_policies",
    "normalize_arg_policy",
    "normalize_execution_control",
    "register_arg_policy_on_targets",
    "register_dependency_on_targets",
    "register_execution_control_on_targets",
]

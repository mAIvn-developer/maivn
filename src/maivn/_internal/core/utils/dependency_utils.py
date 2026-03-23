"""Utility functions for dependency normalization.

This module provides shared dependency normalization logic to eliminate
DRY violations across the codebase.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import BaseDependency, InterruptDependency, dumps


def normalize_dependencies(dependencies: list[BaseDependency] | list[Any] | None) -> list[str]:
    """Normalize dependencies to string format.

    This function handles various dependency types and converts them to
    string representations suitable for serialization and storage.

    Args:
        dependencies: List of dependency objects (BaseDependency or any type)

    Returns:
        List of dependency identifiers as strings

    Examples:
        >>> deps = [ToolDependency(tool_id="tool-123")]
        >>> normalize_dependencies(deps)
        ['tool-123']

        >>> deps = [InterruptDependency(arg_name="user_input", prompt="Enter value")]
        >>> result = normalize_dependencies(deps)
        >>> 'user_input' in result[0]
        True
    """
    if not dependencies:
        return []

    normalized: list[str] = []
    for dep in dependencies:
        normalized_dep = _normalize_single_dependency(dep)
        if normalized_dep is not None:
            normalized.append(normalized_dep)

    return normalized


# MARK: - Single Dependency Normalization


def _normalize_single_dependency(dep: Any) -> str | None:
    """Normalize a single dependency to string format.

    Args:
        dep: A dependency object of any type

    Returns:
        String representation of the dependency, or None if dep is None
    """
    if dep is None:
        return None

    # MARK: - BaseDependency Handling
    if isinstance(dep, BaseDependency):
        return _normalize_base_dependency(dep)

    # MARK: - Pydantic Model Handling
    if hasattr(dep, "model_dump"):
        return _normalize_pydantic_model(dep)

    # MARK: - Attribute-based Identification
    identifier = _extract_identifier_from_attributes(dep)
    if identifier is not None:
        return identifier

    # MARK: - Fallback
    return str(dep)


def _normalize_base_dependency(dep: BaseDependency) -> str:
    """Normalize a BaseDependency instance.

    Args:
        dep: A BaseDependency instance

    Returns:
        String representation of the dependency
    """
    # Try to extract a simple identifier first
    identifier = _extract_identifier_from_attributes(dep)
    if identifier is not None:
        return identifier

    # Handle InterruptDependency specially to avoid serializing function
    if isinstance(dep, InterruptDependency):
        return _normalize_interrupt_dependency(dep)

    # Fallback: serialize the full dependency
    return dumps(dep.model_dump(mode="json"))


def _normalize_interrupt_dependency(dep: InterruptDependency) -> str:
    """Normalize an InterruptDependency without serializing the function.

    Args:
        dep: An InterruptDependency instance

    Returns:
        JSON string representation without the input_handler function
    """
    user_dep_info = {
        "dependency_type": "user",
        "arg_name": dep.arg_name,
        "prompt": dep.prompt,
    }
    return dumps(user_dep_info)


def _normalize_pydantic_model(dep: Any) -> str:
    """Normalize a Pydantic model to string format.

    Args:
        dep: An object with model_dump method

    Returns:
        String representation of the model
    """
    try:
        return dumps(dep.model_dump(mode="json"))
    except Exception:
        return str(dep)


def _extract_identifier_from_attributes(dep: Any) -> str | None:
    """Extract identifier from common dependency attributes.

    Args:
        dep: An object that may have identifier attributes

    Returns:
        String identifier if found, None otherwise
    """
    identifier = (
        getattr(dep, "tool_id", None)
        or getattr(dep, "agent_id", None)
        or getattr(dep, "data_key", None)
        or getattr(dep, "name", None)
    )
    return str(identifier) if identifier else None


__all__ = [
    "normalize_dependencies",
]

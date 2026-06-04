# pyright: strict
"""Utility functions for dependency normalization.

This module provides shared dependency normalization logic to eliminate
DRY violations across the codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, Protocol, TypeGuard, cast

from maivn_shared import BaseDependency, InterruptDependency, dumps
from pydantic import JsonValue

# MARK: Configuration

NAME_IDENTIFIER_ATTRIBUTE: Final = "name"
DEPENDENCY_IDENTIFIER_ATTRIBUTES: Final[tuple[str, ...]] = (
    "tool_id",
    "agent_id",
    "data_key",
    NAME_IDENTIFIER_ATTRIBUTE,
)
DEPENDENCY_TYPE_KEY: Final = "dependency_type"
USER_DEPENDENCY_TYPE: Final = "user"

# MARK: - Protocols


class _ModelDumpable(Protocol):
    """Object with a Pydantic-style model_dump method."""

    def model_dump(self, *, mode: Literal["json"]) -> object:
        """Return a JSON-mode representation."""
        ...


# MARK: - Public API


def normalize_dependencies(dependencies: Sequence[object] | None) -> list[str]:
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


def _normalize_single_dependency(dep: object) -> str | None:
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
    if _has_model_dump(dep):
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
    user_dep_info: dict[str, JsonValue] = {
        DEPENDENCY_TYPE_KEY: USER_DEPENDENCY_TYPE,
        "arg_name": dep.arg_name,
        "prompt": dep.prompt,
    }
    return dumps(user_dep_info)


def _normalize_pydantic_model(dep: _ModelDumpable) -> str:
    """Normalize a Pydantic model to string format.

    Args:
        dep: An object with model_dump method

    Returns:
        String representation of the model
    """
    try:
        return dumps(dep.model_dump(mode="json"))
    except Exception:  # noqa: BLE001 - legacy fallback stringifies unserializable models.
        return str(dep)


def _extract_identifier_from_attributes(dep: object) -> str | None:
    """Extract identifier from common dependency attributes.

    Args:
        dep: An object that may have identifier attributes

    Returns:
        String identifier if found, None otherwise
    """
    for attribute in DEPENDENCY_IDENTIFIER_ATTRIBUTES:
        identifier = _get_optional_attribute(dep, attribute)
        if _is_present_identifier(attribute, identifier):
            return str(identifier)
    return None


def _has_model_dump(dep: object) -> TypeGuard[_ModelDumpable]:
    """Return whether the object exposes a callable model_dump method."""
    return callable(getattr(dep, "model_dump", None))


def _get_optional_attribute(dep: object, name: str) -> object | None:
    """Return an optional dynamic attribute value."""
    return cast(object | None, getattr(dep, name, None))


def _is_present_identifier(attribute: str, identifier: object | None) -> bool:
    """Return whether a dynamic identifier attribute should be treated as present."""
    if identifier is None:
        return False
    return attribute != NAME_IDENTIFIER_ATTRIBUTE or identifier != ""


__all__ = [
    "normalize_dependencies",
]

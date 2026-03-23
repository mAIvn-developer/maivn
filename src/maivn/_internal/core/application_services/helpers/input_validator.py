"""Input validation and sanitization utilities.

This module provides security-focused validation to prevent injection attacks
and ensure data integrity.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar


class InputValidator:
    """Validates and sanitizes user inputs for security."""

    # MARK: - Configuration

    enforce_security_checks: ClassVar[bool] = True

    # MARK: - Security Patterns

    # More specific SQL injection patterns to avoid false positives on natural language.
    # Targets actual injection attempts rather than just SQL keywords in prose.
    SQL_INJECTION_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"("
        r"'\s*(OR|AND)\s+['\d]|"  # ' OR '1 / ' AND 1
        # DDL injection
        r";\s*(DROP|DELETE|INSERT|UPDATE|CREATE|ALTER|TRUNCATE)\s+"  # DDL injection
        r"(TABLE|DATABASE|INDEX|VIEW)\b|"  # DDL injection
        r"UNION\s+(ALL\s+)?SELECT\s|"  # UNION SELECT injection
        r"--\s*$|"  # SQL comment at end of string
        r"\/\*.*?\*\/|"  # Block comments /* */
        r"\bxp_\w+|"  # Extended stored procedures
        r"\bsp_\w+|"  # System stored procedures
        r"EXEC\s*\(|EXECUTE\s*\("  # EXEC() calls
        r")",
        re.IGNORECASE,
    )

    SCRIPT_INJECTION_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"<script|javascript:|onerror=|onclick=|onload=",
        re.IGNORECASE,
    )

    # MARK: - Limits

    MAX_STRING_LENGTH: ClassVar[int] = 10000
    MAX_DICT_KEYS: ClassVar[int] = 100
    MAX_LIST_ITEMS: ClassVar[int] = 1000
    MAX_NESTING_DEPTH: ClassVar[int] = 10

    # MARK: - Public API

    @classmethod
    def validate_tool_arguments(cls, args: dict[str, Any]) -> dict[str, Any]:
        """Validate tool arguments for safety.

        Args:
            args: Tool arguments to validate

        Returns:
            Validated arguments

        Raises:
            ValueError: If validation fails
        """
        return cls.validate_dict(args, "tool_arguments")

    @classmethod
    def set_security_checks(cls, enabled: bool) -> None:
        """Enable or disable strict security patterns."""
        cls.enforce_security_checks = enabled

    # MARK: - Type Validators

    @classmethod
    def validate_string(cls, value: str, field_name: str = "value") -> str:
        """Validate and sanitize a string input.

        Args:
            value: String to validate
            field_name: Name of field being validated

        Returns:
            Validated string

        Raises:
            ValueError: If validation fails
        """
        cls._check_type(value, str, field_name)
        cls._check_string_length(value, field_name)

        if cls.enforce_security_checks:
            cls._check_security_patterns(value, field_name)

        return value

    @classmethod
    def validate_dict(
        cls,
        value: dict[str, Any],
        field_name: str = "dict",
        depth: int = 0,
    ) -> dict[str, Any]:
        """Validate a dictionary recursively.

        Args:
            value: Dictionary to validate
            field_name: Name of field being validated
            depth: Current nesting depth

        Returns:
            Validated dictionary

        Raises:
            ValueError: If validation fails
        """
        cls._check_type(value, dict, field_name)
        cls._check_nesting_depth(depth, field_name)
        cls._check_dict_size(value, field_name)

        return cls._validate_dict_contents(value, field_name, depth)

    @classmethod
    def validate_list(
        cls,
        value: list[Any],
        field_name: str = "list",
        depth: int = 0,
    ) -> list[Any]:
        """Validate a list recursively.

        Args:
            value: List to validate
            field_name: Name of field being validated
            depth: Current nesting depth

        Returns:
            Validated list

        Raises:
            ValueError: If validation fails
        """
        cls._check_type(value, list, field_name)
        cls._check_nesting_depth(depth, field_name)
        cls._check_list_size(value, field_name)

        return cls._validate_list_contents(value, field_name, depth)

    # MARK: - Validation Checks

    @classmethod
    def _check_type(cls, value: Any, expected_type: type, field_name: str) -> None:
        """Check that value is of expected type."""
        if not isinstance(value, expected_type):
            raise ValueError(
                f"{field_name} must be a {expected_type.__name__}, got {type(value).__name__}"
            )

    @classmethod
    def _check_string_length(cls, value: str, field_name: str) -> None:
        """Check string length constraint."""
        if len(value) > cls.MAX_STRING_LENGTH:
            raise ValueError(
                f"{field_name} exceeds maximum length of {cls.MAX_STRING_LENGTH} characters"
            )

    @classmethod
    def _check_nesting_depth(cls, depth: int, field_name: str) -> None:
        """Check nesting depth constraint."""
        if depth > cls.MAX_NESTING_DEPTH:
            raise ValueError(
                f"{field_name} exceeds maximum nesting depth of {cls.MAX_NESTING_DEPTH}"
            )

    @classmethod
    def _check_dict_size(cls, value: dict[str, Any], field_name: str) -> None:
        """Check dictionary size constraint."""
        if len(value) > cls.MAX_DICT_KEYS:
            raise ValueError(f"{field_name} exceeds maximum of {cls.MAX_DICT_KEYS} keys")

    @classmethod
    def _check_list_size(cls, value: list[Any], field_name: str) -> None:
        """Check list size constraint."""
        if len(value) > cls.MAX_LIST_ITEMS:
            raise ValueError(f"{field_name} exceeds maximum of {cls.MAX_LIST_ITEMS} items")

    @classmethod
    def _check_security_patterns(cls, value: str, field_name: str) -> None:
        """Check for dangerous patterns in string."""
        if cls.SQL_INJECTION_PATTERN.search(value):
            raise ValueError(f"{field_name} contains potentially dangerous SQL patterns")

        if cls.SCRIPT_INJECTION_PATTERN.search(value):
            raise ValueError(f"{field_name} contains potentially dangerous script patterns")

    # MARK: - Content Validators

    @classmethod
    def _validate_dict_contents(
        cls,
        value: dict[str, Any],
        field_name: str,
        depth: int,
    ) -> dict[str, Any]:
        """Validate dictionary keys and values recursively."""
        validated: dict[str, Any] = {}

        for key, val in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Dictionary key must be string, got {type(key).__name__}")

            validated_key = cls.validate_string(key, f"{field_name}.{key}")
            validated[validated_key] = cls._validate_value(val, f"{field_name}.{key}", depth)

        return validated

    @classmethod
    def _validate_list_contents(
        cls,
        value: list[Any],
        field_name: str,
        depth: int,
    ) -> list[Any]:
        """Validate list items recursively."""
        return [
            cls._validate_value(item, f"{field_name}[{i}]", depth) for i, item in enumerate(value)
        ]

    @classmethod
    def _validate_value(cls, value: Any, field_name: str, depth: int) -> Any:
        """Validate a value based on its type."""
        if isinstance(value, str):
            return cls.validate_string(value, field_name)
        if isinstance(value, dict):
            return cls.validate_dict(value, field_name, depth + 1)
        if isinstance(value, list):
            return cls.validate_list(value, field_name, depth + 1)
        return value


__all__ = ["InputValidator"]

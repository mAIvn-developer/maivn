"""Normalization helpers for BaseScope fields."""

from __future__ import annotations

from typing import Any

from maivn_shared import PrivateData

# MARK: Private Data


def private_data_list_to_dict(items: list[Any]) -> dict[str, Any]:
    """Convert a list of PrivateData objects to a key-value dict."""
    result: dict[str, Any] = {}
    counter = 0
    for item in items:
        if isinstance(item, PrivateData):
            private_data = item
        elif isinstance(item, dict) and "value" in item:
            private_data = PrivateData.model_validate(item)
        else:
            raise TypeError(
                'private_data list entries must be PrivateData objects or dicts with a "value" key'
            )
        key = private_data.name
        if not key:
            counter += 1
            key = f"_private_{counter}"
        elif key in result:
            raise ValueError(f'duplicate private_data name: "{key}"')
        result[key] = private_data.value
    return result


__all__ = ["private_data_list_to_dict"]

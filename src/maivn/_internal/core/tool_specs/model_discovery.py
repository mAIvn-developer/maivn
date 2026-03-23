"""Model class discovery for schema building.

Finds Pydantic model classes by name within modules and packages,
used to resolve $defs references during schema generation.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

from pydantic import BaseModel

from .type_utils import is_pydantic_model

# MARK: Model Discovery


def find_model_class(class_name: str, module_name: str) -> type[BaseModel] | None:
    """Find a Pydantic model class by name in a module and its imports.

    Searches the specified module directly, then its imported names,
    then sibling submodules within the same package.

    Args:
        class_name: Name of the model class to find.
        module_name: Fully qualified module name to search in.

    Returns:
        The model class if found, None otherwise.
    """
    try:
        module = importlib.import_module(module_name)
        return (
            _find_in_module(module, class_name)
            or _find_in_module_attrs(module, class_name)
            or _find_in_package(module_name, class_name)
        )
    except (ImportError, AttributeError, TypeError):
        return None


# MARK: - Search Strategies


def _find_in_module(module: Any, class_name: str) -> type[BaseModel] | None:
    """Look for the class directly in the module."""
    if hasattr(module, class_name):
        attr = getattr(module, class_name)
        if is_pydantic_model(attr):
            return attr
    return None


def _find_in_module_attrs(module: Any, class_name: str) -> type[BaseModel] | None:
    """Search through module's imported names."""
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if attr and is_pydantic_model(attr) and attr.__name__ == class_name:
            return attr
    return None


def _find_in_package(module_name: str, class_name: str) -> type[BaseModel] | None:
    """Search submodules of the same package."""
    if "." not in module_name:
        return None

    package_name = module_name.rsplit(".", 1)[0]
    for mod_name, mod in list(sys.modules.items()):
        if mod and mod_name.startswith(package_name) and hasattr(mod, class_name):
            attr = getattr(mod, class_name)
            if is_pydantic_model(attr):
                return attr
    return None


__all__ = ["find_model_class"]

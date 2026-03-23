from __future__ import annotations

import sys
import types
from unittest.mock import patch

from pydantic import BaseModel

from maivn._internal.core.tool_specs.model_discovery import find_model_class

# MARK: Test Models


class Alpha(BaseModel):
    value: int


class Beta(BaseModel):
    name: str


class NotAModel:
    """Plain class, not a Pydantic model."""

    pass


# MARK: Tests - find_model_class


def test_find_model_class_direct_attr() -> None:
    """find_model_class returns a model found directly on the module."""
    result = find_model_class("Alpha", __name__)
    assert result is Alpha


def test_find_model_class_not_found_returns_none() -> None:
    """find_model_class returns None when the class does not exist anywhere."""
    result = find_model_class("NonExistentModel", __name__)
    assert result is None


def test_find_model_class_import_error_returns_none() -> None:
    """find_model_class returns None when the module cannot be imported."""
    result = find_model_class("Alpha", "totally.fake.module.that.does.not.exist")
    assert result is None


def test_find_model_class_attribute_error_returns_none() -> None:
    """find_model_class catches AttributeError during discovery."""
    with patch("maivn._internal.core.tool_specs.model_discovery.importlib.import_module") as mock:
        mock.side_effect = AttributeError("boom")
        result = find_model_class("Alpha", __name__)
        assert result is None


def test_find_model_class_type_error_returns_none() -> None:
    """find_model_class catches TypeError during discovery."""
    with patch("maivn._internal.core.tool_specs.model_discovery.importlib.import_module") as mock:
        mock.side_effect = TypeError("boom")
        result = find_model_class("Alpha", __name__)
        assert result is None


# MARK: Tests - _find_in_module (via find_model_class)


def test_find_direct_attr_is_not_pydantic_model() -> None:
    """Returns None when the attr exists but is not a Pydantic model (line 52-53)."""
    result = find_model_class("NotAModel", __name__)
    assert result is None


# MARK: Tests - _find_in_module_attrs (via find_model_class)


def test_find_via_module_attrs() -> None:
    """Finds a model through dir() scan when not a direct attr match (lines 58-61)."""
    # Create a synthetic module that has Alpha under a different attribute name
    fake_module = types.ModuleType("fake_module_attrs")
    fake_module.renamed_alpha = Alpha  # type: ignore[attr-defined]
    sys.modules["fake_module_attrs"] = fake_module

    try:
        result = find_model_class("Alpha", "fake_module_attrs")
        assert result is Alpha
    finally:
        del sys.modules["fake_module_attrs"]


def test_find_in_module_attrs_no_match() -> None:
    """Returns None when dir() scan finds no matching model name (line 62)."""
    fake_module = types.ModuleType("fake_module_no_match")
    fake_module.some_thing = Beta  # type: ignore[attr-defined]
    sys.modules["fake_module_no_match"] = fake_module

    try:
        # Looking for 'Alpha' but only Beta is present
        result = find_model_class("Alpha", "fake_module_no_match")
        # Alpha is not in this module at all, and Beta.__name__ != 'Alpha'
        # _find_in_package won't find it either since 'fake_module_no_match' has no dot
        assert result is None
    finally:
        del sys.modules["fake_module_no_match"]


# MARK: Tests - _find_in_package (via find_model_class)


def test_find_in_package_no_dot_returns_none() -> None:
    """Returns None immediately for top-level module names (line 67-68)."""
    fake_module = types.ModuleType("toplevelmodule")
    sys.modules["toplevelmodule"] = fake_module

    try:
        result = find_model_class("Alpha", "toplevelmodule")
        assert result is None
    finally:
        del sys.modules["toplevelmodule"]


def test_find_in_package_searches_sibling_submodules() -> None:
    """Finds a model in a sibling submodule of the same package (lines 70-75)."""
    # Create a fake package structure: fake_pkg.sub_a and fake_pkg.sub_b
    fake_pkg = types.ModuleType("fake_pkg")
    fake_sub_a = types.ModuleType("fake_pkg.sub_a")
    fake_sub_b = types.ModuleType("fake_pkg.sub_b")

    # Put Alpha on sub_b only
    fake_sub_b.Alpha = Alpha  # type: ignore[attr-defined]

    sys.modules["fake_pkg"] = fake_pkg
    sys.modules["fake_pkg.sub_a"] = fake_sub_a
    sys.modules["fake_pkg.sub_b"] = fake_sub_b

    try:
        # Search from sub_a; Alpha is not there, but sub_b has it
        result = find_model_class("Alpha", "fake_pkg.sub_a")
        assert result is Alpha
    finally:
        del sys.modules["fake_pkg"]
        del sys.modules["fake_pkg.sub_a"]
        del sys.modules["fake_pkg.sub_b"]


def test_find_in_package_skips_non_model_attrs() -> None:
    """_find_in_package skips attributes that are not Pydantic models (line 74)."""
    fake_pkg = types.ModuleType("fake_pkg2")
    fake_sub = types.ModuleType("fake_pkg2.sub")
    fake_sub.Target = NotAModel  # type: ignore[attr-defined]

    sys.modules["fake_pkg2"] = fake_pkg
    sys.modules["fake_pkg2.sub"] = fake_sub

    try:
        result = find_model_class("Target", "fake_pkg2.sub")
        assert result is None
    finally:
        del sys.modules["fake_pkg2"]
        del sys.modules["fake_pkg2.sub"]


def test_find_in_package_returns_none_when_no_sibling_has_model() -> None:
    """Returns None when no sibling submodule has the requested model (line 76)."""
    fake_pkg = types.ModuleType("fake_pkg3")
    fake_sub = types.ModuleType("fake_pkg3.sub")

    sys.modules["fake_pkg3"] = fake_pkg
    sys.modules["fake_pkg3.sub"] = fake_sub

    try:
        result = find_model_class("CompletelyMissing", "fake_pkg3.sub")
        assert result is None
    finally:
        del sys.modules["fake_pkg3"]
        del sys.modules["fake_pkg3.sub"]

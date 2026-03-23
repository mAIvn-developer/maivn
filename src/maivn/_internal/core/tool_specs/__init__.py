"""Tool specification creation and schema generation.

This module provides the ToolSpecFactory for creating flattened ToolSpec objects
from function tools, model tools, and MCP tools. It handles nested Pydantic model
flattening and dependency detection.
"""

from __future__ import annotations

from .dependency_detector import DependencyDetector
from .dependency_extractor import extract_tool_dependencies, merge_metadata
from .factory import ToolSpecFactory
from .flattener import ToolFlattener
from .model_discovery import find_model_class
from .schema_builder import SchemaBuilder
from .schema_processors import SchemaTypeProcessor

__all__ = [
    "DependencyDetector",
    "SchemaBuilder",
    "SchemaTypeProcessor",
    "ToolFlattener",
    "ToolSpecFactory",
    "extract_tool_dependencies",
    "find_model_class",
    "merge_metadata",
]

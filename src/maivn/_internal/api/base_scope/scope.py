"""Base scope implementation for maivn SDK internals.
Defines shared tool registration, compilation, and dependency wiring for Agent/Swarm.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from maivn_shared import (
    MemoryConfig,
    PrivateData,
    SessionResponse,
    SystemMessage,
    create_uuid,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from maivn._internal.api.mcp import MCPServer
from maivn._internal.core.entities.tools import FunctionTool, McpTool, ModelTool
from maivn._internal.core.interfaces.repositories import (
    DependencyRepoInterface,
    ToolRepoInterface,
)
from maivn._internal.core.interfaces.resolvers import ScopeResolverInterface
from maivn._internal.core.registrars import ToolRegistrar
from maivn._internal.core.services.toolify import ToolifyService

from .mcp import McpRegistry
from .memory import BaseScopeMemoryMixin
from .runtime import BaseScopeInitializationMixin
from .tooling import BaseScopeToolingMixin

# MARK: Helpers


def _private_data_list_to_dict(items: list[Any]) -> dict[str, Any]:
    """Convert a list of PrivateData objects to a key-value dict.

    Each PrivateData must have a ``name`` when used in ``private_data``
    (unlike ``known_pii_values`` where names are optional and auto-generated).
    """
    result: dict[str, Any] = {}
    counter = 0
    for item in items:
        if isinstance(item, PrivateData):
            pd = item
        elif isinstance(item, dict) and "value" in item:
            pd = PrivateData.model_validate(item)
        else:
            raise TypeError(
                'private_data list entries must be PrivateData objects or dicts with a "value" key'
            )
        key = pd.name
        if not key:
            counter += 1
            key = f"_private_{counter}"
        elif key in result:
            raise ValueError(f'duplicate private_data name: "{key}"')
        result[key] = pd.value
    return result


def _resolve_max_concurrency(max_concurrency: int | None, input_count: int) -> int | None:
    if max_concurrency is not None and max_concurrency < 1:
        raise ValueError('max_concurrency must be greater than 0.')
    if input_count < 1:
        return 0
    if max_concurrency is None:
        return None
    return min(max_concurrency, input_count)


# MARK: Base Scope


class BaseScope(
    BaseScopeToolingMixin,
    BaseScopeInitializationMixin,
    BaseScopeMemoryMixin,
    BaseModel,
):
    """Base abstract scope for tool registration and dependency management.

    Responsibilities:
    - Manage tool registration via ToolRegistrar
    - Provide accessors for tools
    - Provide a minimal compile_tools implementation
    """

    # MARK: - Pydantic Config

    model_config = ConfigDict(
        validate_assignment=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    # MARK: - Fields

    name: str | None = Field(
        default=None,
        description="The name of the scope. Defaults to class name if not provided.",
    )
    description: str | None = Field(default=None, description="An optional description.")
    system_prompt: str | SystemMessage | None = Field(
        default=None,
        description="Optional system prompt. Converted to SystemMessage if str provided.",
    )
    private_data: dict[Any, Any] = Field(
        default_factory=dict,
        description=(
            "Private user-specific data. Accepts a key-value dict or a list of PrivateData "
            "objects (which are converted to a dict keyed by PrivateData.name "
            "or auto-generated key)."
        ),
    )
    allow_private_in_system_tools: bool = Field(
        default=False,
        description=(
            "If True, system tools may receive raw private_data values (use with caution)."
        ),
    )
    memory_config: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description=(
            "Typed memory defaults applied to every invocation for this scope "
            "(for example level, summarization, retrieval, and persistence behavior)."
        ),
    )
    skills: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Optional user-defined skill definitions for this scope. "
            "These are surfaced to retrieval as origin='user_defined'."
        ),
    )
    resources: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Optional resource bindings for this scope. "
            "Resource ingestion/extraction wiring consumes this list in later phases."
        ),
    )
    tags: list[str] = Field(default_factory=list, description="Tags for the scope.")

    before_execute: Callable[[dict[str, Any]], Any] | None = Field(default=None)
    after_execute: Callable[[dict[str, Any]], Any] | None = Field(default=None)

    hook_execution_mode: Literal["tool", "scope", "agent"] = Field(default="tool")

    # MARK: - Private Attributes

    _tool_repo: ToolRepoInterface = PrivateAttr()
    _dependency_repo: DependencyRepoInterface = PrivateAttr()
    _tool_registrar: ToolRegistrar = PrivateAttr()
    _resolver: ScopeResolverInterface = PrivateAttr()
    _toolify_service: ToolifyService = PrivateAttr()
    _system_message: SystemMessage | None = PrivateAttr(default=None)
    _compiled_tools_cache: list[FunctionTool | ModelTool | McpTool] | None = PrivateAttr(
        default=None
    )
    _tools_dirty: bool = PrivateAttr(default=True)
    _mcp_servers: dict[str, MCPServer] = PrivateAttr(default_factory=dict)
    _mcp_registry: McpRegistry = PrivateAttr()

    # MARK: - Initialization

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_memory_settings(cls, value: Any) -> Any:
        if isinstance(value, dict) and "memory_settings" in value:
            raise ValueError("memory_settings has been removed; use memory_config instead")
        return value

    # MARK: - Validators

    @field_validator("name")
    @classmethod
    def _ensure_name_is_valid_string(cls, v: str | None) -> str | None:
        if v is not None and (not isinstance(v, str) or not v):
            raise ValueError("Name must be a non-empty string.")
        return v

    @field_validator("private_data", mode="before")
    @classmethod
    def _normalize_private_data(cls, v: Any) -> dict[Any, Any]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            return _private_data_list_to_dict(v)
        raise TypeError("private_data must be a dictionary, list of PrivateData, or None")

    @field_validator("memory_config", mode="before")
    @classmethod
    def _normalize_memory_config(cls, v: Any) -> MemoryConfig:
        if v is None:
            return MemoryConfig()
        if isinstance(v, MemoryConfig):
            return v
        if not isinstance(v, dict):
            raise TypeError("memory_config must be a MemoryConfig, dictionary, or None")
        return MemoryConfig.model_validate(v)

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("skills must be a list of dictionaries or None")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(v):
            if not isinstance(item, dict):
                raise TypeError(f"skills[{index}] must be a dictionary")
            normalized.append(dict(item))
        return normalized

    @field_validator("resources", mode="before")
    @classmethod
    def _normalize_resources(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("resources must be a list of dictionaries or None")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(v):
            if not isinstance(item, dict):
                raise TypeError(f"resources[{index}] must be a dictionary")
            normalized.append(dict(item))
        return normalized

    # MARK: - Properties

    @property
    def id(self) -> str:
        name = self.name or self.__class__.__name__
        return create_uuid(f"{self.__class__.__name__}:{name}")

    # MARK: - Batch Invocation

    def batch(
        self,
        inputs: Iterable[Any],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: Any,
    ) -> list[SessionResponse]:
        """Invoke this scope for multiple inputs concurrently.

        Args:
            inputs: Iterable of first-argument values to pass to ``invoke``.
            max_concurrency: Maximum number of invoke calls to run at once.
            **invoke_kwargs: Keyword arguments shared by every invoke call.

        Returns:
            Responses in the same order as ``inputs``.
        """
        input_items = list(inputs)
        max_workers = _resolve_max_concurrency(max_concurrency, len(input_items))
        if max_workers == 0:
            return []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._invoke_batch_item, item, dict(invoke_kwargs))
                for item in input_items
            ]
            return [future.result() for future in futures]

    async def abatch(
        self,
        inputs: Iterable[Any],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: Any,
    ) -> list[SessionResponse]:
        """Asynchronously invoke this scope for multiple inputs concurrently.

        Args:
            inputs: Iterable of first-argument values to pass to ``invoke``.
            max_concurrency: Maximum number of invoke calls to run at once.
            **invoke_kwargs: Keyword arguments shared by every invoke call.

        Returns:
            Responses in the same order as ``inputs``.
        """
        input_items = list(inputs)
        max_workers = _resolve_max_concurrency(max_concurrency, len(input_items))
        if max_workers == 0:
            return []

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [
                loop.run_in_executor(
                    executor,
                    self._invoke_batch_item,
                    item,
                    dict(invoke_kwargs),
                )
                for item in input_items
            ]
            return list(await asyncio.gather(*tasks))

    def _invoke_batch_item(
        self,
        input_item: Any,
        invoke_kwargs: dict[str, Any],
    ) -> SessionResponse:
        invoke_fn = getattr(self, 'invoke', None)
        if invoke_fn is None:
            raise AttributeError('Scope does not support invoke().')
        return invoke_fn(input_item, **invoke_kwargs)


# MARK: Model Rebuild


def _rebuild_base_scope_model() -> None:
    BaseScope.model_rebuild()


_rebuild_base_scope_model()


__all__ = [
    "BaseScope",
]

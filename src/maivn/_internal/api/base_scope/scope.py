"""Base scope implementation for maivn SDK internals."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass

from maivn_shared import (
    MemoryConfig,
    SessionOrchestrationConfig,
    SystemMessage,
    SystemToolsConfig,
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

from .batch import BaseScopeBatchMixin
from .mcp import McpRegistry
from .memory import BaseScopeMemoryMixin
from .normalization import private_data_list_to_dict
from .runtime import BaseScopeInitializationMixin
from .scheduling import BaseScopeSchedulingMixin
from .tooling import BaseScopeToolingMixin

# MARK: Base Scope


class BaseScope(
    BaseScopeToolingMixin,
    BaseScopeInitializationMixin,
    BaseScopeMemoryMixin,
    BaseScopeSchedulingMixin,
    BaseScopeBatchMixin,
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
    system_tools_config: SystemToolsConfig = Field(
        default_factory=SystemToolsConfig,
        description=(
            "Typed system-tool defaults applied to every invocation for this scope "
            "(for example allowed system tools and compose_artifact approvals)."
        ),
    )
    orchestration_config: SessionOrchestrationConfig = Field(
        default_factory=SessionOrchestrationConfig,
        description=(
            "Typed orchestration defaults applied to every invocation for this scope "
            "(for example reevaluate loop and cycle limits)."
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
            return private_data_list_to_dict(v)
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

    @field_validator("system_tools_config", mode="before")
    @classmethod
    def _normalize_system_tools_config(cls, v: Any) -> SystemToolsConfig:
        if v is None:
            return SystemToolsConfig()
        if isinstance(v, SystemToolsConfig):
            return v
        if not isinstance(v, dict):
            raise TypeError("system_tools_config must be a SystemToolsConfig, dictionary, or None")
        return SystemToolsConfig.model_validate(v)

    @field_validator("orchestration_config", mode="before")
    @classmethod
    def _normalize_orchestration_config(cls, v: Any) -> SessionOrchestrationConfig:
        if v is None:
            return SessionOrchestrationConfig()
        if isinstance(v, SessionOrchestrationConfig):
            return v
        if not isinstance(v, dict):
            raise TypeError(
                "orchestration_config must be a SessionOrchestrationConfig, dictionary, or None"
            )
        return SessionOrchestrationConfig.model_validate(v)

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

    # MARK: - System Tool Configuration

    @staticmethod
    def coerce_system_tools_config(value: Any) -> SystemToolsConfig | None:
        """Coerce ``None`` / dict / ``SystemToolsConfig`` to a typed config or ``None``."""
        if value is None:
            return None
        if isinstance(value, SystemToolsConfig):
            return value
        if isinstance(value, dict):
            return SystemToolsConfig.model_validate(value)
        raise TypeError("system_tools_config must be a SystemToolsConfig, dictionary, or None")

    def resolve_system_tools_config(
        self,
        override: Any = None,
        *,
        allow_private_in_system_tools: bool | None = None,
    ) -> SystemToolsConfig | None:
        """Merge SDK defaults, scope-level config, and a per-call ``override``.

        Layering (last wins via :meth:`SystemToolsConfig.merge`): SDK defaults
        → scope ``system_tools_config`` → scope ``allow_private_in_system_tools``
        → caller ``override`` → caller ``allow_private_in_system_tools``.
        """
        configs: list[SystemToolsConfig | None] = [
            SystemToolsConfig(allow_private_data_placeholders=True),
            self.system_tools_config,
        ]
        if self.allow_private_in_system_tools:
            configs.append(SystemToolsConfig(allow_private_data=True))
        configs.append(self.coerce_system_tools_config(override))
        if allow_private_in_system_tools is not None:
            configs.append(SystemToolsConfig(allow_private_data=allow_private_in_system_tools))
        return SystemToolsConfig.merge(*configs)

    # MARK: - Orchestration Configuration

    @staticmethod
    def coerce_orchestration_config(value: Any) -> SessionOrchestrationConfig | None:
        """Coerce ``None`` / dict / ``SessionOrchestrationConfig`` to a typed config or ``None``."""
        if value is None:
            return None
        if isinstance(value, SessionOrchestrationConfig):
            return value
        if isinstance(value, dict):
            return SessionOrchestrationConfig.model_validate(value)
        raise TypeError(
            "orchestration_config must be a SessionOrchestrationConfig, dictionary, or None"
        )

    def resolve_orchestration_config(
        self,
        override: Any = None,
    ) -> SessionOrchestrationConfig | None:
        """Merge the scope's orchestration config with a per-call ``override``."""
        return SessionOrchestrationConfig.merge(
            self.orchestration_config,
            self.coerce_orchestration_config(override),
        )


# MARK: Model Rebuild


def _rebuild_base_scope_model() -> None:
    BaseScope.model_rebuild()


_rebuild_base_scope_model()


__all__ = [
    "BaseScope",
]

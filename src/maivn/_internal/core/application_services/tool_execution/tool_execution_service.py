"""Enhanced tool execution service with dependency handling and hooks.

Extends ``BasicToolExecutionService`` with input validation, dependency
resolution, strategy-based dispatch, and before/after execution hooks.
"""

# pyright: strict
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from maivn_shared import BaseDependency
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol
from pydantic import JsonValue
from typing_extensions import override

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.agent_execution_service import AgentRegistry
from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)
from maivn._internal.core.services.interrupt_service import InterruptService

from ..helpers import InputValidator, PydanticDeserializer
from .argument_utils import prune_arguments
from .basic_tool_execution_service import BasicToolExecutionService, ToolType
from .execution_strategy import StrategyRegistry, create_default_registry
from .helpers import DependencyResolver

if TYPE_CHECKING:
    from maivn._internal.utils.reporting.terminal_reporter import BaseReporter


# MARK: Types

JsonObject = dict[str, JsonValue]
HookPayload = dict[str, object]
ExecutionHook = Callable[[HookPayload], object]
HookEntry = tuple[ExecutionHook, object, str]

# MARK: Enhanced Execution


class ToolExecutionService(BasicToolExecutionService):
    """Tool execution service with dependency resolution and strategy-based dispatch.

    This service orchestrates the execution of tools by:
    - Validating input arguments
    - Resolving dependencies (agent, user, tool)
    - Dispatching execution via the strategy registry
    - Running before/after execution hooks
    - Emitting ``hook_fired`` events through the reporter so frontends can
      surface a header/footer marker on the affected tool card.
    """

    def __init__(
        self,
        *,
        logger: MetricsLoggerProtocol | None = None,
        dependency_execution_service: DependencyExecutionService | None = None,
        pydantic_deserializer: PydanticDeserializer | None = None,
        dependency_resolver: DependencyResolver | None = None,
        strategy_registry: StrategyRegistry | None = None,
        input_validator: type[InputValidator] | None = None,
        reporter_supplier: Callable[[], BaseReporter | None] | None = None,
    ) -> None:
        super().__init__(logger=logger)

        dependency_service = dependency_execution_service or DependencyExecutionService(
            logger=logger
        )
        deserializer = pydantic_deserializer or PydanticDeserializer(logger=logger)

        self._dependency_resolver: DependencyResolver = dependency_resolver or DependencyResolver(
            logger=logger,
            dependency_service=dependency_service,
        )
        self._strategy_registry: StrategyRegistry = strategy_registry or create_default_registry(
            logger=logger,
            deserializer=deserializer,
        )
        self._dependency_service: DependencyExecutionService = dependency_service
        self._input_validator: type[InputValidator] = input_validator or InputValidator
        self._get_reporter: Callable[[], BaseReporter | None] = reporter_supplier or (lambda: None)

    # MARK: - Execution

    @override
    def execute_tool_call(
        self,
        tool_id: str,
        args: JsonObject,
        context: ExecutionContext | None = None,
        *,
        tool_event_id: str | None = None,
    ) -> object:
        """Execute a tool call with dependency resolution and input validation.

        Args:
            tool_id: Tool identifier to execute.
            args: Arguments for tool execution.
            context: Execution context (scope, messages, etc.).
            tool_event_id: Optional per-invocation event id used to route
                ``hook_fired`` events to the right tool card. When omitted,
                hook events fall back to ``tool_id``.

        Returns:
            Tool execution result.

        Raises:
            ValueError: If input validation fails.
        """
        context = context or ExecutionContext()

        tool = self.resolve_tool(tool_id)
        validated_args = self._validate_arguments(tool_id, tool, args)

        self._logger.debug("[TOOL_EXEC] Tool type: %s", type(tool).__name__)

        defaulted_args = self._apply_default_args(tool, validated_args)
        resolved_args = self._resolve_dependencies(tool, defaulted_args, context)
        filtered_args = self._filter_arguments(tool_id, tool, resolved_args)

        self._run_execution_hooks(
            stage="before",
            tool_id=tool_id,
            tool=tool,
            args=filtered_args,
            context=context,
            result=None,
            error=None,
            tool_event_id=tool_event_id,
        )

        try:
            result = self._strategy_registry.execute(tool, filtered_args, context)
        except Exception as exc:  # noqa: BLE001 - tool-call failures run after hooks then propagate
            self._run_execution_hooks(
                stage="after",
                tool_id=tool_id,
                tool=tool,
                args=filtered_args,
                context=context,
                result=None,
                error=exc,
                tool_event_id=tool_event_id,
            )
            raise

        self._run_execution_hooks(
            stage="after",
            tool_id=tool_id,
            tool=tool,
            args=filtered_args,
            context=context,
            result=result,
            error=None,
            tool_event_id=tool_event_id,
        )
        return result

    # MARK: - Validation

    def _validate_arguments(
        self,
        tool_id: str,
        tool: ToolType,
        args: JsonObject,
    ) -> dict[str, object]:
        """Validate input arguments for security."""
        if tool.tool_type == "agent":
            self._logger.debug("[TOOL_EXEC] Skipping input validation for agent tool %s", tool_id)
            return dict(args)
        try:
            validated_args = self._input_validator.validate_tool_arguments(args)
            self._logger.debug("[TOOL_EXEC] Input validation passed for %s", tool_id)
            return dict(validated_args)
        except ValueError as e:
            self._logger.error("[TOOL_EXEC] Input validation failed for %s: %s", tool_id, e)
            raise

    # MARK: - Dependency Resolution

    @staticmethod
    def _apply_default_args(tool: ToolType, args: dict[str, object]) -> dict[str, object]:
        """Merge tool-level default args before dependency resolution.

        ``ToolOverride.default_args`` and MCP defaults both compile to the
        canonical ``metadata["default_args"]`` shape used by the server.
        Local execution applies the same rule so SDK and server runs do not
        diverge. Model-supplied args always win.
        """
        metadata = _object_dict_or_none(cast(object, getattr(tool, "metadata", None)))
        default_args = _object_dict_or_none(metadata.get("default_args")) if metadata else None
        if not isinstance(default_args, dict) or not default_args:
            mcp_defaults = cast(object, getattr(tool, "default_args", None))
            default_args = _object_dict_or_none(mcp_defaults)
        if not isinstance(default_args, dict) or not default_args:
            return args
        merged = dict(default_args)
        merged.update(args)
        return merged

    def _resolve_dependencies(
        self,
        tool: ToolType,
        args: dict[str, object],
        context: ExecutionContext,
    ) -> dict[str, object]:
        """Resolve tool dependencies if needed."""
        dependencies: list[BaseDependency] = tool.dependencies

        self._logger.debug("[TOOL_EXEC] Tool has dependencies: %s", bool(dependencies))
        if dependencies:
            self._logger.debug("[TOOL_EXEC] Dependency count: %d", len(dependencies))
            self._logger.debug("[TOOL_EXEC] Args keys: %s", list(args.keys()))

        if dependencies and self._dependency_resolver.needs_resolution(dependencies, args):
            self._logger.debug("[TOOL_EXEC] Dependencies need resolution - resolving...")
            resolved_args = self._dependency_resolver.resolve_all(
                tool=tool,
                args=args,
                dependencies=dependencies,
                context=context,
                executor=self,
            )
            self._logger.debug(
                "[TOOL_EXEC] Dependencies resolved, args now: %s",
                list(resolved_args.keys()),
            )
            return resolved_args

        self._logger.debug("[TOOL_EXEC] Dependencies already resolved or not needed")
        return args.copy()

    # MARK: - Argument Filtering

    def _filter_arguments(
        self,
        tool_id: str,
        tool: ToolType,
        args: dict[str, object],
    ) -> dict[str, object]:
        """Filter arguments to only those accepted by the tool."""
        filtered_args, dropped_keys = prune_arguments(tool, args, self._logger)
        if dropped_keys:
            self._logger.debug(
                "[TOOL_EXEC] Dropped unexpected args for %s: %s",
                tool_id,
                dropped_keys,
            )
        return filtered_args

    # MARK: - Execution Hooks

    def _run_execution_hooks(
        self,
        *,
        stage: str,
        tool_id: str,
        tool: ToolType,
        args: dict[str, object],
        context: ExecutionContext,
        result: object | None,
        error: Exception | None,
        tool_event_id: str | None = None,
    ) -> None:
        """Run before/after execution hooks from tool, scope, and swarm.

        Each hook firing emits a ``hook_fired`` event via the configured
        reporter so the UI can render a header (before) or footer (after)
        marker on the *card representing the hook's owner* — the swarm card
        for swarm-level hooks, the agent card for scope-level hooks, and the
        tool card for tool-level hooks. Routing per-firing keeps the UI free
        of duplicated pills when all three levels are wired.
        """
        hooks = self._collect_hooks(stage, tool, context)
        payload: HookPayload = {
            "stage": stage,
            "tool_id": tool_id,
            "tool": tool,
            "args": args,
            "context": context,
            "result": result,
            "error": error,
        }

        reporter = self._get_reporter()

        for hook, owner, source in hooks:
            hook_name = _resolve_hook_name(hook)
            started_at = time.monotonic()
            hook_status = "completed"
            error_message: str | None = None
            try:
                _ = hook(payload)
            except Exception as exc:  # noqa: BLE001 - hook failures must never abort execution
                hook_status = "failed"
                error_message = str(exc) or exc.__class__.__name__
                self._logger.exception("[TOOL_EXEC] Execution hook failed")
            finally:
                if reporter is not None:
                    target_type, target_id, target_name = _resolve_hook_target(
                        source=source,
                        owner=owner,
                        tool=tool,
                        tool_id=tool_id,
                        tool_event_id=tool_event_id,
                    )
                    self._emit_hook_fired(
                        reporter,
                        name=hook_name,
                        stage=stage,
                        status=hook_status,
                        source=source,
                        target_type=target_type,
                        target_id=target_id,
                        target_name=target_name,
                        error=error_message,
                        elapsed_ms=int((time.monotonic() - started_at) * 1000),
                    )

    def _emit_hook_fired(
        self,
        reporter: BaseReporter,
        *,
        name: str,
        stage: str,
        status: str,
        source: str,
        target_type: str,
        target_id: str,
        target_name: str,
        error: str | None,
        elapsed_ms: int,
    ) -> None:
        """Defensive forward to ``reporter.report_hook_fired``.

        ``source`` records *where* the hook was defined (``"tool"`` /
        ``"scope"`` / ``"swarm"``) so the UI can label the pill even when
        all three sources hook the same tool execution. ``target_type``
        records *which on-screen card* the firing should attach to.
        """
        try:
            reporter.report_hook_fired(
                name=name,
                stage=stage,
                status=status,
                source=source,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                error=error,
                elapsed_ms=elapsed_ms,
            )
        except TypeError:
            # Older reporters predate the ``source`` kwarg. Retry without it
            # so a partially-upgraded environment can still see hook events.
            try:
                reporter.report_hook_fired(
                    name=name,
                    stage=stage,
                    status=status,
                    target_type=target_type,
                    target_id=target_id,
                    target_name=target_name,
                    error=error,
                    elapsed_ms=elapsed_ms,
                )
            except Exception:  # noqa: BLE001 - emission must never disrupt tool execution
                self._logger.exception("[TOOL_EXEC] Reporter.report_hook_fired raised")
        except Exception:  # noqa: BLE001 - emission must never disrupt tool execution
            self._logger.exception("[TOOL_EXEC] Reporter.report_hook_fired raised")

    def _collect_hooks(
        self,
        stage: str,
        tool: ToolType,
        context: ExecutionContext,
    ) -> list[HookEntry]:
        """Collect hooks to run for ``stage`` based on hook-execution modes.

        Returns a list of ``(hook_callable, owner, source)`` triples where
        ``source`` is one of ``"swarm"`` / ``"scope"`` / ``"tool"`` —
        capturing which level defined the hook so the emission can route
        the firing to the matching on-screen card.

        Order: ``before`` runs outside-in (swarm, scope, tool); ``after`` runs
        inside-out (tool, scope, swarm). A level is skipped if its hook is
        ``None`` or its ``hook_execution_mode`` disables it for this tool type.
        """
        scope = context.scope
        swarm = self._get_swarm_from_scope(scope)
        tool_type = tool.tool_type

        scope_active = _string_attr(scope, "hook_execution_mode", "tool") == "tool"
        swarm_mode = _string_attr(swarm, "hook_execution_mode", "tool")
        swarm_active = swarm_mode == "tool" or (swarm_mode == "agent" and tool_type == "agent")

        # Outside-in: swarm wraps scope wraps tool.
        outside_in: list[tuple[object | None, bool, str]] = [
            (swarm, swarm_active, "swarm"),
            (scope, scope_active, "scope"),
            (tool, True, "tool"),
        ]
        ordered = outside_in if stage == "before" else list(reversed(outside_in))
        attr = "before_execute" if stage == "before" else "after_execute"

        hooks: list[HookEntry] = []
        for owner, active, source in ordered:
            if not active:
                continue
            hook = _hook_attr(owner, attr)
            if hook is not None:
                hooks.append((hook, owner, source))
        return hooks

    def _get_swarm_from_scope(self, scope: object | None) -> object | None:
        """Get swarm object from scope if available."""
        get_swarm = cast(object, getattr(scope, "get_swarm", None))
        if callable(get_swarm):
            return cast(Callable[[], object | None], get_swarm)()
        return None

    # MARK: - Service Configuration

    def set_agent_registry(self, registry: AgentRegistry) -> None:
        """Set agent registry for dependency resolution."""
        self._dependency_service.set_agent_registry(registry)

    def set_interrupt_service(self, service: InterruptService) -> None:
        """Set interrupt service for dependency resolution."""
        self._dependency_service.set_interrupt_service(service)


# MARK: - Module Helpers


def _resolve_hook_name(hook: object) -> str:
    """Best-effort display name for a hook callable."""
    name = cast(object, getattr(hook, "__name__", None))
    if isinstance(name, str) and name:
        return name
    return hook.__class__.__name__


def _resolve_hook_target(
    *,
    source: str,
    owner: object,
    tool: ToolType,
    tool_id: str,
    tool_event_id: str | None,
) -> tuple[str, str, str]:
    """Resolve ``(target_type, target_id, target_name)`` for a hook firing.

    Routes hook events to the on-screen card representing the hook's owner:

    - ``source == "tool"`` → ``target_type="tool"``, attached to the per-invocation
      tool card via ``tool_event_id`` (falling back to ``tool_id``).
    - ``source == "scope"`` → ``target_type="agent"`` so the firing surfaces on
      the agent scope card. ``target_id`` is the agent's stable ``id`` if
      available; ``target_name`` is the agent name.
    - ``source == "swarm"`` → ``target_type="swarm"`` so the firing surfaces on
      the swarm scope card.

    Frontends key scope-card firings by either ``target_id`` or ``target_name``
    (see Studio's ``resolveScopeHookFirings``), so populating both keeps the
    lookup robust to either side of the contract drifting.
    """
    if source == "tool":
        target_id = tool_event_id or tool_id
        target_name = tool.name or tool_id
        return "tool", target_id, target_name

    owner_name = _string_attr(owner, "name", owner.__class__.__name__)
    owner_id = _string_attr(owner, "id", owner_name)
    if source == "swarm":
        return "swarm", str(owner_id), str(owner_name)
    # source == "scope" → render on the agent card
    return "agent", str(owner_id), str(owner_name)


def _object_dict_or_none(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _string_attr(owner: object | None, attr: str, fallback: str) -> str:
    value = cast(object, getattr(owner, attr, fallback))
    return value if isinstance(value, str) and value else fallback


def _hook_attr(owner: object | None, attr: str) -> ExecutionHook | None:
    hook = cast(object, getattr(owner, attr, None))
    return cast(ExecutionHook, hook) if callable(hook) else None


__all__ = ["ToolExecutionService"]

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from maivn._internal.core import ToolEventPayload, ToolEventValue
from maivn._internal.core.application_services.execution import BackgroundExecutor
from maivn._internal.core.application_services.tool_execution import (
    ToolEventDispatcher,
    ToolExecutionService,
)
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter


class InlineExecutor:
    """Test-double executor that runs submitted callables immediately."""

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        fn(*args, **kwargs)


class StubReporter:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str, str | None, dict[str, Any] | None]] = []
        self.completes: list[tuple[str, int, Any]] = []
        self.errors: list[tuple[str, str]] = []
        self.progress_updates: list[tuple[str, str]] = []

    def report_tool_start(
        self,
        tool_id: str,
        event_id: str,
        tool_type: str,
        agent_name: str | None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        self.starts.append((tool_id, event_id, tool_type, agent_name, tool_args))

    def update_progress(self, task: str, message: str) -> None:
        self.progress_updates.append((task, message))

    def report_tool_complete(self, event_id: str, elapsed_ms: int, result: Any) -> None:
        self.completes.append((event_id, elapsed_ms, result))

    def report_tool_error(
        self,
        tool_id: str,
        message: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self.errors.append((tool_id, message))


class FakeToolExecutionService:
    """Enough of ToolExecutionService for dispatcher tests."""

    class _FakeTool:
        def __init__(
            self,
            *,
            tags: set[str] | None = None,
            cls_name: str = "FunctionTool",
            tool_type: str | None = None,
            target_agent_id: str | None = None,
            agent_id: str | None = None,
        ) -> None:
            self.tags = tags or set()
            self.tool_type = tool_type
            self.target_agent_id = target_agent_id
            self.agent_id = agent_id
            self.__class__.__name__ = cls_name

    def __init__(self) -> None:
        self._tools: dict[str, FakeToolExecutionService._FakeTool] = {}
        self.execute_calls: list[tuple[str, dict[str, Any], Any]] = []

    def add_tool(
        self,
        tool_id: str,
        *,
        tags: set[str] | None = None,
        cls_name: str = "FunctionTool",
        tool_type: str | None = None,
        target_agent_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._tools[tool_id] = self._FakeTool(
            tags=tags,
            cls_name=cls_name,
            tool_type=tool_type,
            target_agent_id=target_agent_id,
            agent_id=agent_id,
        )

    def resolve_tool(self, tool_id: str) -> FakeToolExecutionService._FakeTool:
        return self._tools[tool_id]

    def execute_tool_call(
        self,
        tool_id: str,
        args: dict[str, Any],
        context: Any = None,
    ) -> Any:
        self.execute_calls.append((tool_id, args, context))
        return {"raw": f"{tool_id}:{args}"}

    def to_jsonable(self, result: Any) -> Any:
        return {"serialized": result}


class FakeCoordinator:
    def __init__(self) -> None:
        self.tool_results: dict[str, Any] = {}
        self.single_tool_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.batch_calls: list[list[dict[str, Any]]] = []
        self.multi_calls: list[dict[str, Any]] = []

    def get_tool_results(self) -> dict[str, Any]:
        return self.tool_results

    def _store_result(self, tool_id: str, tool: Any | None, result: Any) -> None:
        self.tool_results[tool_id] = result
        if tool is None:
            return
        tool_type = getattr(tool, "tool_type", None)
        if tool_type != "agent":
            return
        agent_id = getattr(tool, "target_agent_id", None) or getattr(tool, "agent_id", None)
        if agent_id:
            self.tool_results[str(agent_id)] = result

    def execute_single_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> Any:
        ctx = context or {}
        self.single_tool_calls.append((tool_id, args, ctx))
        return {"raw": f"{tool_id}:{args}"}

    def to_jsonable(self, result: Any) -> Any:
        return {"serialized": result}

    def execute_tool_events(self, tool_events: dict[str, Any]) -> dict[str, Any]:
        self.multi_calls.append(tool_events)
        return {key: {"result": "ok"} for key in tool_events}

    def execute_tool_batch(
        self,
        batch: list[dict[str, Any]],
        on_tool_complete: Any = None,
    ) -> list[str]:
        self.batch_calls.append(batch)
        results = [f"result-{idx}" for idx, _ in enumerate(batch)]
        if on_tool_complete is not None:
            for idx, tc in enumerate(batch):
                on_tool_complete(idx, str(tc.get("tool_id", "")), results[idx])
        return results


def _build_dispatcher(
    *,
    coordinator: FakeCoordinator | None = None,
    tool_execution_service: FakeToolExecutionService | None = None,
    reporter: StubReporter | None = None,
    post_resume: list[tuple[str, dict[str, Any]]] | None = None,
    agent_count: int = 1,
) -> tuple[ToolEventDispatcher, FakeCoordinator, StubReporter, list[tuple[str, dict[str, Any]]]]:
    coordinator = coordinator or FakeCoordinator()
    service = tool_execution_service or FakeToolExecutionService()
    reporter = reporter or StubReporter()
    posted: list[tuple[str, dict[str, Any]]] = post_resume or []

    dispatcher = ToolEventDispatcher(
        coordinator=cast(Any, coordinator),
        tool_execution_service=cast(ToolExecutionService, service),
        background_executor=cast(BackgroundExecutor, InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: cast(BaseReporter, reporter),
        progress_task_supplier=lambda: "task-123",
        agent_count_supplier=lambda: agent_count,
        tool_agent_lookup=lambda _: "agent-alpha",
        logger=None,
    )
    return dispatcher, coordinator, reporter, posted


def test_submit_tool_call_executes_coordinator_and_reports() -> None:
    dispatcher, coordinator, reporter, posted = _build_dispatcher(agent_count=2)
    service = FakeToolExecutionService()
    service.add_tool("tool-123", tags={"agent_invocation"}, target_agent_id="agent-123")
    dispatcher = ToolEventDispatcher(
        coordinator=cast(Any, coordinator),
        tool_execution_service=cast(ToolExecutionService, service),
        background_executor=cast(BackgroundExecutor, InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: cast(BaseReporter, reporter),
        progress_task_supplier=lambda: "task-123",
        agent_count_supplier=lambda: 2,
        tool_agent_lookup=lambda tool_id: f"owner-{tool_id}",
        logger=None,
    )

    dispatcher.submit_tool_call(
        "event-1",
        {
            "tool_id": "tool-123",
            "args": {"x": 1},
            "private_data_injected": {"foo": "bar"},
            "interrupt_data_injected": {"prompt": "yes"},
        },
        "https://resume",
    )

    assert len(service.execute_calls) == 1
    call_tool_id, call_args, call_context = service.execute_calls[0]
    assert call_tool_id == "tool-123"
    assert call_args == {"x": 1}
    assert isinstance(call_context, ExecutionContext)
    assert call_context.tool_results == coordinator.get_tool_results()
    assert coordinator.tool_results["tool-123"]["raw"] == "tool-123:{'x': 1}"
    assert posted == [
        (
            "https://resume",
            {
                "tool_event_id": "event-1",
                "result": {"serialized": {"raw": "tool-123:{'x': 1}"}},
            },
        )
    ]
    assert reporter.starts == [
        (
            "tool-123",
            "event-1",
            "agent",
            "owner-tool-123",
            {
                "arg_keys": ["x"],
                "agent_id": "agent-123",
                "private_data_injected": ["foo"],
                "interrupt_data_injected": ["prompt"],
            },
        )
    ]
    assert reporter.progress_updates[-1] == ("task-123", "Executing tool-123...")
    assert reporter.completes and reporter.completes[0][0] == "event-1"
    completion_payload = reporter.completes[0][2]
    assert completion_payload["result"]["serialized"]["raw"] == "tool-123:{'x': 1}"
    assert completion_payload["private_data_injected"] == {"foo": "bar"}
    assert completion_payload["interrupt_data_injected"] == {"prompt": "yes"}


def test_submit_tool_call_reports_injected_keys_even_when_args_empty() -> None:
    dispatcher, coordinator, reporter, posted = _build_dispatcher()
    service = FakeToolExecutionService()
    service.add_tool("tool-123")
    dispatcher = ToolEventDispatcher(
        coordinator=cast(Any, coordinator),
        tool_execution_service=cast(ToolExecutionService, service),
        background_executor=cast(BackgroundExecutor, InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: cast(BaseReporter, reporter),
        progress_task_supplier=lambda: None,
        agent_count_supplier=lambda: 1,
        tool_agent_lookup=lambda _: None,
        logger=None,
    )

    dispatcher.submit_tool_call(
        "event-1",
        {
            "tool_id": "tool-123",
            "args": {},
            "private_data_injected": {"foo": "bar"},
            "interrupt_data_injected": ["prompt"],
        },
        "https://resume",
    )

    assert reporter.starts == [
        (
            "tool-123",
            "event-1",
            "func",
            None,
            {
                "arg_keys": [],
                "private_data_injected": ["foo"],
                "interrupt_data_injected": ["prompt"],
            },
        )
    ]


def test_submit_tool_call_reports_unexpected_injected_payload_types_as_lists() -> None:
    dispatcher, coordinator, reporter, posted = _build_dispatcher()
    service = FakeToolExecutionService()
    service.add_tool("tool-123")
    dispatcher = ToolEventDispatcher(
        coordinator=cast(Any, coordinator),
        tool_execution_service=cast(ToolExecutionService, service),
        background_executor=cast(BackgroundExecutor, InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: cast(BaseReporter, reporter),
        progress_task_supplier=lambda: None,
        agent_count_supplier=lambda: 1,
        tool_agent_lookup=lambda _: None,
        logger=None,
    )

    dispatcher.submit_tool_call(
        "event-1",
        {
            "tool_id": "tool-123",
            "args": {},
            "private_data_injected": "foo",
            "interrupt_data_injected": 123,
        },
        "https://resume",
    )

    tool_args = reporter.starts[0][4]
    assert tool_args == {
        "arg_keys": [],
        "private_data_injected": ["str"],
        "interrupt_data_injected": ["int"],
    }


def test_submit_tool_call_stores_agent_id_alias() -> None:
    coordinator = FakeCoordinator()
    service = FakeToolExecutionService()
    service.add_tool("tool-123", tool_type="agent", target_agent_id="agent-42")
    posted: list[tuple[str, dict[str, Any]]] = []
    dispatcher = ToolEventDispatcher(
        coordinator=cast(Any, coordinator),
        tool_execution_service=cast(ToolExecutionService, service),
        background_executor=cast(BackgroundExecutor, InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: None,
        progress_task_supplier=lambda: None,
        agent_count_supplier=lambda: 1,
        tool_agent_lookup=lambda _: None,
        logger=None,
    )

    dispatcher.submit_tool_call(
        "event-1",
        {
            "tool_id": "tool-123",
            "args": {},
        },
        "https://resume",
    )

    # Tool results are stored in coordinator by dispatcher after execution
    assert coordinator.tool_results["tool-123"]["raw"] == "tool-123:{}"
    assert coordinator.tool_results["agent-42"]["raw"] == "tool-123:{}"


def test_process_tool_batch_and_requests_route_results() -> None:
    dispatcher, coordinator, reporter, posted = _build_dispatcher()

    dispatcher.process_tool_batch(
        "batch-event",
        cast(ToolEventValue, {"tool_calls": [{"tool_id": "a"}, {"tool_id": "b"}]}),
        "https://resume-batch",
    )
    assert coordinator.batch_calls == [[{"tool_id": "a"}, {"tool_id": "b"}]]
    assert posted.pop() == (
        "https://resume-batch",
        {
            "tool_event_id": "batch-event",
            "result": {"results": ["result-0", "result-1"]},
        },
    )

    dispatcher.process_tool_requests(
        cast(dict[str, ToolEventPayload], {"evt": {"value": {"tool_call": {"tool_id": "x"}}}}),
        "https://resume-requests",
    )
    assert coordinator.multi_calls
    assert posted.pop() == (
        "https://resume-requests",
        {
            "tool_event_id": "evt",
            "result": {"result": "ok"},
        },
    )

    dispatcher.acknowledge_barrier("barrier-1", "https://resume-barrier")
    assert posted.pop() == (
        "https://resume-barrier",
        {
            "tool_event_id": "barrier-1",
            "result": "ok",
        },
    )

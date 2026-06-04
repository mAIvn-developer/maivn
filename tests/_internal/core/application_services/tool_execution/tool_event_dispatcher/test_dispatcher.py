# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import cast

from pydantic import JsonValue

from maivn._internal.core import ToolEventPayload, ToolEventValue
from maivn._internal.core.application_services.execution import BackgroundExecutor
from maivn._internal.core.application_services.tool_execution import (
    ToolEventDispatcher,
    ToolExecutionService,
)
from maivn._internal.core.application_services.tool_execution.tool_event_dispatcher import (
    dispatcher,
)
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

JsonObject = dict[str, JsonValue]
JsonArray = list[JsonValue]
ToolEventCoordinator = dispatcher.ToolEventCoordinator


class InlineExecutor:
    """Test-double executor that runs submitted callables immediately."""

    def submit(self, fn: Callable[..., object], *args: object, **kwargs: object) -> Future[object]:
        future: Future[object] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class StubReporter:
    starts: list[tuple[str, str, str, str | None, dict[str, object] | None]]
    completes: list[tuple[str, int, object]]
    errors: list[tuple[str, str]]
    progress_updates: list[tuple[str, str]]

    def __init__(self) -> None:
        self.starts = []
        self.completes = []
        self.errors = []
        self.progress_updates = []

    def report_tool_start(
        self,
        tool_id: str,
        event_id: str,
        tool_type: str,
        agent_name: str | None,
        tool_args: dict[str, object] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        _ = swarm_name
        self.starts.append((tool_id, event_id, tool_type, agent_name, tool_args))

    def update_progress(self, task: str, message: str) -> None:
        self.progress_updates.append((task, message))

    def report_tool_complete(self, event_id: str, elapsed_ms: int, result: object) -> None:
        _ = (event_id, elapsed_ms)
        self.completes.append((event_id, elapsed_ms, result))

    def report_tool_error(
        self,
        tool_id: str,
        message: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        _ = (event_id, elapsed_ms)
        self.errors.append((tool_id, message))


class _FakeTool:
    tags: set[str]
    tool_type: str | None
    target_agent_id: str | None
    agent_id: str | None

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
        # Tests pattern-match on the class display name; assigning to a
        # per-instance ``__class__.__name__`` is intentionally test-only.
        type(self).__name__ = cls_name


class FakeToolExecutionService:
    """Enough of ToolExecutionService for dispatcher tests."""

    _tools: dict[str, _FakeTool]
    execute_calls: list[tuple[str, dict[str, object], object]]

    def __init__(self) -> None:
        self._tools = {}
        self.execute_calls = []

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
        self._tools[tool_id] = _FakeTool(
            tags=tags,
            cls_name=cls_name,
            tool_type=tool_type,
            target_agent_id=target_agent_id,
            agent_id=agent_id,
        )

    def resolve_tool(self, tool_id: str) -> _FakeTool:
        return self._tools[tool_id]

    def execute_tool_call(
        self,
        tool_id: str,
        args: dict[str, object],
        context: object = None,
        *,
        tool_event_id: str | None = None,
    ) -> object:
        _ = tool_event_id
        self.execute_calls.append((tool_id, args, context))
        return {"raw": f"{tool_id}:{args}"}

    def to_jsonable(self, result: object) -> object:
        return {"serialized": result}


class FakeCoordinator:
    _scope: object | None
    tool_results: dict[str, object]
    single_tool_calls: list[tuple[str, dict[str, object], dict[str, object]]]
    batch_calls: list[list[JsonObject]]
    multi_calls: list[dict[str, ToolEventPayload]]

    def __init__(self) -> None:
        self._scope = None
        self.tool_results = {}
        self.single_tool_calls = []
        self.batch_calls = []
        self.multi_calls = []

    def get_tool_results(self) -> dict[str, object]:
        return self.tool_results

    def _store_result(self, tool_id: str, tool: object | None, result: object) -> None:
        self.tool_results[tool_id] = result
        if tool is None:
            return
        tool_type = cast(object, getattr(tool, "tool_type", None))
        if tool_type != "agent":
            return
        agent_id = cast(object, getattr(tool, "target_agent_id", None)) or cast(
            object, getattr(tool, "agent_id", None)
        )
        if agent_id:
            self.tool_results[str(agent_id)] = result

    def execute_single_tool(
        self,
        tool_id: str,
        args: dict[str, object],
        *,
        context: dict[str, object] | None = None,
    ) -> object:
        ctx = context or {}
        self.single_tool_calls.append((tool_id, args, ctx))
        return {"raw": f"{tool_id}:{args}"}

    def to_jsonable(self, result: object) -> object:
        return {"serialized": result}

    def execute_tool_events(self, tool_events: dict[str, ToolEventPayload]) -> JsonObject:
        self.multi_calls.append(tool_events)
        return {key: cast(JsonValue, {"result": "ok"}) for key in tool_events}

    def execute_tool_batch(
        self,
        batch: list[JsonObject],
        *,
        on_tool_complete: Callable[[int, str, object], None] | None = None,
    ) -> JsonArray:
        self.batch_calls.append(batch)
        results: JsonArray = [f"result-{idx}" for idx, _ in enumerate(batch)]
        if on_tool_complete is not None:
            for idx, tc in enumerate(batch):
                tool_id_value = tc.get("tool_id", "")
                tool_id = tool_id_value if isinstance(tool_id_value, str) else ""
                on_tool_complete(idx, tool_id, results[idx])
        return results


def _as_coordinator(coord: FakeCoordinator) -> ToolEventCoordinator:
    return cast(ToolEventCoordinator, cast(object, coord))


def _as_service(service: FakeToolExecutionService) -> ToolExecutionService:
    return cast(ToolExecutionService, cast(object, service))


def _as_executor(executor: InlineExecutor) -> BackgroundExecutor:
    return cast(BackgroundExecutor, cast(object, executor))


def _as_reporter(reporter: StubReporter) -> BaseReporter:
    return cast(BaseReporter, cast(object, reporter))


def _build_dispatcher(
    *,
    coordinator: FakeCoordinator | None = None,
    tool_execution_service: FakeToolExecutionService | None = None,
    reporter: StubReporter | None = None,
    post_resume: list[tuple[str, JsonObject]] | None = None,
    agent_count: int = 1,
) -> tuple[ToolEventDispatcher, FakeCoordinator, StubReporter, list[tuple[str, JsonObject]]]:
    coordinator = coordinator or FakeCoordinator()
    service = tool_execution_service or FakeToolExecutionService()
    reporter = reporter or StubReporter()
    posted: list[tuple[str, JsonObject]] = post_resume if post_resume is not None else []

    dispatcher = ToolEventDispatcher(
        coordinator=_as_coordinator(coordinator),
        tool_execution_service=_as_service(service),
        background_executor=_as_executor(InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: _as_reporter(reporter),
        progress_task_supplier=lambda: "task-123",
        agent_count_supplier=lambda: agent_count,
        tool_agent_lookup=lambda _: "agent-alpha",
        logger=None,
    )
    return dispatcher, coordinator, reporter, posted


def test_submit_tool_call_executes_coordinator_and_reports() -> None:
    _, coordinator, reporter, posted = _build_dispatcher(agent_count=2)
    service = FakeToolExecutionService()
    service.add_tool("tool-123", tags={"agent_invocation"}, target_agent_id="agent-123")
    dispatcher = ToolEventDispatcher(
        coordinator=_as_coordinator(coordinator),
        tool_execution_service=_as_service(service),
        background_executor=_as_executor(InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: _as_reporter(reporter),
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
    stored = cast(dict[str, object], coordinator.tool_results["tool-123"])
    assert stored["raw"] == "tool-123:{'x': 1}"
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
    completion_payload = cast(dict[str, object], reporter.completes[0][2])
    serialized = cast(dict[str, object], completion_payload["result"])
    serialized_raw = cast(dict[str, object], serialized["serialized"])
    assert serialized_raw["raw"] == "tool-123:{'x': 1}"
    assert completion_payload["private_data_injected"] == {"foo": "bar"}
    assert completion_payload["interrupt_data_injected"] == {"prompt": "yes"}


def test_submit_tool_call_reports_injected_keys_even_when_args_empty() -> None:
    _, coordinator, reporter, posted = _build_dispatcher()
    service = FakeToolExecutionService()
    service.add_tool("tool-123")
    dispatcher = ToolEventDispatcher(
        coordinator=_as_coordinator(coordinator),
        tool_execution_service=_as_service(service),
        background_executor=_as_executor(InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: _as_reporter(reporter),
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
    _, coordinator, reporter, posted = _build_dispatcher()
    service = FakeToolExecutionService()
    service.add_tool("tool-123")
    dispatcher = ToolEventDispatcher(
        coordinator=_as_coordinator(coordinator),
        tool_execution_service=_as_service(service),
        background_executor=_as_executor(InlineExecutor()),
        post_resume=lambda url, payload: posted.append((url, payload)),
        reporter_supplier=lambda: _as_reporter(reporter),
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
    posted: list[tuple[str, JsonObject]] = []
    dispatcher = ToolEventDispatcher(
        coordinator=_as_coordinator(coordinator),
        tool_execution_service=_as_service(service),
        background_executor=_as_executor(InlineExecutor()),
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
    stored = cast(dict[str, object], coordinator.tool_results["tool-123"])
    assert stored["raw"] == "tool-123:{}"
    alias_stored = cast(dict[str, object], coordinator.tool_results["agent-42"])
    assert alias_stored["raw"] == "tool-123:{}"


def test_process_tool_batch_and_requests_route_results() -> None:
    dispatcher, coordinator, _reporter, posted = _build_dispatcher()

    batch_value: ToolEventValue = cast(
        ToolEventValue,
        cast(object, {"tool_calls": [{"tool_id": "a"}, {"tool_id": "b"}]}),
    )
    dispatcher.process_tool_batch(
        "batch-event",
        batch_value,
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

    request_payloads: dict[str, ToolEventPayload] = cast(
        dict[str, ToolEventPayload],
        cast(object, {"evt": {"value": {"tool_call": {"tool_id": "x"}}}}),
    )
    dispatcher.process_tool_requests(
        request_payloads,
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

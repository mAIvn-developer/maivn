# pyright: strict
from __future__ import annotations

from typing import cast, override

from maivn_shared import SessionClientProtocol
from pydantic import JsonValue

from maivn._internal.core.application_services.events.interrupt_manager import (
    InterruptHandler,
    InterruptManager,
)
from maivn._internal.core.services.interrupt_service import InterruptService
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

JsonObject = dict[str, JsonValue]


class StubReporter:
    _responses: list[str]
    inputs_requested: list[dict[str, object]]
    progress_updates: list[str]

    def __init__(self, *, responses: list[str]) -> None:
        self._responses = responses
        self.inputs_requested = []
        self.progress_updates = []

    def get_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        self.inputs_requested.append(
            {
                "prompt": prompt,
                "input_type": input_type,
                "choices": choices,
                "data_key": data_key,
                "arg_name": arg_name,
            }
        )
        return self._responses.pop(0)

    def update_progress(self, task: str, message: str) -> None:
        self.progress_updates.append(f"{task}:{message}")


class StubInterruptService(InterruptService):
    prompts: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.prompts = []

    @override
    def get_user_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:  # pragma: no cover - reporter path uses reporter
        _ = (input_type, choices, data_key, arg_name)
        self.prompts.append(prompt)
        return ""


class StubHttpResponse:
    _payload: JsonObject

    def __init__(self, payload: JsonObject) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> JsonObject:
        return self._payload


class StubHttpClient:
    posts: list[tuple[str, JsonObject, dict[str, str] | None]]
    _response_payload: JsonObject

    def __init__(self, response_payload: JsonObject) -> None:
        self.posts = []
        self._response_payload = response_payload

    def post(
        self,
        url: str,
        json: JsonObject,
        *,
        headers: dict[str, str] | None = None,
    ) -> StubHttpResponse:
        self.posts.append((url, json, headers))
        return StubHttpResponse(self._response_payload)


class StubClient:
    base_url: str
    http_client: StubHttpClient
    _thread_id: str | None

    def __init__(self, base_url: str, http_client: StubHttpClient) -> None:
        self.base_url = base_url
        self.http_client = http_client
        self._thread_id = None

    def headers(self) -> dict[str, str]:
        return {"X-API-Key": "test-key"}

    def start_session(self, *, payload: JsonObject) -> JsonObject:
        return payload

    def get_thread_id(self, *, create_if_missing: bool) -> str | None:
        if create_if_missing and self._thread_id is None:
            self._thread_id = "thread-1"
        return self._thread_id


class StubAgent:
    private_data: JsonObject | None

    def __init__(self) -> None:
        self.private_data = {}


def _build_handler(
    *,
    reporter: StubReporter,
    client: StubClient,
    interrupt_service: InterruptService | None = None,
    manager: InterruptManager | None = None,
    resume_calls: list[tuple[str, JsonObject]] | None = None,
    agent: StubAgent | None = None,
) -> tuple[InterruptHandler, InterruptManager, list[tuple[str, JsonObject]], StubAgent]:
    manager = manager or InterruptManager()
    agent = agent or StubAgent()
    resume_calls = resume_calls if resume_calls is not None else []
    handler = InterruptHandler(
        agent=agent,
        client=cast(SessionClientProtocol, cast(object, client)),
        interrupt_service=interrupt_service or StubInterruptService(),
        interrupt_manager=manager,
        resume_callback=lambda url, payload: resume_calls.append((url, payload)),
        reporter_supplier=lambda: cast(BaseReporter, cast(object, reporter)),
        progress_task_supplier=lambda: "task-1",
        logger=None,
    )
    return handler, manager, resume_calls, agent


def test_handle_user_input_request_collects_via_reporter() -> None:
    reporter = StubReporter(responses=["Chad"])
    http_client = StubHttpClient({})
    client = StubClient("https://api.local", http_client)
    handler, manager, resume_calls, _ = _build_handler(
        reporter=reporter,
        client=client,
    )

    handler.handle_user_input_request(
        "tool-event-1",
        {"arg_name": "user_name", "prompt": "Name?"},
        "https://resume",
    )

    assert "Name?" in cast(str, reporter.inputs_requested[0]["prompt"])
    assert reporter.inputs_requested[0]["arg_name"] == "user_name"
    assert resume_calls == [
        (
            "https://resume",
            {
                "tool-event-1": {
                    "arg_name": "user_name",
                    "value": "Chad",
                }
            },
        )
    ]
    assert manager.resumed_session_id is None


def test_handle_interrupt_required_resumes_checkpoint_merges_private_data() -> None:
    reporter = StubReporter(responses=["blue"])
    response_payload: JsonObject = {
        "session_id": "session-456",
        "private_data": {"favorite_color": "blue"},
    }
    http_client = StubHttpClient(response_payload)
    client = StubClient("https://api.local", http_client)
    resume_calls: list[tuple[str, JsonObject]] = []
    handler, manager, _, agent = _build_handler(
        reporter=reporter,
        client=client,
        manager=InterruptManager(),
        resume_calls=resume_calls,
    )

    handler.handle_interrupt_required(
        {
            "checkpoint_id": "chk-1",
            "data_key": "favorite_color",
            "arg_name": "favorite_color",
            "prompt": "Color?",
            "input_type": "choice",
            "choices": ["blue", "green"],
        },
        "unused-resume",
    )

    assert reporter.inputs_requested == [
        {
            "prompt": "\nColor?",
            "input_type": "choice",
            "choices": ["blue", "green"],
            "data_key": "favorite_color",
            "arg_name": "favorite_color",
        }
    ]
    assert http_client.posts == [
        (
            "https://api.local/api/v1/checkpoints/chk-1/resume",
            {"interrupt_data": {"favorite_color": "blue"}},
            {"X-API-Key": "test-key"},
        )
    ]
    assert manager.resumed_session_id == "session-456"
    # The owner's own private values from the resume response are merged into
    # agent state so subsequent client-side rehydration can resolve placeholders.
    assert agent.private_data == {"favorite_color": "blue"}

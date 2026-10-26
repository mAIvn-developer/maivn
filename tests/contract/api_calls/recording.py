"""Record what the SDK sends to the API and which response fields it reads.

A driver runs real SDK code against `RecordingTransport`, a fake API that
answers from the driver's own script. Every request is recorded as the SDK
built it (method, path, query names, body shape), and every JSON reply is
handed back as `TrackedDict` objects that note each key the SDK looks up, so
the record says which response fields the SDK actually reads -- including keys
the reply did not carry. Pydantic validation looks keys up through `get`, so a
response model's fields count as reads without any knowledge of the model.

Some replies are parsed from raw bytes (`model_validate_json`, a TypeAdapter's
`validate_json`), never through `response.json()`. While a driver runs inside
`observing_byte_replies`, bytes this fake API served are also validated, for
observation only, from a tracked copy, so those reads are recorded too; the
SDK still receives the result of its own parse.

Nothing here knows the server's routes or schemas; the root contract test
compares this record with the servers' own OpenAPI documents.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import unittest.mock
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, TypeAlias

import httpx
from pydantic import AnyUrl, BaseModel, TypeAdapter

from maivn import Client, ClientConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable

JsonValue: TypeAlias = Any
# HTTPS: the SDK refuses plain HTTP to a vault origin that is not loopback.
BASE_URL = 'https://api.contract.test'
API_KEY = 'contract-test-key'
_SDK_PACKAGE = 'maivn'
_TRANSPORT_MODULES = frozenset(
    {'maivn._internal.transport.http', 'maivn._internal.transport.leases'}
)


@dataclass
class Reads:
    """Response field paths the SDK looked up, and the objects it enumerated."""

    fields: set[str] = field(default_factory=set[str])
    enumerated: set[str] = field(default_factory=set[str])


def _join(path: str, key: object) -> str:
    return f'{path}.{key}' if path else str(key)


class TrackedDict(dict[str, Any]):
    """A JSON object that records every key the SDK looks up in it."""

    def __init__(self, value: dict[str, Any], reads: Reads, path: str) -> None:
        """Wrap `value`, found at `path` in the reply, recording into `reads`."""
        super().__init__({key: track(item, reads, _join(path, key)) for key, item in value.items()})
        self._reads = reads
        self._path = path

    def _read(self, key: object) -> None:
        self._reads.fields.add(_join(self._path, key))

    def _enumerate(self) -> None:
        self._reads.enumerated.add(self._path or '$')

    def __getitem__(self, key: str) -> Any:
        """Record the lookup, then return the value."""
        self._read(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        """Record the lookup, then answer the membership test."""
        self._read(key)
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        """Record that the whole object was enumerated."""
        self._enumerate()
        return super().__iter__()

    def get(self, key: str, default: Any = None) -> Any:
        """Record the lookup, then return the value or `default`."""
        self._read(key)
        return super().get(key, default)

    def pop(self, key: str, *default: Any) -> Any:
        """Record the lookup, then remove and return the value."""
        self._read(key)
        return super().pop(key, *default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Record the lookup, then return or insert the value."""
        self._read(key)
        return super().setdefault(key, default)

    def keys(self) -> Any:
        """Record that the whole object was enumerated."""
        self._enumerate()
        return super().keys()

    def items(self) -> Any:
        """Record that the whole object was enumerated."""
        self._enumerate()
        return super().items()

    def values(self) -> Any:
        """Record that the whole object was enumerated."""
        self._enumerate()
        return super().values()

    def copy(self) -> dict[str, Any]:
        """Record that the whole object was enumerated, then copy it."""
        self._enumerate()
        return dict(super().items())


def track(value: JsonValue, reads: Reads, path: str) -> JsonValue:
    """Wrap every JSON object in `value` so key lookups land in `reads`."""
    if isinstance(value, dict):
        return TrackedDict(value, reads, path)  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, list):
        return [track(item, reads, f'{path}[]') for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


_SCALAR_TYPES = ((bool, 'boolean'), (int, 'integer'), (float, 'number'), (str, 'string'))


def shape(value: JsonValue) -> JsonValue:
    """Replace every scalar with its JSON type name: the body's structure, not its values."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        shapes = {json.dumps(shape(item), sort_keys=True) for item in value}  # pyright: ignore[reportUnknownVariableType]
        return [json.loads(item) for item in sorted(shapes)]
    return next((name for kind, name in _SCALAR_TYPES if isinstance(value, kind)), 'null')


@dataclass(frozen=True)
class Reply:
    """One scripted API answer."""

    status: int = HTTPStatus.OK
    json: JsonValue = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def kind(self) -> str:
        """Return how the SDK consumes this reply: json, sse, bytes or empty."""
        if self.content is None:
            return 'empty' if self.json is None else 'json'
        return 'sse' if self.headers.get('content-type') == 'text/event-stream' else 'bytes'

    @staticmethod
    def sse(*events: dict[str, Any]) -> Reply:
        """Answer with SSE frames the way the API frames them: `id`, `event`, then `data`."""
        frames = ''.join(
            f'id: {position}\nevent: {event["type"]}\ndata: {json.dumps(event)}\n\n'
            for position, event in enumerate(events, start=1)
        )
        return Reply(content=frames.encode(), headers={'content-type': 'text/event-stream'})


@dataclass
class RecordedCall:
    """One request the SDK sent and what it read from the reply."""

    driver: str
    method: str
    path: str
    query: tuple[str, ...]
    body: JsonValue
    status: int
    response: str
    callers: tuple[str, ...]
    reads: Reads = field(default_factory=Reads)

    def snapshot(self) -> dict[str, Any]:
        """Return the deterministic, value-free record the root contract test reads."""
        return {
            'driver': self.driver,
            'method': self.method,
            'path': self.path,
            'query': list(self.query),
            'body': self.body,
            'status': self.status,
            'response': self.response,
            'reads': sorted(self.reads.fields),
            'enumerated': sorted(self.reads.enumerated),
        }


Responder: TypeAlias = Callable[[httpx.Request], Reply]


def _request_body(request: httpx.Request, content: bytes) -> JsonValue:
    content_type = request.headers.get('content-type', '')
    if not content:
        return None
    if content_type.startswith('application/json'):
        return shape(json.loads(content))
    return {'$content-type': content_type}


def _sdk_callers() -> tuple[str, ...]:
    """Return `module:Class.method` for every SDK frame on the stack above the transport."""
    callers: list[str] = []
    frame = inspect.currentframe()
    while frame is not None:
        module = frame.f_globals.get('__name__', '')
        if module.split('.')[0] == _SDK_PACKAGE and module not in _TRANSPORT_MODULES:
            owner = frame.f_locals.get('self')
            name = frame.f_code.co_name
            if owner is not None and not name.startswith('_'):
                callers.append(f'{type(owner).__module__}:{type(owner).__qualname__}.{name}')
        frame = frame.f_back
    return tuple(sorted(set(callers)))


class TrackedResponse(httpx.Response):
    """A response whose JSON body records the SDK's key lookups."""

    def __init__(self, reads: Reads, *args: Any, **kwargs: Any) -> None:
        """Build the response; `reads` collects the key lookups."""
        super().__init__(*args, **kwargs)
        self._reads = reads

    def json(self, **kwargs: Any) -> JsonValue:
        """Parse the body, then wrap it so key lookups are recorded."""
        return track(super().json(**kwargs), self._reads, '')


class RecordingTransport(httpx.AsyncBaseTransport):
    """A fake API that answers from a script and records every request."""

    def __init__(self, driver: str, responder: Responder) -> None:
        """Answer every request with `responder` and record it under `driver`."""
        self.driver = driver
        self.calls: list[RecordedCall] = []
        self.served: dict[bytes, Reads] = {}
        self._responder = responder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Record the request, then answer it from the script."""
        content = await request.aread()
        reply = self._responder(request)
        call = RecordedCall(
            driver=self.driver,
            method=request.method,
            path=request.url.path,
            query=tuple(sorted({key for key, _ in request.url.params.multi_items()})),
            body=_request_body(request, content),
            status=reply.status,
            response=reply.kind,
            callers=_sdk_callers(),
        )
        self.calls.append(call)
        body: dict[str, Any] = {}
        if reply.content is not None:
            body['content'] = reply.content
        elif reply.json is not None:
            body['json'] = reply.json
        response = TrackedResponse(
            call.reads, reply.status, headers=reply.headers, request=request, **body
        )
        if reply.kind == 'json':
            self.served[response.content] = call.reads
        return response

    async def aclose(self) -> None:
        """Stay open: the SDK's lease closes transports between requests."""


@contextlib.contextmanager
def observing_byte_replies(transport: RecordingTransport) -> Generator[None]:
    """Record the reads of replies the SDK parses from the bytes `transport` served."""
    model_validate_json = BaseModel.model_validate_json.__func__
    adapter_validate_json: Callable[..., object] = TypeAdapter[Any].validate_json

    def observe(validate: Callable[[Any], object], data: object) -> None:
        reads = transport.served.get(data.encode() if isinstance(data, str) else bytes(data))  # pyright: ignore[reportArgumentType]
        if reads is not None:
            # Observation only: the parse the SDK relies on happens below, unchanged.
            with contextlib.suppress(Exception):
                validate(track(json.loads(data), reads, ''))  # pyright: ignore[reportArgumentType]

    def model_json(cls: type[BaseModel], json_data: object, **options: Any) -> object:
        observe(cls.model_validate, json_data)
        return model_validate_json(cls, json_data, **options)

    def adapter_json(adapter: TypeAdapter[Any], data: object, **options: Any) -> object:
        observe(adapter.validate_python, data)
        return adapter_validate_json(adapter, data, **options)

    with (
        unittest.mock.patch.object(BaseModel, 'model_validate_json', classmethod(model_json)),
        unittest.mock.patch.object(TypeAdapter, 'validate_json', adapter_json),
    ):
        yield


class FakeApi:
    """One driver's scripted API: exact (method, path) answers, anything else fails loudly."""

    def __init__(self, driver: str) -> None:
        """Start an empty script recorded under `driver`."""
        self._answers: dict[tuple[str, str], list[Reply]] = {}
        self.transport = RecordingTransport(driver, self._respond)

    def answer(self, method: str, path: str, *replies: Reply) -> None:
        """Answer `method path` with `replies` in order, repeating the last one."""
        self._answers[(method, path)] = list(replies)

    def client(self, **options: Any) -> Client:
        """Return an SDK client whose every request reaches this fake API."""
        return Client(
            config=ClientConfig(api_key=API_KEY, base_url=AnyUrl(BASE_URL)),
            transport=self.transport,
            vault_transport=self.transport,
            **options,
        )

    @property
    def calls(self) -> list[RecordedCall]:
        """Return every request recorded so far."""
        return self.transport.calls

    def _respond(self, request: httpx.Request) -> Reply:
        key = (request.method, request.url.path)
        replies = self._answers.get(key)
        if not replies:
            message = f'{self.transport.driver}: unscripted request {key[0]} {key[1]}'
            raise AssertionError(message)
        return replies.pop(0) if len(replies) > 1 else replies[0]


Driver: TypeAlias = 'Callable[[FakeApi], Awaitable[None]]'

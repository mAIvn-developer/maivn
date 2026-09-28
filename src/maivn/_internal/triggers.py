"""Durable trigger registration builders shared by Agent and Swarm scopes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from maivn_contracts.scenarios import (
    OutputConnectionSelector,
    RetryPolicy,
    TriggerOptions,
    TriggerSource,
)
from maivn_contracts.scenarios.producers import (
    ArmSLATimerRequest,
    ConversationReplyReceipt,
    ConversationReplyRequest,
    SatisfySLATimerRequest,
    SLATimerReceipt,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping

    from maivn._internal.client import Client
    from maivn._internal.models import StreamEvent
    from maivn._internal.transport.http import JsonObject

from maivn._internal.api.async_stream import run_blocking, stream_async_iterator
from maivn._internal.errors import MaivnHTTPError, MaivnSDKError

TriggerMessagesInput: TypeAlias = str | Sequence[str]
BackoffStrategy: TypeAlias = Literal['fixed', 'exponential']
_MAX_HISTORY_LIMIT = 100
# The two fields a create body carries that an update never does: the update's
# path already names the scenario, and the trigger identity is immutable.
_IDENTITY_FIELDS = frozenset({'scenario_id', 'trigger_id'})


class TriggerInvocationBuilder:
    """One source-typed durable trigger registration in progress."""

    def __init__(
        self,
        client: Client | None,
        source: TriggerSource,
        *,
        client_provider: Callable[[], Client] | None = None,
    ) -> None:
        """Bind one authoritative trigger source to an SDK authoring scope.

        The client may be absent while an app is merely being *described*
        (Studio discovery imports the module without credentials); it is
        required the moment the trigger is registered with ``invoke``.
        """
        self._client = client
        self._client_provider = client_provider
        self._source = source
        self._key: str | None = None
        self._name: str | None = None
        self._agent_id: str | None = None
        self._agent_version: int | None = None
        self._filter: str | None = None
        self._options: TriggerOptions | None = None
        self._output_binding: JsonObject = {'kind': 'display_in_app'}
        self._output_connection: str | None = None
        self._messages: list[str] | None = None

    @property
    def source(self) -> TriggerSource:
        """Return the immutable authoritative source specification."""
        return self._source

    def key(self, value: str) -> TriggerInvocationBuilder:
        """Set the stable developer-owned registration key."""
        self._key = _nonblank(value, field='key')
        return self

    def named(self, value: str) -> TriggerInvocationBuilder:
        """Set the human-readable trigger/scenario name."""
        self._name = _nonblank(value, field='name')
        return self

    def target(self, agent_id: str, *, version: int) -> TriggerInvocationBuilder:
        """Pin the trigger to one immutable deployed-agent version."""
        self._agent_id = _nonblank(agent_id, field='agent_id')
        if version < 1:
            message = 'trigger target version must be positive'
            raise ValueError(message)
        self._agent_version = version
        return self

    def where(self, jsonpath: str | None) -> TriggerInvocationBuilder:
        """Set or clear the JSONPath admission predicate."""
        self._filter = None if jsonpath is None else _nonblank(jsonpath, field='filter')
        return self

    def with_options(  # noqa: PLR0913 - mirrors the bounded trigger-options contract.
        self,
        *,
        concurrency: int = 4,
        max_attempts: int | None = None,
        initial_delay_seconds: int = 30,
        max_delay_seconds: int = 3600,
        backoff: BackoffStrategy = 'exponential',
        dedupe_window_seconds: int = 86400,
        depth_cap: int = 8,
        fanout_cap: int = 100,
        shadow: bool = False,
        fire_budget: int = 5,
    ) -> TriggerInvocationBuilder:
        """Configure bounded retry, dedupe, lineage, and shadow guard options."""
        retry_policy = (
            None
            if max_attempts is None
            else RetryPolicy(
                max_attempts=max_attempts,
                initial_delay_seconds=initial_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                backoff=backoff,
            )
        )
        self._options = TriggerOptions(
            concurrency=concurrency,
            retry_policy=retry_policy,
            dedupe_window_seconds=dedupe_window_seconds,
            depth_cap=depth_cap,
            fanout_cap=fanout_cap,
            shadow=shadow,
            fire_budget=fire_budget,
        )
        return self

    def asking(self, messages: TriggerMessagesInput) -> TriggerInvocationBuilder:
        """Set what the app is asked when this trigger fires, without registering.

        A trigger is the app called from an event instead of from a person, so
        something has to stand in for what the person would have typed. That
        used to be reachable only through ``invoke(messages)``, which also
        registers and therefore needs a client - so an app that merely
        *declares* its triggers had nowhere to put its prompt, and a tool
        registering that declaration later would have had to invent one.
        Inventing it would make the registered trigger differ from the app's
        own code, which is the one thing this whole surface exists to prevent.
        """
        self._messages = _template_messages(messages)
        return self

    def emit(self, event_type: str) -> TriggerInvocationBuilder:
        """Emit each completed invocation as one canonical event output."""
        self._output_binding = {
            'kind': 'event_emission',
            'target': {'event_type': _nonblank(event_type, field='event_type')},
        }
        self._output_connection = None
        return self

    def to_slack(self, channel_ref: str) -> TriggerInvocationBuilder:
        """Send each completed invocation to one Slack channel."""
        self._output_binding = {
            'kind': 'slack_message',
            'target': {'channel_ref': _nonblank(channel_ref, field='channel_ref')},
        }
        self._output_connection = None
        return self

    def to_discord(self, channel_ref: str) -> TriggerInvocationBuilder:
        """Send each completed invocation to one Discord channel."""
        self._output_binding = {
            'kind': 'discord_message',
            'target': {'channel_ref': _nonblank(channel_ref, field='channel_ref')},
        }
        self._output_connection = None
        return self

    def reply_by_email(self, thread_ref: str) -> TriggerInvocationBuilder:
        """Reply to one email thread with each completed invocation."""
        self._output_binding = {
            'kind': 'email_reply',
            'target': {'thread_ref': _nonblank(thread_ref, field='thread_ref')},
        }
        self._output_connection = None
        return self

    def comment_on_github(
        self,
        repository_ref: str,
        *,
        issue_ref: str,
    ) -> TriggerInvocationBuilder:
        """Comment on one GitHub issue or pull request with each completed invocation."""
        self._output_binding = {
            'kind': 'github_comment',
            'target': {
                'repository_ref': _nonblank(repository_ref, field='repository_ref'),
                'issue_ref': _nonblank(issue_ref, field='issue_ref'),
            },
        }
        self._output_connection = None
        return self

    def to_webhook(self, endpoint_ref: str) -> TriggerInvocationBuilder:
        """Send each completed invocation to one configured webhook endpoint."""
        self._output_binding = {
            'kind': 'webhook',
            'target': {'endpoint_ref': _nonblank(endpoint_ref, field='endpoint_ref')},
        }
        self._output_connection = None
        return self

    def display_in_app(self) -> TriggerInvocationBuilder:
        """Display each completed invocation in the app UI."""
        self._output_connection = None
        self._output_binding = {'kind': 'display_in_app'}
        return self

    def to_connection(self, connection_id: str) -> TriggerInvocationBuilder:
        """Send results to a saved output connection in the trigger's project.

        The platform resolves its private destination and provider adapter.
        Declarations contain only the safe connection ID. The last output
        helper called determines the destination, including display_in_app().
        """
        value = _nonblank(connection_id, field='connection_id')
        self._output_connection = OutputConnectionSelector(connection_id=value).connection_id
        return self

    def invoke(self, messages: TriggerMessagesInput | None = None) -> Trigger:
        """Register or update one durable trigger synchronously."""
        return run_blocking(lambda: self.ainvoke(messages))

    def declaration_payload(self, messages: TriggerMessagesInput | None = None) -> JsonObject:
        """Return exactly the scenario-create body ``invoke`` would send.

        One writer for the registration shape. ``ainvoke`` posts this verbatim,
        and a tool that wants to show what *would* be registered reads the same
        object instead of reconstructing it and drifting from what actually
        goes on the wire.

        Requires ``.key()`` and a target. Scope-bound builders inherit their
        owner's target; standalone builders need ``.target(...)`` explicitly.
        """
        key, agent_id, _version = self._required_registration()
        scenario_id, trigger_id = _stable_ids(agent_id, key)
        resolved = self._required_messages(messages)
        return {
            'scenario_id': scenario_id,
            'trigger_id': trigger_id,
            **self._registration_fields(resolved),
        }

    def declaration(self) -> JsonObject:
        """Return the trigger as declared so far, without registering it.

        Everything a tool needs to list a trigger beside the app that carries
        it: the source, the target, the output, and the stable ids the
        registration would use, so a declaration in code can be matched to
        its registered twin on the platform.

        Unlike :meth:`declaration_payload` this tolerates an unfinished
        declaration - Studio lists what the developer has written so far - and
        so it carries no message binding and null ids until the declaration
        names both a key and a target.
        """
        ids = (
            stable_trigger_ids(self._agent_id, self._key)
            if self._agent_id is not None and self._key is not None
            else (None, None)
        )
        return {
            'key': self._key,
            **self._registration_fields(None),
            'scenario_id': ids[0],
            'trigger_id': ids[1],
        }

    def _required_messages(self, messages: TriggerMessagesInput | None) -> list[str]:
        """Resolve the message template, preferring an explicit one over the declared one."""
        if messages is not None:
            return _template_messages(messages)
        if self._messages is not None:
            return list(self._messages)
        message = (
            'durable trigger registration requires a message template: '
            'pass one to invoke(...), or declare it with .asking(...)'
        )
        raise ValueError(message)

    def _registration_fields(self, messages: TriggerMessagesInput | None) -> JsonObject:
        """Build the mutable registration fields shared by create, update, and declaration.

        ``messages`` is ``None`` only for a declaration, which describes a
        trigger rather than registering one and therefore carries no message
        template.
        """
        fields: JsonObject = {
            'name': self._name or self._key,
            'trigger_source': self._source.model_dump(mode='json', exclude_none=True),
            'agent': (
                None
                if self._agent_id is None or self._agent_version is None
                else {'agent_id': self._agent_id, 'version': self._agent_version}
            ),
            'trigger_filter': self._filter,
            'trigger_options': (
                None
                if self._options is None
                else self._options.model_dump(mode='json', exclude_none=True)
            ),
        }
        if self._output_connection is None:
            fields['output_binding'] = dict(self._output_binding)
        else:
            fields['output_connection'] = {'connection_id': self._output_connection}
        resolved = messages if messages is not None else self._messages
        if resolved is not None:
            fields['trigger_binding'] = {
                'kind': 'template',
                'messages': _template_messages(resolved),
            }
        return fields

    def _require_client(self) -> Client:
        if self._client is None and self._client_provider is not None:
            self._client = self._client_provider()
        if self._client is None:
            message = 'trigger registration requires an initialized SDK client'
            raise MaivnSDKError(message)
        return self._client

    async def ainvoke(self, messages: TriggerMessagesInput | None = None) -> Trigger:
        """Register or update one durable trigger asynchronously."""
        client = self._require_client()
        payload = self.declaration_payload(messages)
        scenario_id = cast('str', payload['scenario_id'])
        # An update addresses the scenario by path, so it carries every mutable
        # field but neither identity - the create body minus exactly those two.
        update = {key: value for key, value in payload.items() if key not in _IDENTITY_FIELDS}
        http = client.control_http()
        path = f'/v1/scenarios/{scenario_id}'
        try:
            _ = await http.get(path)
        except MaivnHTTPError as exc:
            if exc.status_code != HTTPStatus.NOT_FOUND:
                raise
            body = await http.post('/v1/scenarios', payload)
        else:
            body = await http.patch(path, update)
        return Trigger(client, body)

    def _required_registration(self) -> tuple[str, str, int]:
        if self._key is None:
            message = 'durable trigger registration requires .key(...)'
            raise ValueError(message)
        if self._agent_id is None or self._agent_version is None:
            message = 'durable trigger registration requires .target(agent_id, version=...)'
            raise ValueError(message)
        return self._key, self._agent_id, self._agent_version


class Trigger:
    """Attached durable trigger returned by registration."""

    def __init__(self, client: Client, body: JsonObject) -> None:
        """Attach to one scenario response."""
        self._client = client
        self._body = body
        scenario, trigger = _canvas_parts(body)
        self._scenario_id = _required_string(scenario, 'scenario_id')
        self._project_id = _required_string(scenario, 'project_id')
        self._trigger_id = _required_string(trigger, 'trigger_id')

    @property
    def scenario_id(self) -> str:
        """Return the stable scenario identity."""
        return self._scenario_id

    @property
    def trigger_id(self) -> str:
        """Return the stable trigger identity."""
        return self._trigger_id

    @property
    def status(self) -> str:
        """Return the current attached trigger status."""
        _scenario, trigger = _canvas_parts(self._body)
        return _required_string(trigger, 'status')

    def history(self, *, limit: int = 50) -> tuple[JsonObject, ...]:
        """Return the newest durable sessions for this registered scenario."""
        return run_blocking(lambda: self.ahistory(limit=limit))

    async def ahistory(self, *, limit: int = 50) -> tuple[JsonObject, ...]:
        """Asynchronously return durable sessions for this registered scenario."""
        if not 1 <= limit <= _MAX_HISTORY_LIMIT:
            message = 'trigger history limit must be between 1 and 100'
            raise ValueError(message)
        body = await self._client.control_http().get(
            '/v1/sessions',
            params={
                'project_id': self._project_id,
                'scenario_id': self._scenario_id,
                'limit': limit,
            },
        )
        items: object = body.get('items')
        if not isinstance(items, list):
            message = 'server returned malformed trigger history'
            raise MaivnSDKError(message)
        resolved: list[JsonObject] = []
        for item in cast('list[object]', items):
            if not isinstance(item, dict):
                message = 'server returned malformed trigger history item'
                raise MaivnSDKError(message)
            resolved.append(cast('JsonObject', item))
        return tuple(resolved)

    def replay(self, session_id: str) -> Iterator[StreamEvent]:
        """Replay durable events for one concrete session from this trigger's history."""
        return stream_async_iterator(lambda: self.areplay(session_id))

    async def areplay(self, session_id: str) -> AsyncIterator[StreamEvent]:
        """Asynchronously replay durable events for one concrete session."""
        resolved_session_id = _nonblank(session_id, field='session_id')
        async for event in self._client.stream_session(resolved_session_id):
            yield event

    def pause(self) -> Trigger:
        """Pause automatic source firing synchronously."""
        return run_blocking(self.apause)

    async def apause(self) -> Trigger:
        """Pause automatic source firing asynchronously."""
        return await self._set_lifecycle('pause')

    def resume(self) -> Trigger:
        """Resume automatic source firing synchronously."""
        return run_blocking(self.aresume)

    async def aresume(self) -> Trigger:
        """Resume automatic source firing asynchronously."""
        return await self._set_lifecycle('resume')

    def delete(self) -> None:
        """Archive this durable trigger and scenario synchronously."""
        run_blocking(self.adelete)

    async def adelete(self) -> None:
        """Archive this durable trigger and scenario asynchronously."""
        _ = await self._client.control_http().delete(self._path)

    def arm(
        self, instance_key: str, *, payload: Mapping[str, object] | None = None
    ) -> SLATimerReceipt:
        """Arm one SLA instance; retries must keep its key and payload unchanged."""
        return run_blocking(lambda: self.aarm(instance_key, payload=payload))

    async def aarm(
        self, instance_key: str, *, payload: Mapping[str, object] | None = None
    ) -> SLATimerReceipt:
        """Arm asynchronously using the scenario's declared deadline and server time."""
        request = ArmSLATimerRequest.model_validate(
            {'instance_key': instance_key, 'payload': dict(payload or {})}
        )
        body = await self._client.control_http().post(
            f'{self._path}/sla-timers', request.model_dump(mode='json')
        )
        return SLATimerReceipt.model_validate(body)

    def timer(self, instance_key: str) -> SLATimerReceipt:
        """Read a durable SLA receipt without changing the timer."""
        return run_blocking(lambda: self.atimer(instance_key))

    async def atimer(self, instance_key: str) -> SLATimerReceipt:
        """Read the timer asynchronously; processed state is not an invocation receipt."""
        key = ArmSLATimerRequest(instance_key=instance_key).instance_key
        body = await self._client.control_http().get(f'{self._path}/sla-timers/{key}')
        return SLATimerReceipt.model_validate(body)

    def satisfy(
        self, instance_key: str, *, evidence: Mapping[str, object] | None = None
    ) -> SLATimerReceipt:
        """Record the declared SLA condition; retry with identical evidence."""
        return run_blocking(lambda: self.asatisfy(instance_key, evidence=evidence))

    async def asatisfy(
        self, instance_key: str, *, evidence: Mapping[str, object] | None = None
    ) -> SLATimerReceipt:
        """Record condition evidence asynchronously, with server-owned occurrence time."""
        key = ArmSLATimerRequest(instance_key=instance_key).instance_key
        request = SatisfySLATimerRequest.model_validate({'evidence': dict(evidence or {})})
        body = await self._client.control_http().post(
            f'{self._path}/sla-timers/{key}/satisfy', request.model_dump(mode='json')
        )
        return SLATimerReceipt.model_validate(body)

    def reply(
        self, content: str, *, conversation_key: str, reply_id: str
    ) -> ConversationReplyReceipt:
        """Submit a developer-originated reply using a stable retry identity."""
        return run_blocking(
            lambda: self.areply(content, conversation_key=conversation_key, reply_id=reply_id)
        )

    async def areply(
        self, content: str, *, conversation_key: str, reply_id: str
    ) -> ConversationReplyReceipt:
        """Commit a normalized reply asynchronously; admission and invocation follow later."""
        request = ConversationReplyRequest(
            conversation_key=conversation_key, reply_id=reply_id, content=content
        )
        body = await self._client.control_http().post(
            f'{self._path}/replies', request.model_dump(mode='json')
        )
        return ConversationReplyReceipt.model_validate(body)

    def fire(self, payload: Mapping[str, object] | None = None) -> TriggerFireReceipt:
        """Fire this attached trigger and return its durable correlation."""
        return run_blocking(lambda: self.afire(payload))

    async def afire(
        self,
        payload: Mapping[str, object] | None = None,
    ) -> TriggerFireReceipt:
        """Asynchronously fire this attached trigger."""
        body = await self._client.control_http().post(
            f'{self._path}/fire',
            {'payload': dict(payload or {})},
        )
        return TriggerFireReceipt(
            fire_id=_required_string(body, 'fire_id'),
            trigger_id=_required_string(body, 'trigger_id'),
            session_id=_required_string(body, 'session_id'),
        )

    def fire_stream(
        self,
        payload: Mapping[str, object] | None = None,
    ) -> Iterator[StreamEvent]:
        """Fire synchronously and stream the resulting concrete session."""
        return stream_async_iterator(lambda: self.afire_stream(payload))

    async def afire_stream(
        self,
        payload: Mapping[str, object] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Fire asynchronously and stream the resulting concrete session."""
        receipt = await self.afire(payload)
        async for event in self._client.stream_session(receipt.session_id):
            yield event

    @property
    def _path(self) -> str:
        return f'/v1/scenarios/{self._scenario_id}'

    async def _set_lifecycle(self, action: Literal['pause', 'resume']) -> Trigger:
        self._body = await self._client.control_http().post(f'{self._path}/{action}', {})
        return self


@dataclass(frozen=True, slots=True)
class TriggerFireReceipt:
    """Stable correlation for one accepted attached trigger fire."""

    fire_id: str
    trigger_id: str
    session_id: str


def stable_trigger_ids(agent_id: str, key: str) -> tuple[str, str]:
    """Return the (scenario_id, trigger_id) a registration derives from its target and key."""
    digest = sha256(f'{agent_id}\0{key}'.encode()).hexdigest()[:32]
    return f'scn-sdk-{digest}', f'trg-sdk-{digest}'


_stable_ids = stable_trigger_ids


def _template_messages(messages: TriggerMessagesInput) -> list[str]:
    values = [messages] if isinstance(messages, str) else list(messages)
    if not values:
        message = 'trigger registration requires at least one message template'
        raise ValueError(message)
    return [_nonblank(value, field='message template') for value in values]


def _nonblank(value: str, *, field: str) -> str:
    resolved = value.strip()
    if not resolved:
        message = f'{field} must not be blank'
        raise ValueError(message)
    return resolved


def _canvas_parts(body: JsonObject) -> tuple[JsonObject, JsonObject]:
    envelope: object = body.get('scenario')
    if not isinstance(envelope, dict):
        message = 'server returned a malformed scenario response'
        raise MaivnSDKError(message)
    envelope_object = cast('JsonObject', envelope)
    scenario: object = envelope_object.get('scenario')
    trigger: object = envelope_object.get('trigger')
    if not isinstance(scenario, dict) or not isinstance(trigger, dict):
        message = 'server returned a malformed scenario canvas'
        raise MaivnSDKError(message)
    return cast('JsonObject', scenario), cast('JsonObject', trigger)


def _required_string(value: JsonObject, field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        message = f'scenario response omitted {field}'
        raise MaivnSDKError(message)
    return result


__all__ = ['Trigger', 'TriggerFireReceipt', 'TriggerInvocationBuilder']

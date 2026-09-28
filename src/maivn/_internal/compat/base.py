"""Shared public base class for v1-compatible authoring scopes."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, cast, overload

from maivn_contracts.scenarios import (
    ChainedEventSource,
    EmailToInvokeSource,
    ExternalConnectionEventSource,
    FireTriggerToolSource,
    FormConnectionSelector,
    GitHubConnectionSelector,
    LocalFileConnectionSelector,
    ManualSource,
    MemoryEventSource,
    ReplyConversationSource,
    ScheduleSource,
    SLATimerSource,
    StorageEventSource,
    TriggerSource,
    WebhookSource,
)

from maivn._internal.compat.scheduling import (
    AtSchedule,
    CronInvocationBuilder,
    CronSchedule,
    IntervalSchedule,
    JitterSpec,
)
from maivn._internal.triggers import TriggerInvocationBuilder

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timezone

    from maivn_contracts.scenarios.model import (
        ChainedEventType,
        DataPlaneEventOperation,
        MemoryRecordKind,
        SLAFireWhen,
        StorageObjectKind,
    )
    from typing_extensions import Unpack

    from maivn._internal.client import Client
    from maivn._internal.compat.scheduling import (
        MisfirePolicy,
        OverlapPolicy,
        Retry,
    )


class _TriggerScope(Protocol):
    """Authoring scope carrying the client used for trigger registration."""

    client: Client | None
    name: str
    agent_id: str | None
    version: int

    @property
    def _client(self) -> Client: ...


class _ScheduleOptions(TypedDict, total=False):
    """Keyword options forwarded to CronInvocationBuilder (mirrors its __init__)."""

    name: str | None
    jitter: JitterSpec | timedelta | float | None
    misfire: MisfirePolicy
    max_overlap: int
    overlap_policy: OverlapPolicy
    start_at: datetime | None
    end_at: datetime | None
    max_runs: int | None
    retry: Retry | None
    emit_events: bool


class BaseScope:
    """Common authoring scope methods shared by Agent and Swarm."""

    @overload
    def cron(
        self,
        expression: str,
        *,
        tz: str | timezone | None = None,
        mode: Literal['local'],
        **kwargs: Unpack[_ScheduleOptions],
    ) -> CronInvocationBuilder: ...

    @overload
    def cron(
        self,
        expression: str,
        *,
        tz: str | timezone | None = None,
        mode: Literal['remote'] = 'remote',
        **kwargs: Unpack[_ScheduleOptions],
    ) -> TriggerInvocationBuilder: ...

    def cron(
        self,
        expression: str,
        *,
        tz: str | timezone | None = None,
        mode: Literal['remote', 'local'] = 'remote',
        **kwargs: Unpack[_ScheduleOptions],
    ) -> TriggerInvocationBuilder | CronInvocationBuilder:
        """Build a scheduled invocation from a cron expression."""
        if mode == 'local':
            return CronInvocationBuilder(self, CronSchedule(expression, tz=tz), **kwargs)
        remote_expression = expression
        if isinstance(tz, str) and tz != 'UTC':
            _ = CronSchedule(expression, tz=tz)
            remote_expression = f'CRON_TZ={tz} {expression}'
        elif tz is not None and str(tz) != 'UTC':
            message = 'remote cron timezone must be UTC or an IANA timezone name'
            raise ValueError(message)
        jitter_seconds = _remote_jitter_seconds(kwargs.pop('jitter', None))
        name = kwargs.pop('name', None)
        _require_no_remote_schedule_options(kwargs)
        builder = self.on(
            ScheduleSource(
                kind='schedule',
                phase='phase_1',
                cron=remote_expression,
                jitter_seconds=jitter_seconds,
            )
        )
        return builder.named(name) if name is not None else builder

    @overload
    def interval(
        self,
        interval: timedelta,
        *,
        mode: Literal['local'],
        **kwargs: Unpack[_ScheduleOptions],
    ) -> CronInvocationBuilder: ...

    @overload
    def interval(
        self,
        interval: timedelta,
        *,
        mode: Literal['remote'] = 'remote',
        **kwargs: Unpack[_ScheduleOptions],
    ) -> TriggerInvocationBuilder: ...

    def interval(
        self,
        interval: timedelta,
        *,
        mode: Literal['remote', 'local'] = 'remote',
        **kwargs: Unpack[_ScheduleOptions],
    ) -> TriggerInvocationBuilder | CronInvocationBuilder:
        """Build a scheduled invocation from a fixed interval."""
        if mode == 'local':
            return CronInvocationBuilder(self, IntervalSchedule(interval), **kwargs)
        seconds = interval.total_seconds()
        if not seconds.is_integer():
            message = 'remote trigger intervals require whole seconds'
            raise ValueError(message)
        jitter_seconds = _remote_jitter_seconds(kwargs.pop('jitter', None))
        name = kwargs.pop('name', None)
        _require_no_remote_schedule_options(kwargs)
        builder = self.on(
            ScheduleSource(
                kind='schedule',
                phase='phase_1',
                interval_seconds=int(seconds),
                jitter_seconds=jitter_seconds,
            )
        )
        return builder.named(name) if name is not None else builder

    def at(
        self,
        when: datetime,
        **kwargs: Unpack[_ScheduleOptions],
    ) -> CronInvocationBuilder:
        """Build a one-shot scheduled invocation."""
        return CronInvocationBuilder(self, AtSchedule(when), **kwargs)

    @property
    def declared_triggers(self) -> list[TriggerInvocationBuilder]:
        """Triggers declared on this scope, in declaration order.

        Declaring a trigger (``on_email(...)``, ``on_event(...)``, ``cron(...)``
        and friends) records it here whether or not a client exists, so a tool
        that imports the app can list what it would register. Nothing is sent
        anywhere until a builder's ``invoke`` runs.
        """
        registry = self.__dict__.get('_declared_triggers')
        if registry is None:
            registry = []
            self.__dict__['_declared_triggers'] = registry
        return cast('list[TriggerInvocationBuilder]', registry)

    def on(self, source: TriggerSource) -> TriggerInvocationBuilder:
        """Declare a durable trigger from an authoritative low-level source.

        Registration itself happens on ``invoke`` and needs a client; the
        declaration does not, so an app can be described without credentials.
        The target defaults to this scope's agent ID (or name) and version.
        Use ``.target(...)`` only to override that owner identity.
        """
        scope = cast('_TriggerScope', self)
        builder = TriggerInvocationBuilder(
            scope.client,
            source,
            client_provider=lambda: scope._client,  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - owned scope/builder wiring.
        ).target(
            scope.agent_id if scope.agent_id is not None else scope.name,
            version=scope.version,
        )
        self.declared_triggers.append(builder)
        return builder

    def on_manual(self) -> TriggerInvocationBuilder:
        """Build a manually fired durable trigger."""
        return self.on(ManualSource(kind='manual', phase='phase_1'))

    def on_webhook(self, secret_ref: str) -> TriggerInvocationBuilder:
        """Build an inbound HMAC webhook trigger."""
        return self.on(WebhookSource(kind='webhook', phase='phase_1', secret_ref=secret_ref))

    def on_connection(
        self,
        connection_id: str,
        *,
        event_type: str,
        table: str | None = None,
    ) -> TriggerInvocationBuilder:
        """Build a normalized saved-connection event trigger."""
        return self.on(
            ExternalConnectionEventSource(
                kind='external_connection_event',
                phase='phase_2',
                connection_id=connection_id,
                event_type=event_type,
                table=table,
            )
        )

    def on_payment(
        self, connection_id: str, *, status: Literal['succeeded', 'failed'] = 'succeeded'
    ) -> TriggerInvocationBuilder:
        """Build a Stripe payment-intent trigger for successful or failed payment attempts."""
        if type(status) is not str or status not in ('succeeded', 'failed'):
            message = "status must be 'succeeded' or 'failed'"
            raise ValueError(message)
        return self.on_connection(
            connection_id,
            event_type='payment_intent.succeeded'
            if status == 'succeeded'
            else 'payment_intent.payment_failed',
        )

    def on_pull_request(
        self,
        connection_id: str,
        *,
        action: Literal['opened', 'reopened', 'synchronize', 'closed'] = 'opened',
        repository: str | None = None,
        branch: str | None = None,
        merged: bool | None = None,
    ) -> TriggerInvocationBuilder:
        """Declare a GitHub PR action; branch means its base (destination) branch."""
        if type(action) is not str or action not in ('opened', 'reopened', 'synchronize', 'closed'):
            message = "action must be 'opened', 'reopened', 'synchronize', or 'closed'"
            raise ValueError(message)
        return self._on_github(
            connection_id,
            f'pull_request.{action}',
            GitHubConnectionSelector(
                kind='github',
                repository=repository,
                branch=branch,
                merged=merged,
            ),
        )

    def on_issue(
        self,
        connection_id: str,
        *,
        action: Literal['opened', 'reopened', 'closed', 'labeled'] = 'opened',
        repository: str | None = None,
    ) -> TriggerInvocationBuilder:
        """Declare one supported GitHub issue action without changing repository permissions."""
        if type(action) is not str or action not in ('opened', 'reopened', 'closed', 'labeled'):
            message = "action must be 'opened', 'reopened', 'closed', or 'labeled'"
            raise ValueError(message)
        return self._on_github(
            connection_id,
            f'issues.{action}',
            GitHubConnectionSelector(
                kind='github',
                repository=repository,
            ),
        )

    def on_push(
        self,
        connection_id: str,
        *,
        repository: str | None = None,
        branch: str | None = None,
    ) -> TriggerInvocationBuilder:
        """Declare a GitHub push; a branch predicate excludes tag pushes with the same name."""
        return self._on_github(
            connection_id,
            'push',
            GitHubConnectionSelector(
                kind='github',
                repository=repository,
                branch=branch,
            ),
        )

    def on_workflow_run(
        self,
        connection_id: str,
        *,
        repository: str | None = None,
        branch: str | None = None,
        workflow: str | None = None,
        conclusion: Literal[
            'success',
            'failure',
            'cancelled',
            'timed_out',
            'neutral',
            'skipped',
            'action_required',
            'stale',
            'startup_failure',
        ]
        | None = None,
    ) -> TriggerInvocationBuilder:
        """Declare completed GitHub CI runs, optionally matching their exact conclusion."""
        return self._on_github(
            connection_id,
            'workflow_run.completed',
            GitHubConnectionSelector(
                kind='github',
                repository=repository,
                branch=branch,
                workflow=workflow,
                conclusion=conclusion,
            ),
        )

    def _on_github(
        self,
        connection_id: str,
        event_type: str,
        selector: GitHubConnectionSelector,
    ) -> TriggerInvocationBuilder:
        return self.on(
            ExternalConnectionEventSource(
                kind='external_connection_event',
                phase='phase_2',
                connection_id=connection_id,
                event_type=event_type,
                selector=selector,
            )
        )

    def on_invoice(
        self, connection_id: str, *, event: Literal['paid', 'payment_failed'] = 'paid'
    ) -> TriggerInvocationBuilder:
        """Build a Stripe invoice trigger; invoice status is distinct from a payment attempt."""
        if type(event) is not str or event not in ('paid', 'payment_failed'):
            message = "event must be 'paid' or 'payment_failed'"
            raise ValueError(message)
        return self.on_connection(connection_id, event_type=f'invoice.{event}')

    def on_subscription(
        self,
        connection_id: str,
        *,
        event: Literal['created', 'updated', 'deleted', 'trial_will_end'] = 'created',
    ) -> TriggerInvocationBuilder:
        """Build a Stripe subscription lifecycle trigger from a signed snapshot."""
        if type(event) is not str or event not in (
            'created',
            'updated',
            'deleted',
            'trial_will_end',
        ):
            message = "event must be 'created', 'updated', 'deleted', or 'trial_will_end'"
            raise ValueError(message)
        return self.on_connection(connection_id, event_type=f'customer.subscription.{event}')

    def on_file_change(
        self,
        watch_id: str,
        *,
        connection_id: str,
        operation: Literal['created', 'modified'] = 'created',
    ) -> TriggerInvocationBuilder:
        """Declare local file metadata events without opening or watching a filesystem."""
        if type(operation) is not str or operation not in ('created', 'modified'):
            message = "operation must be 'created' or 'modified'"
            raise ValueError(message)
        return self.on(
            ExternalConnectionEventSource(
                kind='external_connection_event',
                phase='phase_2',
                connection_id=connection_id,
                event_type=f'file.{operation}',
                selector=LocalFileConnectionSelector(kind='local_file', watch_id=watch_id),
            )
        )

    def on_form(self, form_id: str, *, connection_id: str) -> TriggerInvocationBuilder:
        """Build a signed form trigger whose form identity survives additional filters."""
        return self.on(
            ExternalConnectionEventSource(
                kind='external_connection_event',
                phase='phase_2',
                connection_id=connection_id,
                event_type='form.submitted',
                selector=FormConnectionSelector(kind='form', form_id=form_id),
            )
        )

    def on_email(
        self,
        connection_id: str,
        *,
        sender_allowlist: tuple[str, ...],
    ) -> TriggerInvocationBuilder:
        """Build a verified project drop-zone email trigger."""
        return self.on(
            EmailToInvokeSource(
                kind='email_to_invoke',
                phase='phase_2',
                connection_id=connection_id,
                sender_allowlist=sender_allowlist,
            )
        )

    def on_event(
        self,
        event_type: ChainedEventType,
        *,
        origin_trigger_id: str | None = None,
    ) -> TriggerInvocationBuilder:
        """Build a same-project canonical completion-event trigger."""
        return self.on(
            ChainedEventSource(
                kind='chained_event',
                phase='phase_3',
                event_type=event_type,
                origin_trigger_id=origin_trigger_id,
            )
        )

    def on_sla(
        self,
        condition: str,
        *,
        fire_when: SLAFireWhen,
        deadline_seconds: int,
    ) -> TriggerInvocationBuilder:
        """Build a durable named-condition SLA timer trigger."""
        return self.on(
            SLATimerSource(
                kind='sla_timer',
                phase='phase_4',
                condition=condition,
                fire_when=fire_when,
                deadline_seconds=deadline_seconds,
            )
        )

    def on_record_change(
        self,
        table: str | None = None,
        *,
        connection_id: str | None = None,
        operation: str,
        record_kind: MemoryRecordKind | None = None,
    ) -> TriggerInvocationBuilder:
        """Watch a table in your database, or retain a legacy memory declaration.

        Database callers provide ``table='public.orders'``, ``connection_id``
        and ``operation='insert'|'update'|'delete'``. Existing positional or
        keyword memory record kinds keep working; prefer ``on_memory_change``
        for new platform-memory declarations.
        """
        if connection_id is not None:
            if record_kind is not None or table is None:
                message = 'database changes require a table and cannot name a memory record_kind'
                raise ValueError(message)
            if operation not in {'insert', 'update', 'delete'}:
                message = 'database operation must be insert, update or delete'
                raise ValueError(message)
            return self.on_connection(connection_id, event_type=f'row.{operation}', table=table)
        if table is not None and record_kind is not None:
            message = 'provide either a legacy record_kind or a database table, not both'
            raise ValueError(message)
        if operation in {'insert', 'update', 'delete'}:
            message = 'database record changes require connection_id'
            raise ValueError(message)
        return self.on_memory_change(
            cast('MemoryRecordKind', record_kind if record_kind is not None else table),
            operation=cast('DataPlaneEventOperation', operation),
        )

    def on_memory_change(
        self,
        record_kind: MemoryRecordKind,
        *,
        operation: DataPlaneEventOperation,
    ) -> TriggerInvocationBuilder:
        """Build a durable memory-record change trigger."""
        return self.on(
            MemoryEventSource(
                kind='memory_event',
                phase='phase_3',
                record_kind=record_kind,
                operation=operation,
            )
        )

    def on_storage(
        self,
        object_kind: StorageObjectKind,
        *,
        operation: DataPlaneEventOperation,
    ) -> TriggerInvocationBuilder:
        """Build a private memory-resource Storage object trigger."""
        return self.on(
            StorageEventSource(
                kind='storage_event',
                phase='phase_3',
                bucket='memory-resources',
                object_kind=object_kind,
                operation=operation,
            )
        )

    def on_reply(self) -> TriggerInvocationBuilder:
        """Build a database-owned conversation reply trigger."""
        return self.on(ReplyConversationSource(kind='reply_conversation', phase='phase_4'))

    def on_tool_fire(self) -> TriggerInvocationBuilder:
        """Build a server-resident fire-trigger tool source."""
        return self.on(FireTriggerToolSource(kind='fire_trigger_tool', phase='phase_4'))

    def close(self) -> None:
        """Release scope resources when available."""


def _remote_jitter_seconds(value: JitterSpec | timedelta | float | None) -> int:
    if value is None:
        return 60
    if isinstance(value, JitterSpec):
        # The remote wire carries one non-negative amplitude; a JitterSpec's
        # amplitude is its largest bound (symmetric specs have min == -max).
        value = max(abs(value.min.total_seconds()), abs(value.max.total_seconds()))
    if isinstance(value, timedelta):
        value = value.total_seconds()
    if value < 0 or not float(value).is_integer():
        message = 'remote schedules require jitter as non-negative whole seconds'
        raise ValueError(message)
    return int(value)


def _require_no_remote_schedule_options(options: Mapping[str, object]) -> None:
    supplied = sorted(name for name, value in options.items() if value is not None)
    if supplied:
        message = f'unsupported remote schedule options: {", ".join(supplied)}'
        raise ValueError(message)


__all__ = ['BaseScope']

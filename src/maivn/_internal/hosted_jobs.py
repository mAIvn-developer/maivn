"""Explicit detached jobs; closing this SDK never cancels their server work."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

from maivn_contracts.jobs import HostedJobOptions, HostedJobStatus

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.wire import (
    invocation_options,
    message_payloads,
    optional_run_options,
    run_config_payload,
    skill_selection_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maivn._internal.models import RunOptions
    from maivn._internal.transport.http import HttpJsonClient
    from maivn.messages import SdkMessagesInput


class HostedJobsClient:
    """No SDK-local tools, automatic resume, provider retries or private-value restoration."""

    def __init__(
        self,
        http: Callable[[], HttpJsonClient],
        options: Callable[[RunOptions | None], RunOptions],
        api_key: Callable[[], str],
        default_thread_id: Callable[[], str | None] | None = None,
    ) -> None:
        """Bind the existing HTTP transport and caller run options."""
        self._http, self._options = http, options
        self._default_thread_id = default_thread_id
        self._api_key = api_key

    def start(
        self,
        messages: SdkMessagesInput,
        *,
        job: HostedJobOptions,
        options: RunOptions,
        private_data: Mapping[object, object] | None = None,
    ) -> HostedJobStatus:
        """Start immediately with all bounds and an exact options.max_expense_usd."""
        return run_blocking(
            lambda: self.astart(messages, job=job, options=options, private_data=private_data)
        )

    async def astart(
        self,
        messages: SdkMessagesInput,
        *,
        job: HostedJobOptions,
        options: RunOptions,
        private_data: Mapping[object, object] | None = None,
    ) -> HostedJobStatus:
        """Start a bounded server job without transferring lifetime to this client."""
        if not options.thread_id:
            configured = self._default_thread_id() if self._default_thread_id is not None else None
            thread_id = (
                configured
                or 'thr-job-'
                + hmac.new(
                    self._api_key().encode(),
                    b'maivn-hosted-job-thread-v1\0' + job.idempotency_key.encode(),
                    hashlib.sha256,
                ).hexdigest()
            )
            options = options.model_copy(update={'thread_id': thread_id})
        resolved = self._options(options)
        if resolved.max_expense_usd is None:
            message = 'hosted jobs require explicit max_expense_usd'
            raise ValueError(message)
        if resolved.session_id is not None:
            message = 'hosted job session identities are server assigned'
            raise ValueError(message)
        invoke: dict[str, Any] = {
            'messages': message_payloads(messages),
            'run_config': run_config_payload(resolved),
            **optional_run_options(resolved),
            **skill_selection_payload(resolved),
            **invocation_options((), private_data=private_data, force_final_tool=False),
        }
        payload = {**job.model_dump(mode='json'), 'invoke': invoke}
        return HostedJobStatus.model_validate(await self._http().post('/v1/jobs', payload))

    def get(self, session_id: str) -> HostedJobStatus:
        """Read durable shielded progress/output after disconnect or owner loss."""
        return run_blocking(lambda: self.aget(session_id))

    async def aget(self, session_id: str) -> HostedJobStatus:
        """Read the durable shielded status without a local run handle."""
        return HostedJobStatus.model_validate(await self._http().get(self._path(session_id)))

    def cancel(self, session_id: str) -> HostedJobStatus:
        """Request durable cancellation; it cannot undo in-flight external effects."""
        return run_blocking(lambda: self.acancel(session_id))

    async def acancel(self, session_id: str) -> HostedJobStatus:
        """Commit cancellation through the owning server authority."""
        return HostedJobStatus.model_validate(
            await self._http().post(self._path(session_id) + '/cancel', {})
        )

    @staticmethod
    def _path(session_id: str) -> str:
        # Server job identities are ASCII UUID-derived segments, not arbitrary paths.
        if (
            not session_id.startswith('job-')
            or len(session_id) != len('job-') + 32
            or any(c not in '0123456789abcdef' for c in session_id[4:])
        ):
            message = 'invalid hosted job session identity'
            raise ValueError(message)
        return '/v1/jobs/' + session_id

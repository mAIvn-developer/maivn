"""Contract rejections remain actionable when clients display their exception."""

import asyncio

import httpx

from maivn import MaivnHTTPError
from maivn._internal.transport.http import HttpJsonClient

_UNPROCESSABLE = 422
_RETRY_AFTER_SECONDS = 3


def test_validation_error_displays_field_and_reason_without_raw_input() -> None:
    """Plain exception displays identify the rejected field without dumping input."""
    field = {'field': 'model', 'message': 'Value error, unknown model: test-unavailable-model'}
    error = MaivnHTTPError(
        status_code=_UNPROCESSABLE,
        reason='Request validation failed.',
        code='validation_failed',
        fields=[field],
    )
    assert 'model: Value error, unknown model: test-unavailable-model' in str(error)
    assert error.fields == [field]


def test_other_error_details_are_not_implicitly_disclosed() -> None:
    """Unstructured payloads are retained for explicit callers, not printed."""
    error = MaivnHTTPError(
        status_code=500,
        reason='Internal Server Error',
        detail={'credential': 'synthetic-sensitive-input'},
    )
    assert str(error) == 'mAIvn API returned 500: Internal Server Error'


def test_every_envelope_member_reaches_the_exception() -> None:
    """The transport maps detail, code, fields, retry_after and context exactly."""
    body = {
        'detail': 'Request validation failed.',
        'code': 'validation_failed',
        'fields': [{'field': 'messages.0.content', 'message': 'Field required'}],
        'retry_after': _RETRY_AFTER_SECONDS,
        'context': {'hint': 'value'},
    }

    async def scenario() -> MaivnHTTPError:
        client = HttpJsonClient(
            base_url='http://api.test',
            api_key='key',
            timeout_seconds=5.0,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(_UNPROCESSABLE, json=body),
            ),
        )
        try:
            await client.get('/v1/x')
        except MaivnHTTPError as error:
            return error
        raise AssertionError

    error = asyncio.run(scenario())
    assert error.status_code == _UNPROCESSABLE
    assert error.reason == 'Request validation failed.'
    assert error.code == 'validation_failed'
    assert error.fields == [{'field': 'messages.0.content', 'message': 'Field required'}]
    assert error.retry_after == _RETRY_AFTER_SECONDS
    assert error.detail == {'hint': 'value'}
    assert 'messages.0.content: Field required' in str(error)


def test_a_body_that_is_not_the_envelope_falls_back_to_the_status_phrase() -> None:
    """A proxy page or empty body still raises a typed error, never a parse failure."""

    async def scenario() -> MaivnHTTPError:
        client = HttpJsonClient(
            base_url='http://api.test',
            api_key='key',
            timeout_seconds=5.0,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(502, content=b'<html>bad gateway</html>'),
            ),
        )
        try:
            await client.get('/v1/x')
        except MaivnHTTPError as error:
            return error
        raise AssertionError

    error = asyncio.run(scenario())
    assert error.reason == 'Bad Gateway'
    assert error.code is None

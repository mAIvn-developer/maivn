"""The SDK forwards exact ceilings and omits them for existing callers."""

import pytest
from pydantic import ValidationError

from maivn import RunOptions
from maivn._internal.wire import run_config_payload


def test_request_ceiling_is_forwarded_without_rounding() -> None:
    """Verify request ceiling is forwarded without rounding."""
    assert (
        run_config_payload(RunOptions(max_expense_usd='1.000000000001'))['max_expense_usd']
        == '1.000000000001'
    )


def test_absent_request_ceiling_does_not_change_wire() -> None:
    """Verify absent request ceiling does not change wire."""
    assert 'max_expense_usd' not in run_config_payload(RunOptions())


@pytest.mark.parametrize('value', [True, 1, 1.5, '0', '-1', 'NaN', '1e1', '0.0000000000001'])
def test_sdk_rejects_nonexact_or_invalid_ceiling(value: object) -> None:
    """Verify sdk rejects nonexact or invalid ceiling."""
    with pytest.raises(ValidationError):
        RunOptions.model_validate({'max_expense_usd': value})

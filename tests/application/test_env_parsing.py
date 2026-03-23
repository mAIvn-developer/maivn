from __future__ import annotations

import pytest

from maivn._internal.utils.env_parsing import (
    coerce_bool_env,
    coerce_float_env,
    coerce_int_env,
    read_bool_env,
    read_int_env,
    read_str_env,
)


def test_coerce_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "MAIVN_TEST_COERCE"

    monkeypatch.setenv(key, "true")
    assert coerce_bool_env(key) is True

    monkeypatch.setenv(key, "false")
    assert coerce_bool_env(key) is False

    monkeypatch.setenv(key, "maybe")
    assert coerce_bool_env(key) == "maybe"

    monkeypatch.setenv(key, "42")
    assert coerce_int_env(key) == 42

    monkeypatch.setenv(key, "not-an-int")
    assert coerce_int_env(key) == "not-an-int"

    monkeypatch.setenv(key, "3.5")
    assert coerce_float_env(key) == 3.5

    monkeypatch.setenv(key, "not-a-float")
    assert coerce_float_env(key) == "not-a-float"


def test_read_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "MAIVN_TEST_READ"
    monkeypatch.delenv(key, raising=False)

    assert read_bool_env(key, default=True) is True
    assert read_int_env(key, default=7) == 7
    assert read_str_env(key, default="fallback") == "fallback"

    monkeypatch.setenv(key, "")
    assert read_bool_env(key, default=False) is False
    assert read_int_env(key, default=3) == 3
    assert read_str_env(key, default="blank") == "blank"

    monkeypatch.setenv(key, "off")
    assert read_bool_env(key, default=True) is False

    monkeypatch.setenv(key, "123")
    assert read_int_env(key, default=0) == 123

    monkeypatch.setenv(key, "  spaced  ")
    assert read_str_env(key, default="") == "spaced"

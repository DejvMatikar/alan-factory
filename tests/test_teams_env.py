from __future__ import annotations

import pytest

from alan.teams._env import float_env, float_or_none, int_or_none, positive_int_env


def test_int_or_none_accepts_int() -> None:
    assert int_or_none(5) == 5


def test_int_or_none_accepts_float() -> None:
    assert int_or_none(5.9) == 5


def test_int_or_none_rejects_bool() -> None:
    assert int_or_none(True) is None
    assert int_or_none(False) is None


def test_int_or_none_rejects_string() -> None:
    assert int_or_none("5") is None


def test_int_or_none_rejects_none() -> None:
    assert int_or_none(None) is None


def test_float_or_none_accepts_int() -> None:
    assert float_or_none(5) == 5.0


def test_float_or_none_accepts_float() -> None:
    assert float_or_none(1.5) == 1.5


def test_float_or_none_rejects_bool() -> None:
    assert float_or_none(True) is None
    assert float_or_none(False) is None


def test_float_or_none_rejects_string() -> None:
    assert float_or_none("1.5") is None


def test_float_env_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_TEST_FLOAT_ENV", raising=False)
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 3.5


def test_float_env_blank_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "   ")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 3.5


def test_float_env_valid_value_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "12.5")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 12.5


def test_float_env_non_numeric_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "not-a-number")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 3.5


def test_float_env_negative_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "-1.0")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 3.5


def test_float_env_non_finite_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "inf")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 3.5


def test_float_env_zero_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_FLOAT_ENV", "0")
    assert float_env("ALAN_TEST_FLOAT_ENV", 3.5) == 0.0


def test_positive_int_env_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_TEST_INT_ENV", raising=False)
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 7


def test_positive_int_env_blank_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_INT_ENV", "  ")
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 7


def test_positive_int_env_valid_value_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_INT_ENV", "42")
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 42


def test_positive_int_env_non_numeric_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_INT_ENV", "nope")
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 7


def test_positive_int_env_zero_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_INT_ENV", "0")
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 7


def test_positive_int_env_negative_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_TEST_INT_ENV", "-3")
    assert positive_int_env("ALAN_TEST_INT_ENV", 7) == 7

from __future__ import annotations

import pytest

from alan import claude_cli


def test_idle_timeout_seconds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(claude_cli.IDLE_TIMEOUT_ENV, raising=False)
    assert claude_cli.idle_timeout_seconds() == claude_cli._DEFAULT_IDLE_TIMEOUT_SECONDS  # noqa: SLF001


def test_idle_timeout_seconds_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude_cli.IDLE_TIMEOUT_ENV, "45")
    assert claude_cli.idle_timeout_seconds() == 45.0


def test_abs_timeout_seconds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(claude_cli.ABS_TIMEOUT_ENV, raising=False)
    assert claude_cli.abs_timeout_seconds() == claude_cli._DEFAULT_ABS_TIMEOUT_SECONDS  # noqa: SLF001


def test_abs_timeout_seconds_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude_cli.ABS_TIMEOUT_ENV, "999")
    assert claude_cli.abs_timeout_seconds() == 999.0

from __future__ import annotations

import pytest

from alan import claude_cli
from alan.fleet_load import (
    FLEET_ASYNC_STALL_TIMEOUT_MS,
    FLEET_BASH_DEFAULT_TIMEOUT_MS,
    FLEET_BASH_MAX_OUTPUT_LENGTH,
    FLEET_BASH_MAX_TIMEOUT_MS,
)

_FLEET_ENV_KEYS = (
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_TIMEOUT_MS",
    "BASH_MAX_OUTPUT_LENGTH",
    "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS",
)


def test_worker_env_fleet_true_carries_all_fleet_bash_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in _FLEET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env = claude_cli._worker_env(fleet=True)  # pyright: ignore[reportPrivateUsage]
    assert env["BASH_DEFAULT_TIMEOUT_MS"] == str(FLEET_BASH_DEFAULT_TIMEOUT_MS)
    assert env["BASH_MAX_TIMEOUT_MS"] == str(FLEET_BASH_MAX_TIMEOUT_MS)
    assert env["BASH_MAX_OUTPUT_LENGTH"] == str(FLEET_BASH_MAX_OUTPUT_LENGTH)
    assert env["CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS"] == str(FLEET_ASYNC_STALL_TIMEOUT_MS)


def test_worker_env_fleet_false_carries_none_of_the_fleet_bash_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in _FLEET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env = claude_cli._worker_env(fleet=False)  # pyright: ignore[reportPrivateUsage]
    for key in _FLEET_ENV_KEYS:
        assert key not in env

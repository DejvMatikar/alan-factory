from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import alan.server as server
from alan import driver_loop_runner
from alan.events import PubSub
from alan.routes import events as events_mod
from alan.routes import infra as infra_mod

_REPO_ROOT = Path(__file__).resolve().parent.parent

_DRIVER_LOOP_LAYERS: tuple[str, ...] = (
    "driver_loop_runner",
    "driver_loop_status",
    "driver_loop_schedule",
    "driver_loop_runs",
    "driver_loop_pause",
    "driver_loop_cmd",
    "driver_loop_registry",
)

_OPERATOR_LAYERS: tuple[str, ...] = (
    "operator_checkout",
    "operator_pty",
    "operator_ticket",
    "operator_prompt",
    "operator_tmux",
)

_EXPECTED_BLOCK: tuple[str, ...] = (
    *_DRIVER_LOOP_LAYERS[:5],
    *_OPERATOR_LAYERS,
    *_DRIVER_LOOP_LAYERS[5:],
)


def _layers() -> list[str]:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for contract in data["tool"]["importlinter"]["contracts"]:
        if contract.get("name") == "Layered architecture":
            return list(contract["layers"])
    raise AssertionError("Layered architecture contract not found in pyproject.toml")


def test_driver_loop_layers_sit_between_project_and_queue() -> None:
    layers = _layers()
    project_idx = layers.index("project")
    queue_idx = layers.index("queue")
    block = layers[project_idx + 1 : queue_idx]
    assert block == list(_EXPECTED_BLOCK)


def test_operator_layers_sit_directly_above_driver_loop_cmd() -> None:
    layers = _layers()
    cmd_idx = layers.index("driver_loop_cmd")
    assert layers[cmd_idx - len(_OPERATOR_LAYERS) : cmd_idx] == list(_OPERATOR_LAYERS)


def test_operator_session_sits_directly_above_workspace() -> None:
    layers = _layers()
    assert layers[layers.index("workspace") - 1] == "operator_session"


def test_driver_loop_layers_declared_exactly_once() -> None:
    layers = _layers()
    for name in _DRIVER_LOOP_LAYERS:
        assert layers.count(name) == 1


def test_driver_loop_changed_is_a_health_kind() -> None:
    assert "driver_loop_changed" in events_mod._HEALTH_KINDS  # noqa: SLF001


def test_publish_driver_loop_changed_emits_the_expected_event() -> None:
    bus = PubSub()
    events: list[Any] = []
    bus.subscribe_all(events.append)
    infra_mod._publish_driver_loop_changed(bus)  # noqa: SLF001
    assert events[0].kind == "driver_loop_changed"


def test_infra_lifespan_calls_driver_loop_runner_start_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_start(**kwargs: Any) -> None:
        calls.append("start")
        assert "queue" in kwargs
        assert callable(kwargs.get("on_change"))

    def _fake_stop() -> None:
        calls.append("stop")

    monkeypatch.setattr(driver_loop_runner, "start", _fake_start)
    monkeypatch.setattr(driver_loop_runner, "stop", _fake_stop)
    with TestClient(server.app):
        assert calls == ["start"]
    assert calls == ["start", "stop"]

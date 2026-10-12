from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from alan import agent_key, driver_loop_registry, driver_loop_runs
from alan.agent_key import AgentKey
from alan.engine_config import EngineConfig
from tests import agent_catalogue_parity as parity
from tests.agent_catalogue_parity import Driver, ParityEnv
from tests.agent_catalogue_standalone_drivers import STANDALONE_DRIVERS

_DRIVER_LOOP_ENTRY_YAML = (
    "name: parity-loop\ncommand: /loop\nargs: --parity\ncwd: {cwd}\nself_paced: true\n"
    "model: opus\n"
)

_DRIVER_LOOP_DRIVER = "driver loops"


def write_driver_loop_entry(directory: Path, cwd: Path) -> None:
    (directory / "parity-loop.yaml").write_text(
        _DRIVER_LOOP_ENTRY_YAML.format(cwd=cwd), encoding="utf-8"
    )


def use_driver_loop_registry(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setattr(driver_loop_registry, "config_dir", lambda: directory)


def _spawn_every_driver_loop(env: ParityEnv) -> None:
    entries, _ = driver_loop_registry.load_registry()
    process = SimpleNamespace(pid=4242)
    fake_subprocess = SimpleNamespace(
        Popen=lambda *_a, **_k: process, DEVNULL=subprocess.DEVNULL
    )
    env.monkeypatch.setattr(driver_loop_runs, "subprocess", fake_subprocess)
    env.monkeypatch.setattr(os, "getpgid", lambda _pid: 4242)
    for entry in entries:
        driver_loop_runs.spawn_run(entry)


def _driver_loop_keys(_cfg: EngineConfig) -> Sequence[AgentKey]:
    entries, _ = driver_loop_registry.load_registry()
    return [agent_key.driver_loop(entry.name) for entry in entries]


def register() -> None:
    for driver in STANDALONE_DRIVERS:
        if driver.name != _DRIVER_LOOP_DRIVER:
            parity.register(driver)
    parity.register(Driver(_DRIVER_LOOP_DRIVER, _driver_loop_keys, _spawn_every_driver_loop))

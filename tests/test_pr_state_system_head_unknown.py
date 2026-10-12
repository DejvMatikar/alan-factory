from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_state_paths


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _write_raw(payload: dict[str, Any]) -> None:
    path = _pr_state_paths.system_head_path(42, DEFAULT_FORGE_TARGET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _on_disk() -> dict[str, Any]:
    path = _pr_state_paths.system_head_path(42, DEFAULT_FORGE_TARGET)
    on_disk: dict[str, Any] = json.loads(path.read_text())
    return on_disk


def test_an_unknown_head_marker_round_trips_with_an_absent_head() -> None:
    before = time.time()
    _pr_state_paths.save_system_head(42, None, "proactive_rebase", DEFAULT_FORGE_TARGET)
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert marker.head_sha is None
    assert (marker.rung, marker.forwarded) == ("proactive_rebase", False)
    assert marker.written_at is not None and marker.written_at >= before


def test_an_unknown_head_is_stored_as_null_never_an_empty_string() -> None:
    _pr_state_paths.save_system_head(42, None, "dirty", DEFAULT_FORGE_TARGET)
    assert _on_disk()["head_sha"] is None


def test_a_known_head_marker_is_stamped_with_its_write_time() -> None:
    before = time.time()
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None and marker.head_sha == "sha-1"
    assert marker.written_at is not None and marker.written_at >= before


def test_forwarding_an_unknown_head_marker_keeps_its_absent_head_and_write_time() -> None:
    _pr_state_paths.save_system_head(42, None, "dirty", DEFAULT_FORGE_TARGET)
    written = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.mark_system_head_forwarded(42, DEFAULT_FORGE_TARGET)
    forwarded = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert written is not None and forwarded is not None
    assert forwarded.head_sha is None
    assert forwarded.forwarded is True
    assert forwarded.written_at == written.written_at


def test_a_marker_written_before_this_field_loads_with_no_write_time() -> None:
    _write_raw({"head_sha": "sha-1", "rung": "dirty", "forwarded": False})
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == ("sha-1", "dirty", False)
    assert marker.written_at is None


def test_a_missing_head_key_with_a_write_time_loads_as_an_unknown_head() -> None:
    _write_raw({"rung": "dirty", "written_at": 1000.0})
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.written_at) == (None, 1000.0)


@pytest.mark.parametrize(
    "payload",
    [
        {"head_sha": None, "rung": "dirty", "forwarded": False},
        {"head_sha": None, "rung": "dirty", "written_at": True},
        {"head_sha": None, "rung": "dirty", "written_at": "1000"},
        {"head_sha": None, "rung": "dirty", "written_at": float("inf")},
        {"head_sha": None, "rung": "dirty", "written_at": float("nan")},
        {"head_sha": None, "rung": "dirty", "written_at": 10**400},
        {"head_sha": "", "rung": "dirty", "written_at": 1000.0},
        {"head_sha": 7, "rung": "dirty", "written_at": 1000.0},
        {"head_sha": None, "rung": "", "written_at": 1000.0},
        {"head_sha": None, "written_at": 1000.0},
        {"head_sha": None, "rung": "dirty", "forwarded": "yes", "written_at": 1000.0},
    ],
)
def test_an_unknown_head_marker_still_fails_open_when_malformed(payload: dict[str, Any]) -> None:
    _write_raw(payload)
    assert _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET) is None


@pytest.mark.parametrize(
    "written_at", [True, "1000", None, [1], float("inf"), float("-inf"), float("nan"), 10**400]
)
def test_a_known_head_marker_ignores_an_unusable_write_time(written_at: object) -> None:
    _write_raw({"head_sha": "sha-1", "rung": "dirty", "written_at": written_at})
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None and marker.written_at is None


def test_an_integer_write_time_is_read_as_a_float() -> None:
    _write_raw({"head_sha": None, "rung": "dirty", "written_at": 1000})
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None and marker.written_at == 1000.0
    assert isinstance(marker.written_at, float)

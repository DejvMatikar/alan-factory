from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_state_paths
from tests._pr_branch_support import _WIDGET_FORGE


def test_mark_thread_replied_then_is_thread_replied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.is_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET) is False
    _pr_state_paths.mark_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.is_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET) is True


def test_mark_thread_replied_second_call_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.mark_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    on_disk_first = _pr_state_paths.replied_threads_path(42, DEFAULT_FORGE_TARGET).read_text()
    _pr_state_paths.mark_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    on_disk_second = _pr_state_paths.replied_threads_path(42, DEFAULT_FORGE_TARGET).read_text()
    assert on_disk_first == on_disk_second
    assert json.loads(on_disk_second) == ["PRTI_1"]


def test_mark_thread_replied_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.mark_thread_replied(42, "PRTI_1", _WIDGET_FORGE)
    assert _pr_state_paths.is_thread_replied(42, "PRTI_1", DEFAULT_FORGE_TARGET) is False
    assert _pr_state_paths.is_thread_replied(42, "PRTI_1", _WIDGET_FORGE) is True


def test_mark_thread_resolved_then_is_thread_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.is_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET) is False
    _pr_state_paths.mark_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.is_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET) is True


def test_mark_thread_resolved_second_call_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.mark_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    on_disk_first = _pr_state_paths.resolved_threads_path(42, DEFAULT_FORGE_TARGET).read_text()
    _pr_state_paths.mark_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET)
    on_disk_second = _pr_state_paths.resolved_threads_path(42, DEFAULT_FORGE_TARGET).read_text()
    assert on_disk_first == on_disk_second
    assert json.loads(on_disk_second) == ["PRTI_1"]


def test_mark_thread_resolved_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.mark_thread_resolved(42, "PRTI_1", _WIDGET_FORGE)
    assert _pr_state_paths.is_thread_resolved(42, "PRTI_1", DEFAULT_FORGE_TARGET) is False
    assert _pr_state_paths.is_thread_resolved(42, "PRTI_1", _WIDGET_FORGE) is True

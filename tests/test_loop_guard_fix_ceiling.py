from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.loop_guard import fix_cycle_ceiling_reached, fix_cycle_park_reason
from alan.teams import _pr_state_paths


def test_fix_cycle_ceiling_not_reached_below_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(5):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is False


def test_fix_cycle_ceiling_reached_at_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is True


def test_fix_cycle_ceiling_reached_false_with_no_dispatches_yet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is False


def test_fix_cycle_ceiling_override_lifts_a_reached_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is True
    _pr_state_paths.grant_fix_cycle_override(
        42, "fix_cycle_ceiling_abc12345", DEFAULT_FORGE_TARGET
    )
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is False


def test_fix_cycle_ceiling_override_exhausted_after_three_more_cycles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(
        42, "fix_cycle_ceiling_abc12345", DEFAULT_FORGE_TARGET
    )
    for _ in range(3):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    assert fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET) is True


def test_fix_cycle_park_reason_names_the_count() -> None:
    reason = fix_cycle_park_reason(6, "coverage gap on new code")
    assert "6" in reason
    assert "coverage gap on new code" in reason

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_state_paths
from tests._pr_branch_support import _WIDGET_FORGE


def test_capability_waiver_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.capability_waiver_path(42, DEFAULT_FORGE_TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_capability_waivers.json"
    )


def test_capability_boundary_waiver_recorded_false_when_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        42, "q1", DEFAULT_FORGE_TARGET
    ) is False


def test_record_capability_boundary_waiver_first_grant_returns_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.record_capability_boundary_waiver(
        42, "q1", DEFAULT_FORGE_TARGET
    ) is True
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        42, "q1", DEFAULT_FORGE_TARGET
    ) is True


def test_record_capability_boundary_waiver_replay_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.record_capability_boundary_waiver(42, "q1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.record_capability_boundary_waiver(
        42, "q1", DEFAULT_FORGE_TARGET
    ) is False


def test_record_capability_boundary_waiver_distinct_ids_accumulate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.record_capability_boundary_waiver(42, "q1", DEFAULT_FORGE_TARGET)
    _pr_state_paths.record_capability_boundary_waiver(42, "q2", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        42, "q1", DEFAULT_FORGE_TARGET
    ) is True
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        42, "q2", DEFAULT_FORGE_TARGET
    ) is True


def test_record_capability_boundary_waiver_writes_sorted_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.record_capability_boundary_waiver(42, "qb", DEFAULT_FORGE_TARGET)
    _pr_state_paths.record_capability_boundary_waiver(42, "qa", DEFAULT_FORGE_TARGET)
    path = _pr_state_paths.capability_waiver_path(42, DEFAULT_FORGE_TARGET)
    on_disk = json.loads(path.read_text())
    assert on_disk == ["qa", "qb"]


def test_capability_boundary_waiver_recorded_false_for_unrecorded_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.record_capability_boundary_waiver(42, "q1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        42, "q2", DEFAULT_FORGE_TARGET
    ) is False


def test_capability_waiver_state_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.record_capability_boundary_waiver(5, "q1", _WIDGET_FORGE)
    assert _pr_state_paths.capability_boundary_waiver_recorded(5, "q1", _WIDGET_FORGE) is True
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        5, "q1", DEFAULT_FORGE_TARGET
    ) is False


def test_capability_waiver_leaves_fix_cycle_state_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(42, "fix-question", DEFAULT_FORGE_TARGET)
    _pr_state_paths.record_capability_boundary_waiver(
        42, "capability-question", DEFAULT_FORGE_TARGET
    )
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.granted_question_ids == ("fix-question",)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT
    assert state.count == 1


def test_load_capability_waiver_corrupt_json_defaults_to_not_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.capability_waiver_path(5, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        5, "q1", DEFAULT_FORGE_TARGET
    ) is False


@pytest.mark.parametrize("payload", ['"not-a-list"', "42", json.dumps([1, 2, 3])])
def test_load_capability_waiver_malformed_payloads_default_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.capability_waiver_path(5, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload)
    assert _pr_state_paths.capability_boundary_waiver_recorded(
        5, "q1", DEFAULT_FORGE_TARGET
    ) is False


def test_record_capability_boundary_waiver_concurrent_distinct_grants_all_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    threads = [
        threading.Thread(
            target=_pr_state_paths.record_capability_boundary_waiver,
            args=(42, f"q{i}", DEFAULT_FORGE_TARGET),
        )
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    path = _pr_state_paths.capability_waiver_path(42, DEFAULT_FORGE_TARGET)
    on_disk = json.loads(path.read_text())
    assert len(on_disk) == 10

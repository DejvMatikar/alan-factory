from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_state_paths
from tests._pr_branch_support import _WIDGET_FORGE


def test_load_fix_cycle_state_missing_file_returns_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state == _pr_state_paths.FixCycleState(count=0, allowance=0)


def test_fix_cycle_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_fix_cycles.json"
    )


def test_bump_fix_cycle_count_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET) == 1
    assert _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET) == 2
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state == _pr_state_paths.FixCycleState(count=2, allowance=0)
    on_disk = json.loads(_pr_state_paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET).read_text())
    assert on_disk == {"count": 2, "allowance": 0}


def test_bump_fix_cycle_count_never_reset_by_a_fresh_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET).count == 6


def test_fix_cycle_budget_defaults_to_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    budget = _pr_state_paths.fix_cycle_budget(42, DEFAULT_FORGE_TARGET)
    assert budget == _pr_state_paths.FIX_CYCLE_CEILING


def test_fix_cycle_budget_adds_allowance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.fix_cycle_budget(42, DEFAULT_FORGE_TARGET) == (
        _pr_state_paths.FIX_CYCLE_CEILING + _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT
    )


def test_grant_fix_cycle_override_first_grant_returns_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET) is True
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT
    assert state.granted_question_ids == ("q1",)


def test_grant_fix_cycle_override_replay_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET) is False
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT


def test_grant_fix_cycle_override_distinct_question_ids_accumulate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(42, "q2", DEFAULT_FORGE_TARGET)
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT * 2
    assert state.granted_question_ids == ("q1", "q2")


def test_grant_fix_cycle_override_preserves_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET)
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.count == 2


def test_bump_fix_cycle_count_omits_granted_question_ids_key_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    on_disk = json.loads(_pr_state_paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET).read_text())
    assert "granted_question_ids" not in on_disk


def test_grant_fix_cycle_override_writes_sorted_granted_question_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.grant_fix_cycle_override(42, "qb", DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(42, "qa", DEFAULT_FORGE_TARGET)
    on_disk = json.loads(_pr_state_paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET).read_text())
    assert on_disk["granted_question_ids"] == ["qa", "qb"]


def test_load_fix_cycle_state_corrupt_json_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "DejvMatikar" / "alan").mkdir(parents=True)
    (tmp_path / "DejvMatikar" / "alan" / "pr_5_fix_cycles.json").write_text("not json{")
    state = _pr_state_paths.load_fix_cycle_state(5, DEFAULT_FORGE_TARGET)
    assert state == _pr_state_paths.FixCycleState(count=0, allowance=0)


@pytest.mark.parametrize(
    "payload",
    [
        '"not-a-dict"',
        "42",
        "[1, 2, 3]",
        json.dumps({"count": -1, "allowance": 0}),
        json.dumps({"count": True, "allowance": 0}),
        json.dumps({"count": 0, "allowance": -1}),
        json.dumps({"count": "x", "allowance": 0}),
    ],
)
def test_load_fix_cycle_state_malformed_payloads_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.fix_cycle_path(5, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload)
    state = _pr_state_paths.load_fix_cycle_state(5, DEFAULT_FORGE_TARGET)
    assert state == _pr_state_paths.FixCycleState(count=0, allowance=0)


def test_load_fix_cycle_state_non_list_granted_question_ids_defaults_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.fix_cycle_path(5, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"count": 1, "allowance": 0, "granted_question_ids": "nope"}))
    state = _pr_state_paths.load_fix_cycle_state(5, DEFAULT_FORGE_TARGET)
    assert state == _pr_state_paths.FixCycleState(count=1, allowance=0)


def test_bump_fix_cycle_count_concurrent_calls_lose_no_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    threads = [
        threading.Thread(
            target=_pr_state_paths.bump_fix_cycle_count, args=(42, DEFAULT_FORGE_TARGET)
        )
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET).count == 20


def test_grant_fix_cycle_override_concurrent_distinct_grants_all_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    threads = [
        threading.Thread(
            target=_pr_state_paths.grant_fix_cycle_override,
            args=(42, f"q{i}", DEFAULT_FORGE_TARGET),
        )
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT * 10
    assert len(state.granted_question_ids) == 10


def test_fix_cycle_state_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_fix_cycle_count(5, _WIDGET_FORGE)
    assert _pr_state_paths.load_fix_cycle_state(5, _WIDGET_FORGE).count == 1
    assert _pr_state_paths.load_fix_cycle_state(5, DEFAULT_FORGE_TARGET).count == 0


def test_should_escalate_fix_cycle_severity_false_when_not_running() -> None:
    assert _pr_state_paths.should_escalate_fix_cycle_severity(10, False) is False


def test_should_escalate_fix_cycle_severity_false_below_threshold() -> None:
    assert _pr_state_paths.should_escalate_fix_cycle_severity(2, True) is False


def test_should_escalate_fix_cycle_severity_true_at_threshold() -> None:
    assert _pr_state_paths.should_escalate_fix_cycle_severity(3, True) is True


def test_should_escalate_fix_cycle_severity_true_above_threshold() -> None:
    assert _pr_state_paths.should_escalate_fix_cycle_severity(6, True) is True


def test_bump_fix_cycle_count_if_under_ceiling_returns_new_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET) == 1
    assert _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET) == 2


def test_bump_fix_cycle_count_if_under_ceiling_refuses_at_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(_pr_state_paths.FIX_CYCLE_CEILING):
        _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET) is None
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.count == _pr_state_paths.FIX_CYCLE_CEILING


def test_bump_fix_cycle_count_if_under_ceiling_honors_allowance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(_pr_state_paths.FIX_CYCLE_CEILING):
        _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET)
    _pr_state_paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET) == (
        _pr_state_paths.FIX_CYCLE_CEILING + 1
    )


def test_bump_fix_cycle_count_if_under_ceiling_concurrent_calls_never_exceed_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    for _ in range(_pr_state_paths.FIX_CYCLE_CEILING - 1):
        _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET)
    results: list[int | None] = []
    lock = threading.Lock()

    def _attempt() -> None:
        result = _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(42, DEFAULT_FORGE_TARGET)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=_attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(None) == 19
    assert sorted(r for r in results if r is not None) == [_pr_state_paths.FIX_CYCLE_CEILING]
    state = _pr_state_paths.load_fix_cycle_state(42, DEFAULT_FORGE_TARGET)
    assert state.count == _pr_state_paths.FIX_CYCLE_CEILING

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.teams import _pr_state_paths as paths

_ZERO_STATE = paths.GateRerunState(head_sha=None, count=0, lifetime=0)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _seed(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))


@pytest.mark.parametrize("missing", ["count", "lifetime"])
def test_a_gate_rerun_file_missing_a_counter_resets_the_whole_state(missing: str) -> None:
    payload = {"head_sha": "sha-1", "count": 2, "lifetime": 5, "checks": ["ci/e2e"]}
    del payload[missing]
    _seed(paths.gate_rerun_count_path(7, DEFAULT_FORGE_TARGET), payload)
    assert paths.load_gate_rerun_state(7, DEFAULT_FORGE_TARGET) == _ZERO_STATE


def test_a_gate_rerun_count_written_as_a_float_resets_the_whole_state() -> None:
    payload = {"head_sha": "sha-1", "count": 1.0, "lifetime": 1}
    _seed(paths.gate_rerun_count_path(7, DEFAULT_FORGE_TARGET), payload)
    assert paths.load_gate_rerun_state(7, DEFAULT_FORGE_TARGET) == _ZERO_STATE


def test_a_non_string_check_empties_only_the_check_list() -> None:
    _seed(paths.gate_rerun_count_path(7, DEFAULT_FORGE_TARGET), {
        "head_sha": "sha-1", "count": 2, "lifetime": 5,
        "checks": ["ci/e2e", 3], "specs": ["e2e/a.spec.ts"],
    })
    assert paths.load_gate_rerun_state(7, DEFAULT_FORGE_TARGET) == paths.GateRerunState(
        head_sha="sha-1", count=2, lifetime=5, specs=("e2e/a.spec.ts",)
    )


def test_a_non_string_granted_question_id_empties_only_the_granted_list() -> None:
    _seed(paths.fix_cycle_path(7, DEFAULT_FORGE_TARGET), {
        "count": 2, "allowance": 3, "granted_question_ids": ["q1", None],
    })
    state = paths.load_fix_cycle_state(7, DEFAULT_FORGE_TARGET)
    assert state == paths.FixCycleState(count=2, allowance=3)


def test_an_empty_string_gate_rerun_head_is_accepted() -> None:
    payload = {"head_sha": "", "count": 1, "lifetime": 1}
    _seed(paths.gate_rerun_count_path(7, DEFAULT_FORGE_TARGET), payload)
    assert paths.load_gate_rerun_state(7, DEFAULT_FORGE_TARGET) == paths.GateRerunState(
        head_sha="", count=1, lifetime=1
    )


@pytest.mark.parametrize(
    ("path_of", "load", "payload", "expected"),
    [
        (
            paths.gate_rerun_count_path,
            paths.load_gate_rerun_state,
            {"head_sha": "sha-1", "count": 1, "lifetime": 2, "stray": [1]},
            paths.GateRerunState(head_sha="sha-1", count=1, lifetime=2),
        ),
        (
            paths.fix_cycle_path,
            paths.load_fix_cycle_state,
            {"count": 1, "allowance": 3, "stray": {"a": 1}},
            paths.FixCycleState(count=1, allowance=3),
        ),
        (
            paths.system_head_path,
            paths.load_system_head,
            {"head_sha": "sha-1", "rung": "dirty", "stray": None},
            paths.SystemHeadMarker(head_sha="sha-1", rung="dirty"),
        ),
    ],
)
def test_unknown_keys_are_ignored_on_load(
    path_of: Callable[[int, ForgeTarget], Path],
    load: Callable[[int, ForgeTarget], object],
    payload: dict[str, object],
    expected: object,
) -> None:
    _seed(path_of(7, DEFAULT_FORGE_TARGET), payload)
    assert load(7, DEFAULT_FORGE_TARGET) == expected


@pytest.mark.parametrize("missing", ["count", "allowance"])
def test_a_fix_cycle_file_missing_a_counter_resets_the_whole_state(missing: str) -> None:
    payload = {"count": 2, "allowance": 3, "granted_question_ids": ["q1"]}
    del payload[missing]
    _seed(paths.fix_cycle_path(7, DEFAULT_FORGE_TARGET), payload)
    state = paths.load_fix_cycle_state(7, DEFAULT_FORGE_TARGET)
    assert state == paths.FixCycleState(count=0, allowance=0)


def test_a_rerun_bump_without_checks_or_specs_stores_none_after_one_that_had_them() -> None:
    paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=("ci/e2e",), specs=("e2e/a.spec.ts",)
    )
    paths.bump_gate_rerun_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"head_sha": "sha-1", "count": 2, "lifetime": 2}'
    )
    checks = paths.gate_rerun_checks_for_sha(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert checks == frozenset()
    assert paths.gate_rerun_specs_for_sha(42, DEFAULT_FORGE_TARGET, head_sha="sha-1") == ()


def test_releasing_the_ack_budget_to_zero_writes_zero_count_and_zero_lifetime() -> None:
    paths.bump_ack_budget(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert paths.release_ack_budget(42, DEFAULT_FORGE_TARGET, head_sha="sha-1") == 0
    assert paths.ack_budget_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"head_sha": "sha-1", "count": 0, "lifetime": 0}'
    )


def test_a_first_override_grant_writes_a_fix_cycle_count_of_zero() -> None:
    assert paths.grant_fix_cycle_override(42, "q1", DEFAULT_FORGE_TARGET) is True
    assert paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"count": 0, "allowance": 3, "granted_question_ids": ["q1"]}'
    )


def test_gate_rerun_lists_load_in_file_order_with_duplicates() -> None:
    _seed(paths.gate_rerun_count_path(7, DEFAULT_FORGE_TARGET), {
        "head_sha": "sha-1", "count": 1, "lifetime": 1,
        "checks": ["b", "a", "b"], "specs": ["z", "y", "z"],
    })
    state = paths.load_gate_rerun_state(7, DEFAULT_FORGE_TARGET)
    assert state.checks == ("b", "a", "b")
    assert state.specs == ("z", "y", "z")


def test_granted_question_ids_load_in_file_order_with_duplicates() -> None:
    _seed(paths.fix_cycle_path(7, DEFAULT_FORGE_TARGET), {
        "count": 1, "allowance": 3, "granted_question_ids": ["q2", "q1", "q2"],
    })
    state = paths.load_fix_cycle_state(7, DEFAULT_FORGE_TARGET)
    assert state.granted_question_ids == ("q2", "q1", "q2")


def test_forwarding_a_marker_with_no_write_time_writes_an_explicit_null() -> None:
    payload = {"head_sha": "sha-1", "rung": "dirty", "forwarded": False}
    _seed(paths.system_head_path(42, DEFAULT_FORGE_TARGET), payload)
    paths.mark_system_head_forwarded(42, DEFAULT_FORGE_TARGET)
    assert paths.system_head_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"head_sha": "sha-1", "rung": "dirty", "forwarded": true, "written_at": null}'
    )


def test_a_bare_integer_ack_budget_file_reads_as_no_head_with_that_count_and_lifetime() -> None:
    _seed(paths.ack_budget_path(42, DEFAULT_FORGE_TARGET), "3")
    assert paths.bump_ack_budget(42, DEFAULT_FORGE_TARGET, head_sha=None) == 4
    assert paths.ack_budget_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"head_sha": null, "count": 4, "lifetime": 4}'
    )


@pytest.mark.parametrize("payload", ["-4", "true"])
def test_a_bare_negative_or_boolean_reconcile_file_loads_as_the_default(payload: str) -> None:
    _seed(paths.reconcile_attempt_count_path(42, DEFAULT_FORGE_TARGET), payload)
    assert paths.load_reconcile_attempt_state(42, DEFAULT_FORGE_TARGET) == _ZERO_STATE


@pytest.mark.parametrize("payload", ["-4", "true"])
def test_a_bare_negative_or_boolean_ack_budget_file_loads_as_the_default(payload: str) -> None:
    _seed(paths.ack_budget_path(42, DEFAULT_FORGE_TARGET), payload)
    assert paths.bump_ack_budget(42, DEFAULT_FORGE_TARGET, head_sha=None) == 1


def test_a_gate_rerun_write_is_sorted_compact_json_in_field_order() -> None:
    paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=("ci/z", "ci/a"),
        specs=("e2e/b.spec.ts", "e2e/a.spec.ts"),
    )
    assert paths.gate_rerun_count_path(42, DEFAULT_FORGE_TARGET).read_text() == (
        '{"head_sha": "sha-1", "count": 1, "lifetime": 1, '
        '"checks": ["ci/a", "ci/z"], "specs": ["e2e/a.spec.ts", "e2e/b.spec.ts"]}'
    )


def test_a_fix_cycle_write_is_compact_json_in_field_order() -> None:
    paths.bump_fix_cycle_count(42, DEFAULT_FORGE_TARGET)
    path = paths.fix_cycle_path(42, DEFAULT_FORGE_TARGET)
    assert path.read_text() == '{"count": 1, "allowance": 0}'


def test_a_system_head_write_carries_all_four_keys_with_a_float_write_time() -> None:
    paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    on_disk = json.loads(paths.system_head_path(42, DEFAULT_FORGE_TARGET).read_text())
    assert list(on_disk) == ["head_sha", "rung", "forwarded", "written_at"]
    assert isinstance(on_disk["written_at"], float)


def test_a_null_head_and_zero_counts_are_always_written_with_no_list_keys() -> None:
    assert paths.release_ack_budget(42, DEFAULT_FORGE_TARGET, head_sha=None) == 0
    path = paths.ack_budget_path(42, DEFAULT_FORGE_TARGET)
    assert path.read_text() == '{"head_sha": null, "count": 0, "lifetime": 0}'


@pytest.mark.parametrize(
    ("written_at", "expected"),
    [(0, 0.0), (-5, -5.0), (2**53 + 1, 2.0**53), (10**300, 1e300)],
)
def test_an_unknown_head_marker_reads_any_finite_integer_write_time_as_a_float(
    written_at: int, expected: float
) -> None:
    payload = {"head_sha": None, "rung": "dirty", "written_at": written_at}
    _seed(paths.system_head_path(42, DEFAULT_FORGE_TARGET), payload)
    marker = paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None and marker.written_at == expected
    assert isinstance(marker.written_at, float)


def test_markers_differing_only_in_write_time_are_unequal() -> None:
    first = paths.SystemHeadMarker(head_sha="sha-1", rung="dirty", written_at=1.0)
    second = paths.SystemHeadMarker(head_sha="sha-1", rung="dirty", written_at=2.0)
    assert first != second


@pytest.mark.parametrize(
    ("model", "args"),
    [
        (paths.GateRerunState, (None, 0, 0)),
        (paths.FixCycleState, (0, 0)),
        (paths.SystemHeadMarker, (None, "dirty")),
    ],
)
def test_positional_construction_raises(
    model: Callable[..., object], args: tuple[object, ...]
) -> None:
    with pytest.raises(TypeError):
        model(*args)


@pytest.mark.parametrize(
    ("instance", "field", "value"),
    [
        (_ZERO_STATE, "count", 5),
        (paths.FixCycleState(count=0, allowance=0), "allowance", 3),
        (paths.SystemHeadMarker(head_sha="sha-1", rung="dirty"), "forwarded", True),
    ],
)
def test_assigning_a_field_raises(instance: object, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        setattr(instance, field, value)


def test_a_marker_with_an_empty_head_constructs_without_raising() -> None:
    assert paths.SystemHeadMarker(head_sha="", rung="dirty").head_sha == ""

from __future__ import annotations

from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_state_paths
from alan.teams._pr_ready import PrReadiness

_E2E_GATE = "Frontend E2E (Playwright, blocking)"
_BACKEND_CHECK = "Backend tests + coverage"


def _readiness(
    *,
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
) -> PrReadiness:
    return PrReadiness(
        ok=not failing_checks and not pending_checks,
        merge_state_status="CLEAN",
        review_decision="APPROVED",
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        failed_reasons=(
            (f"failing checks: {', '.join(failing_checks)}",) if failing_checks else ()
        ),
    )


def test_recover_transient_flake_bump_receives_specs_fetched_before_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)

    def _fetch_specs(*_a: object, **_k: object) -> tuple[str, ...]:
        order.append("specs")
        return ("e2e/chatty.spec.ts",)

    def _rerun(*_a: object, **_k: object) -> bool:
        order.append("rerun")
        return True

    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_specs", _fetch_specs)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", _rerun)
    captured: dict[str, object] = {}

    def _bump(_pr: int, _forge: object = None, *, head_sha: object, **kw: object) -> int:
        del head_sha
        captured.update(kw)
        return 1

    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    ok, cls = merge_recovery._recover_transient_flake(  # noqa: SLF001
        42, "task-x", readiness, DEFAULT_FORGE_TARGET, "sha-1",
    )
    assert (ok, cls) == (True, MergeBlockClass.TRANSIENT_FLAKE)
    assert order == ["specs", "rerun"]
    assert captured["specs"] == ("e2e/chatty.spec.ts",)


def test_recover_transient_flake_exhausted_budget_skips_spec_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)

    def _boom(*_a: object, **_k: object) -> tuple[str, ...]:
        raise AssertionError("must not fetch specs once the budget is exhausted")

    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_specs", _boom)
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    ok, cls = merge_recovery._recover_transient_flake(  # noqa: SLF001
        42, "task-x", readiness, DEFAULT_FORGE_TARGET, "sha-1",
    )
    assert (ok, cls) == (False, MergeBlockClass.TRANSIENT_FLAKE)


def test_merge_lane_survivor_persisted_specs_overlap_keeps_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_E2E_GATE,),
        specs=("e2e/chatty.spec.ts",),
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_specs", lambda *_a, **_kw: ("e2e/chatty.spec.ts",)
    )
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        42, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_merge_lane_survivor_persisted_specs_disjoint_drops_e2e_keeps_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_E2E_GATE, _BACKEND_CHECK),
        specs=("e2e/before.spec.ts",),
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_specs", lambda *_a, **_kw: ("e2e/after.spec.ts",)
    )
    readiness = _readiness(failing_checks=(_E2E_GATE, _BACKEND_CHECK))
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        42, DEFAULT_FORGE_TARGET,
    ) == {_BACKEND_CHECK: "sha-1"}


def test_merge_lane_survivor_absent_persisted_specs_falls_back_to_check_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_E2E_GATE,)
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_specs", lambda *_a, **_kw: ("e2e/after.spec.ts",)
    )
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        42, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_merge_lane_survivor_after_side_fetch_empty_falls_back_to_check_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_E2E_GATE,),
        specs=("e2e/before.spec.ts",),
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_specs", lambda *_a, **_kw: ())
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        42, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_merge_lane_survivor_skips_spec_fetch_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_BACKEND_CHECK,)
    )

    def _boom(*_a: object, **_k: object) -> tuple[str, ...]:
        raise AssertionError("must not fetch specs when the E2E check is untargeted")

    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_specs", _boom)
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    merge_recovery._merge_lane_survivor_evidence(  # noqa: SLF001
        42, readiness, DEFAULT_FORGE_TARGET, "sha-1",
    )
    assert _pr_flake_ledger.load_gate_survived_checks(42, DEFAULT_FORGE_TARGET) == {}


def test_classify_block_genuine_when_spec_gated_record_keeps_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,), specs_before=("e2e/x.spec.ts",), specs_after=("e2e/x.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(42, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    survived_at_head = _pr_flake_ledger.survived_at_head_checks(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert (
        classify_block(readiness, (), DEFAULT_FORGE_TARGET, survived_at_head=survived_at_head)
        is MergeBlockClass.GENUINE
    )


def test_classify_block_transient_flake_when_spec_gated_record_drops_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,), specs_before=("e2e/x.spec.ts",), specs_after=("e2e/y.spec.ts",),
    )
    _pr_flake_ledger.record_flake_survival(42, survived, "sha-1", forge_target=DEFAULT_FORGE_TARGET)
    readiness = _readiness(failing_checks=(_E2E_GATE,))
    survived_at_head = _pr_flake_ledger.survived_at_head_checks(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert (
        classify_block(readiness, (), DEFAULT_FORGE_TARGET, survived_at_head=survived_at_head)
        is MergeBlockClass.TRANSIENT_FLAKE
    )

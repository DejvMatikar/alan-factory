from __future__ import annotations

from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_freshness, _pr_reconcile, _pr_state_paths
from alan.teams._pr_ready import PrReadiness
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result

_FLAKY_GATE = "Frontend E2E (Playwright, blocking)"


def _readiness(
    *,
    ok: bool = False,
    merge_state_status: str = "CLEAN",
    review_decision: str = "APPROVED",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    failed_reasons: tuple[str, ...] = (),
    check_count: int = 1,
) -> PrReadiness:
    return PrReadiness(
        ok=ok,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        failed_reasons=failed_reasons,
        check_count=check_count,
    )


def test_ci_reset_delay_classes_includes_behind_plus_transient() -> None:
    assert MergeBlockClass.BEHIND_PLUS_TRANSIENT in merge_recovery.CI_RESET_DELAY_CLASSES


def test_classify_block_post_update_red_when_system_head_true() -> None:
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    assert (
        classify_block(r, (), DEFAULT_FORGE_TARGET, system_head=True)
        is MergeBlockClass.POST_UPDATE_RED
    )


def test_classify_block_genuine_when_system_head_default_false() -> None:
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_classify_block_post_update_red_does_not_preempt_behind_plus_transient() -> None:
    r = _readiness(
        merge_state_status="BEHIND", failed_reasons=("mergeStateStatus=BEHIND",),
    )
    assert (
        classify_block(r, (), DEFAULT_FORGE_TARGET, system_head=True)
        is MergeBlockClass.BEHIND_PLUS_TRANSIENT
    )


def test_classify_block_post_update_red_does_not_preempt_sonar_rung() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    assert (
        classify_block(
            r,
            (),
            DEFAULT_FORGE_TARGET.model_copy(
                update={"authoritative_checks": frozenset({"SonarCloud Code Analysis"})}
            ),
            system_head=True,
        )
        is MergeBlockClass.SONAR_NO_ANNOTATIONS
    )


def test_classify_block_post_update_red_does_not_preempt_pending_rung() -> None:
    r = _readiness(pending_checks=("ci/test",), failed_reasons=("pending checks: ci/test",))
    assert (
        classify_block(r, (), DEFAULT_FORGE_TARGET, system_head=True)
        is MergeBlockClass.STILL_COMPUTING
    )


def test_unblock_instruction_post_update_red_names_the_system_update_as_suspect() -> None:
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.POST_UPDATE_RED, pr_number=None)
    assert blocker is not None
    assert blocker.instruction is not None
    assert "system" in blocker.instruction.lower()
    assert "re-dispatch pr_merge" in blocker.instruction


def test_attempt_recovery_system_head_marker_match_yields_post_update_red(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.POST_UPDATE_RED


def test_attempt_recovery_system_head_marker_stale_sha_yields_genuine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_system_head(42, "sha-old", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.GENUINE


def test_attempt_recovery_system_head_marker_already_forwarded_still_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    _pr_state_paths.mark_system_head_forwarded(42, DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.POST_UPDATE_RED


def test_attempt_recovery_no_marker_no_flaky_skips_head_sha_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    def boom(*_a: object, **_kw: object) -> str:
        raise AssertionError("must not fetch head sha with no marker and no flaky failure")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", boom)
    r = _readiness(failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",))
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.GENUINE


def test_attempt_recovery_marker_present_dirty_reuses_single_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.save_system_head(42, "sha-1", "behind_plus_transient", DEFAULT_FORGE_TARGET)
    calls: list[int] = []

    def fake_fetch(*_a: object, **_kw: object) -> str:
        calls.append(1)
        return "sha-1"

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", fake_fetch)
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RACED),
    )
    r = _readiness(merge_state_status="DIRTY", failed_reasons=("mergeStateStatus=DIRTY",))
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.DIRTY
    assert len(calls) == 1


def test_recover_behind_plus_transient_writes_system_head_marker_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    always(_active_fake_forge, "request_branch_update", api_result())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    r = _readiness(merge_state_status="BEHIND", failed_reasons=("mergeStateStatus=BEHIND",))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is True
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (
        "sha-new", "behind_plus_transient", False,
    )


def test_recover_behind_plus_transient_no_marker_when_update_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    always(_active_fake_forge, "request_branch_update", api_result(returncode=1, stderr="boom"))

    def boom(*_a: object, **_kw: object) -> str:
        raise AssertionError("must not fetch head sha when the branch update failed")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", boom)
    r = _readiness(merge_state_status="BEHIND", failed_reasons=("mergeStateStatus=BEHIND",))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET) is None


def test_recover_stale_measured_baseline_writes_marker_on_updated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.UPDATED,
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    r = _readiness(failed_reasons=(_pr_freshness.MEASURED_BASELINE_STALE_REASON,))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is True
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (
        "sha-new", "stale_measured_baseline", False,
    )


def test_recover_stale_measured_baseline_no_marker_on_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.CONFLICT,
    )
    r = _readiness(failed_reasons=(_pr_freshness.MEASURED_BASELINE_STALE_REASON,))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET) is None


def test_recover_dirty_writes_marker_using_post_sha_on_reconciled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "pre-sha")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _pr_reconcile.ReconcileResult(
            _pr_reconcile.ReconcileOutcome.RECONCILED, post_sha="post-sha-123",
        ),
    )
    r = _readiness(merge_state_status="DIRTY", failed_reasons=("mergeStateStatus=DIRTY",))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is True
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == ("post-sha-123", "dirty", False)


def test_recover_dirty_no_marker_on_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "pre-sha")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT),
    )
    r = _readiness(merge_state_status="DIRTY", failed_reasons=("mergeStateStatus=DIRTY",))
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET) is None


def test_merge_lane_writes_survivor_evidence_after_rerun_concluded_at_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_FLAKY_GATE,)
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    r = _readiness(
        failing_checks=(_FLAKY_GATE,), failed_reasons=(f"failing checks: {_FLAKY_GATE}",)
    )
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        42, DEFAULT_FORGE_TARGET,
    ) == {_FLAKY_GATE: "sha-1"}


def test_merge_lane_survivor_evidence_excludes_check_the_rerun_never_targeted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    other_flaky_check = "Backend tests + coverage"
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_FLAKY_GATE,)
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    r = _readiness(
        failing_checks=(other_flaky_check,),
        failed_reasons=(f"failing checks: {other_flaky_check}",),
    )
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(42, DEFAULT_FORGE_TARGET) == {}


def test_bump_gate_rerun_count_persists_targeted_checks_for_the_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1", checks=(_FLAKY_GATE,)
    )
    assert _pr_state_paths.gate_rerun_checks_for_sha(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1"
    ) == {_FLAKY_GATE}
    assert _pr_state_paths.gate_rerun_checks_for_sha(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-2"
    ) == frozenset()


def test_merge_lane_does_not_write_survivor_evidence_before_any_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    r = _readiness(
        failing_checks=(_FLAKY_GATE,), failed_reasons=(f"failing checks: {_FLAKY_GATE}",)
    )
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(42, DEFAULT_FORGE_TARGET) == {}


def test_merge_lane_does_not_write_survivor_evidence_when_pending_checks_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_gate_rerun_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        pending_checks=("other/check",),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(42, DEFAULT_FORGE_TARGET) == {}

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_branch, _pr_reconcile, _pr_state_paths
from alan.teams._pr_ready import PrReadiness


def _dirty_readiness() -> PrReadiness:
    return PrReadiness(
        ok=False,
        merge_state_status="DIRTY",
        review_decision="APPROVED",
        failed_reasons=("mergeStateStatus=DIRTY",),
    )


def _reconcile_result(
    outcome: _pr_reconcile.ReconcileOutcome,
    *, base_sha: str | None = "b" * 40, pre_sha: str | None = "1" * 40,
    post_sha: str | None = "2" * 40,
) -> _pr_reconcile.ReconcileResult:
    return _pr_reconcile.ReconcileResult(outcome, base_sha, pre_sha, post_sha)


def test_ci_reset_delay_classes_includes_dirty() -> None:
    assert MergeBlockClass.DIRTY in merge_recovery.CI_RESET_DELAY_CLASSES
    assert MergeBlockClass.TRANSIENT_FLAKE in merge_recovery.CI_RESET_DELAY_CLASSES
    assert MergeBlockClass.SONAR_HOTSPOT in merge_recovery.CI_RESET_DELAY_CLASSES


def test_reconcile_attempts_exhausted_false_for_fresh_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    assert merge_recovery.reconcile_attempts_exhausted(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET
    ) is False


def test_reconcile_attempts_exhausted_true_after_per_sha_budget_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert merge_recovery.reconcile_attempts_exhausted(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET
    ) is True


def test_reconcile_attempts_exhausted_false_for_new_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    assert merge_recovery.reconcile_attempts_exhausted(
        42, "sha-2", forge_target=DEFAULT_FORGE_TARGET
    ) is False


def test_reconcile_attempts_exhausted_true_once_lifetime_cap_spent_regardless_of_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    p = _pr_state_paths.reconcile_attempt_count_path(42, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-1", "count": 0, "lifetime": 6}))
    assert merge_recovery.reconcile_attempts_exhausted(
        42, "sha-new", forge_target=DEFAULT_FORGE_TARGET
    ) is True


def test_recover_dirty_exhausted_skips_reconcile_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    _pr_state_paths.bump_reconcile_attempt_count(42, DEFAULT_FORGE_TARGET, head_sha="sha-1")

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("must not attempt reconciliation once the budget is exhausted")

    monkeypatch.setattr(_pr_reconcile, "reconcile_branch", boom)
    ok, cls, rec = merge_recovery._recover_dirty(  # noqa: SLF001
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert (ok, cls, rec) == (False, MergeBlockClass.DIRTY, None)


def test_recover_dirty_reconciled_recovers_and_bumps_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _reconcile_result(_pr_reconcile.ReconcileOutcome.RECONCILED),
    )
    ok, cls, rec = merge_recovery._recover_dirty(  # noqa: SLF001
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert ok is True
    assert cls is MergeBlockClass.DIRTY
    assert rec is not None
    assert rec.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED
    assert _pr_state_paths.reconcile_attempts_for_sha(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1"
    ) == 1
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["action"] == "merge_recovery_attempt"
    assert audit["block_class"] == "dirty"
    assert audit["recovered"] is True
    assert audit["reconcile_outcome"] == "reconciled"


def test_recover_dirty_conflict_does_not_recover_but_bumps_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _reconcile_result(_pr_reconcile.ReconcileOutcome.CONFLICT),
    )
    ok, cls, rec = merge_recovery._recover_dirty(  # noqa: SLF001
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert ok is False
    assert cls is MergeBlockClass.DIRTY
    assert rec is not None
    assert rec.outcome is _pr_reconcile.ReconcileOutcome.CONFLICT
    assert _pr_state_paths.reconcile_attempts_for_sha(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1"
    ) == 1
    audit = json.loads((tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1])
    assert audit["recovered"] is False
    assert audit["reconcile_outcome"] == "conflict"


@pytest.mark.parametrize(
    "outcome", [
        _pr_reconcile.ReconcileOutcome.ALREADY_CURRENT,
        _pr_reconcile.ReconcileOutcome.RACED,
        _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED,
    ],
)
def test_recover_dirty_no_budget_spend_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    outcome: _pr_reconcile.ReconcileOutcome,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch", lambda *_a, **_kw: _reconcile_result(outcome),
    )
    ok, cls, rec = merge_recovery._recover_dirty(  # noqa: SLF001
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert ok is False
    assert cls is MergeBlockClass.DIRTY
    assert rec is not None
    assert rec.outcome is outcome
    assert _pr_state_paths.reconcile_attempts_for_sha(
        42, DEFAULT_FORGE_TARGET, head_sha="sha-1"
    ) == 0


def test_recover_dirty_fetch_head_sha_failure_still_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _reconcile_result(_pr_reconcile.ReconcileOutcome.RECONCILED),
    )
    ok, cls, rec = merge_recovery._recover_dirty(  # noqa: SLF001
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert ok is True
    assert cls is MergeBlockClass.DIRTY
    assert rec is not None
    assert rec.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED


def test_attempt_mechanical_recovery_dirty_dispatches_to_reconcile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _reconcile_result(_pr_reconcile.ReconcileOutcome.RECONCILED),
    )
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _dirty_readiness(), forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.DIRTY
    assert rec is not None
    assert rec.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED


def test_attempt_mechanical_recovery_non_dirty_returns_none_third_element(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    readiness = PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="APPROVED",
        failing_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
    )
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.GENUINE
    assert rec is None


def test_unblock_instruction_dirty_has_manual_recovery_instruction() -> None:
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.DIRTY, pr_number=42)
    assert blocker is not None
    assert blocker.instruction is not None
    assert "re-dispatch pr_merge" in blocker.instruction
    assert blocker.action_url is None


def test_unblock_instruction_dirty_ignores_pr_number() -> None:
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.DIRTY, pr_number=None)
    assert blocker is not None
    assert blocker.instruction is not None


def test_reconcile_verdict_is_terminal_true_for_conflict() -> None:
    rec = _reconcile_result(_pr_reconcile.ReconcileOutcome.CONFLICT)
    assert merge_recovery.reconcile_verdict_is_terminal(rec) is True


@pytest.mark.parametrize(
    "outcome", [
        _pr_reconcile.ReconcileOutcome.RECONCILED,
        _pr_reconcile.ReconcileOutcome.ALREADY_CURRENT,
        _pr_reconcile.ReconcileOutcome.RACED,
        _pr_reconcile.ReconcileOutcome.TRANSPORT_FAILED,
    ],
)
def test_reconcile_verdict_is_terminal_false_for_non_conflict(
    outcome: _pr_reconcile.ReconcileOutcome,
) -> None:
    rec = _reconcile_result(outcome)
    assert merge_recovery.reconcile_verdict_is_terminal(rec) is False


def test_reconcile_verdict_is_terminal_false_for_none() -> None:
    assert merge_recovery.reconcile_verdict_is_terminal(None) is False

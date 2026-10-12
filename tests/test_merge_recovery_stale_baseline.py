from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams import _pr_branch, _pr_freshness
from alan.teams._pr_ready import PrReadiness


def _readiness(
    *,
    merge_state_status: str = "CLEAN",
    review_decision: str = "APPROVED",
    failed_reasons: tuple[str, ...] = (),
) -> PrReadiness:
    return PrReadiness(
        ok=not failed_reasons,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failed_reasons=failed_reasons,
    )


def _stale_baseline_readiness() -> PrReadiness:
    return _readiness(failed_reasons=(_pr_freshness.MEASURED_BASELINE_STALE_REASON,))


def test_classify_block_stale_measured_baseline() -> None:
    result = classify_block(_stale_baseline_readiness(), (), DEFAULT_FORGE_TARGET)
    assert result is MergeBlockClass.STALE_MEASURED_BASELINE


def test_classify_block_dirty_wins_over_stale_measured_baseline() -> None:
    r = _readiness(
        merge_state_status="DIRTY",
        failed_reasons=(_pr_freshness.MEASURED_BASELINE_STALE_REASON,),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.DIRTY


def test_stale_measured_baseline_in_ci_reset_delay_classes() -> None:
    assert MergeBlockClass.STALE_MEASURED_BASELINE in merge_recovery.CI_RESET_DELAY_CLASSES


def test_stale_measured_baseline_conflict_not_in_ci_reset_delay_classes() -> None:
    assert (
        MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT
        not in merge_recovery.CI_RESET_DELAY_CLASSES
    )


def test_attempt_recovery_updated_outcome_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.UPDATED,
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _stale_baseline_readiness(), forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.STALE_MEASURED_BASELINE
    assert rec is None
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["action"] == "merge_recovery_attempt"
    assert audit["block_class"] == "stale_measured_baseline"
    assert audit["recovered"] is True


def test_attempt_recovery_conflict_outcome_reclassifies_and_does_not_recover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.CONFLICT,
    )
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _stale_baseline_readiness(), forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT
    assert rec is None
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "stale_measured_baseline_conflict"
    assert audit["recovered"] is False


def test_attempt_recovery_failed_outcome_stays_stale_measured_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.FAILED,
    )
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _stale_baseline_readiness(), forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.STALE_MEASURED_BASELINE
    assert rec is None
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "stale_measured_baseline"
    assert audit["recovered"] is False


def test_unblock_instruction_stale_measured_baseline_distinct_from_dirty() -> None:
    baseline = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE, pr_number=None,
    )
    dirty = merge_recovery.unblock_instruction(MergeBlockClass.DIRTY, pr_number=None)
    assert baseline is not None
    assert dirty is not None
    assert baseline.instruction != dirty.instruction
    assert baseline.detail != dirty.detail
    assert "re-dispatch pr_merge" in (baseline.instruction or "")


def test_unblock_instruction_stale_measured_baseline_conflict_distinct_from_dirty() -> None:
    conflict = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, pr_number=None,
    )
    dirty = merge_recovery.unblock_instruction(MergeBlockClass.DIRTY, pr_number=None)
    assert conflict is not None
    assert dirty is not None
    assert conflict.instruction != dirty.instruction
    assert "re-dispatch pr_merge" in (conflict.instruction or "")


def test_unblock_instruction_stale_measured_baseline_distinct_from_its_conflict_sibling() -> None:
    baseline = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE, pr_number=None,
    )
    conflict = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, pr_number=None,
    )
    assert baseline is not None
    assert conflict is not None
    assert baseline.instruction != conflict.instruction
    assert baseline.detail != conflict.detail


def test_unblock_instructions_do_not_claim_the_pr_itself_touched_the_baseline_file() -> None:
    baseline = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE, pr_number=None,
    )
    conflict = merge_recovery.unblock_instruction(
        MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, pr_number=None,
    )
    assert baseline is not None
    assert conflict is not None
    assert "This PR touches" not in (baseline.instruction or "")
    assert "This PR touches" not in (conflict.instruction or "")

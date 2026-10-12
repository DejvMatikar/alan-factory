from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import merge_gate, merge_recovery
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass
from alan.park import Blocker
from alan.teams import (
    _forge_errors,
    _pr_closing,
    _pr_freshness,
    _pr_ready,
    _pr_reconcile,
)


def _ws(root: Path) -> workspace_mod.Workspace:
    return workspace_mod.create("merge-gate-attempt", "d", root=root)


def _readiness(**kwargs: object) -> _pr_ready.PrReadiness:
    base: dict[str, object] = {
        "ok": False,
        "merge_state_status": "CLEAN",
        "review_decision": "APPROVED",
    }
    base.update(kwargs)
    return _pr_ready.PrReadiness(**base)  # type: ignore[arg-type]


def _rows(tmp_path: Path) -> list[dict[str, object]]:
    audit_file = tmp_path / "logs" / "merge_gate_audit.jsonl"
    if not audit_file.exists():
        return []
    return [json.loads(line) for line in audit_file.read_text().splitlines()]


def test_attempt_recovery_returns_not_attempted_when_already_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(ok=True),
    )
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: False)
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert isinstance(attempt, merge_gate.MergeGateNotAttempted)
    assert _rows(tmp_path) == []


def test_attempt_recovery_recovered_writes_audit_row_with_recovered_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(
            merge_state_status="BEHIND", failed_reasons=("mergeStateStatus=BEHIND",),
        ),
    )
    monkeypatch.setattr(
        merge_recovery, "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.BEHIND_PLUS_TRANSIENT, None),
    )
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert isinstance(attempt, merge_gate.MergeGateRecovered)
    assert attempt.block_class is MergeBlockClass.BEHIND_PLUS_TRANSIENT
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["recovered"] is True
    assert rows[0]["outcome"] == "pr_merge"


def test_attempt_recovery_not_recovered_writes_no_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(
            merge_state_status="BLOCKED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert isinstance(attempt, merge_gate.MergeGateBlocked)
    assert attempt.block_class is MergeBlockClass.GENUINE
    assert _rows(tmp_path) == []


def test_attempt_recovery_exception_writes_attempt_failed_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(
            merge_state_status="BLOCKED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )

    def _boom(*_a: object, **_kw: object) -> object:
        raise _forge_errors.ForgeTransientError("gh unavailable")

    monkeypatch.setattr(merge_recovery, "attempt_mechanical_recovery", _boom)
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert isinstance(attempt, merge_gate.MergeGateAttemptFailed)
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "attempt_failed"
    assert rows[0]["block_class"] is None


def test_attempt_recovery_propagates_non_transient_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(
            merge_state_status="BLOCKED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )

    def _boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(merge_recovery, "attempt_mechanical_recovery", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert _rows(tmp_path) == []


def test_attempt_recovery_carries_reconcile_result_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _readiness(
            merge_state_status="DIRTY", failed_reasons=("mergeStateStatus=DIRTY",),
        ),
    )
    reconciled = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RECONCILED)
    monkeypatch.setattr(
        merge_recovery, "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.DIRTY, reconciled),
    )
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert isinstance(attempt, merge_gate.MergeGateRecovered)
    assert attempt.reconcile is reconciled
    rows = _rows(tmp_path)
    assert rows[0]["reconcile_outcome"] == "reconciled"


def test_merge_gate_decision_attempted_requires_a_real_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _readiness(ok=True),
    )
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: False)
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    decision = merge_gate.MergeGateDecision.attempted(attempt)
    assert decision.policy == "attempted"
    assert decision.attempt is attempt
    assert decision.skip_reason is None


def test_merge_gate_decision_skipped_carries_a_reason() -> None:
    decision = merge_gate.MergeGateDecision.skipped("no pull request to gate against")
    assert decision.policy == "skipped"
    assert decision.attempt is None
    assert decision.skip_reason == "no pull request to gate against"


def test_enrich_conflict_blockers_appends_behind_commits() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT,
        behind_commits=("abc123 fix a", "def456 fix b"),
    )
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    behind = [b for b in blockers if b.kind == "behind_commit"]
    assert [b.name for b in behind] == ["abc123 fix a", "def456 fix b"]
    assert not any(b.name == "behind_commits_truncated" for b in blockers)


def test_enrich_conflict_blockers_truncates_behind_commits() -> None:
    blockers: list[Blocker] = []
    commits = tuple(f"sha{i} msg" for i in range(12))
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT, behind_commits=commits,
    )
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    behind = [b for b in blockers if b.kind == "behind_commit"]
    assert len(behind) == 10
    truncated = next(b for b in blockers if b.name == "behind_commits_truncated")
    assert "2" in truncated.detail


def test_enrich_conflict_blockers_no_behind_blockers_when_empty() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT)
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    assert not any(b.kind == "behind_commit" for b in blockers)


def test_enrich_conflict_blockers_uses_pre_sha_over_head_sha_when_present() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT, pre_sha="cafefeed",
    )
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    branch_head = next(b for b in blockers if b.name == "branch_head")
    assert "cafefeed" in branch_head.detail


def test_enrich_conflict_blockers_keeps_pending_blockers_when_rec_is_none() -> None:
    blockers: list[Blocker] = [
        Blocker(kind="pending_check", name="ci/test", detail="check pending: ci/test"),
    ]
    merge_gate.enrich_conflict_blockers(blockers, None, "deadbeef")
    assert blockers[0] == Blocker(
        kind="pending_check", name="ci/test", detail="check pending: ci/test",
    )
    branch_head = next(b for b in blockers if b.name == "branch_head")
    assert "deadbeef" in branch_head.detail


def test_enrich_conflict_blockers_none_rec_unknown_head_sha() -> None:
    blockers: list[Blocker] = []
    merge_gate.enrich_conflict_blockers(blockers, None, "")
    branch_head = next(b for b in blockers if b.name == "branch_head")
    assert "unknown" in branch_head.detail


def test_conflict_fix_decision_false_for_non_dirty_block_class() -> None:
    blockers: list[Blocker] = []
    assert (
        merge_gate.conflict_fix_decision(
            blockers, None, MergeBlockClass.GENUINE, "deadbeef",
        )
        is False
    )
    assert blockers == []


def test_conflict_fix_decision_true_when_reconcile_ladder_exhausted() -> None:
    blockers: list[Blocker] = []
    assert merge_gate.conflict_fix_decision(
        blockers, None, MergeBlockClass.DIRTY, "deadbeef",
    )
    assert any(b.name == "branch_head" for b in blockers)


def test_conflict_fix_decision_true_for_terminal_conflict_verdict() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT, conflicted_paths=("alan/foo.py",),
    )
    assert merge_gate.conflict_fix_decision(blockers, rec, MergeBlockClass.DIRTY, "deadbeef")
    assert any(b.kind == "conflicted_path" and b.name == "alan/foo.py" for b in blockers)


def test_conflict_fix_decision_false_for_raced_verdict() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RACED)
    assert (
        merge_gate.conflict_fix_decision(blockers, rec, MergeBlockClass.DIRTY, "deadbeef")
        is False
    )
    assert blockers == []


def test_conflict_fix_decision_false_for_stale_measured_baseline_conflict() -> None:
    blockers: list[Blocker] = []
    assert (
        merge_gate.conflict_fix_decision(
            blockers, None, MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, "deadbeef",
        )
        is False
    )
    assert blockers == []


def test_readiness_after_closing_links_check_skips_when_already_failing(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path / "ws")
    readiness = _readiness(ok=False, failed_reasons=("mergeStateStatus=DIRTY",))
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness


def _boom_closing_read(*_a: object, **_kw: object) -> frozenset[int]:
    raise AssertionError("no forge call expected")


def test_readiness_after_closing_links_check_skips_when_no_trailer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _ws(tmp_path / "ws")
    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", _boom_closing_read)
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness


def test_readiness_after_closing_links_check_skips_on_transient_forge_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create(
        "closing-links-transient", "Ship it.\n\nDelivers-Issues: 12", root=tmp_path / "ws",
    )

    def _raise(*_a: object, **_kw: object) -> frozenset[int]:
        raise _forge_errors.ForgeTransientError("gh timed out")

    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", _raise)
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness


def test_readiness_after_closing_links_check_skips_on_rate_limited_forge_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create(
        "closing-links-rate-limited", "Ship it.\n\nDelivers-Issues: 12", root=tmp_path / "ws",
    )

    def _raise(*_a: object, **_kw: object) -> frozenset[int]:
        raise _forge_errors.ForgeRateLimited("rate limited", 0.0)

    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", _raise)
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness


def test_readiness_after_closing_links_check_skips_when_forge_read_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create(
        "closing-links-unavailable", "Ship it.\n\nDelivers-Issues: 12", root=tmp_path / "ws",
    )
    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", lambda *_a, **_kw: None)
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness


def test_readiness_after_closing_links_check_flags_missing_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create(
        "closing-links", "Ship it.\n\nDelivers-Issues: 12, 34", root=tmp_path / "ws",
    )
    monkeypatch.setattr(
        _pr_closing, "gh_pr_closing_issue_numbers", lambda *_a, **_kw: frozenset({12})
    )
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result.ok is False
    missing_reasons = [
        r for r in result.failed_reasons if r.startswith(_pr_closing.CLOSING_LINKS_MISSING_REASON)
    ]
    assert len(missing_reasons) == 1
    assert "#34" in missing_reasons[0]
    assert "#12" not in missing_reasons[0]


def test_branch_head_blocker_carries_pinned_push_constraint_when_branch_known() -> None:
    b = merge_gate._branch_head_blocker("deadbeef", "teams/foo-inv_1")  # noqa: SLF001
    assert b.instruction == (
        "git push --force-with-lease=teams/foo-inv_1:deadbeef origin teams/foo-inv_1"
    )


def test_branch_head_blocker_omits_instruction_when_branch_unknown() -> None:
    b = merge_gate._branch_head_blocker("deadbeef")  # noqa: SLF001
    assert b.instruction is None


def test_branch_head_blocker_omits_instruction_when_sha_unknown() -> None:
    b = merge_gate._branch_head_blocker(None, "teams/foo-inv_1")  # noqa: SLF001
    assert b.instruction is None


def test_conflict_fix_decision_threads_branch_into_push_constraint() -> None:
    blockers: list[Blocker] = []
    result = merge_gate.conflict_fix_decision(
        blockers, None, MergeBlockClass.DIRTY, "deadbeef", "teams/foo-inv_1",
    )
    assert result is True
    branch_head = next(b for b in blockers if b.name == "branch_head")
    assert branch_head.instruction == (
        "git push --force-with-lease=teams/foo-inv_1:deadbeef origin teams/foo-inv_1"
    )
    assert "main" not in branch_head.instruction


def test_conflict_fix_decision_omits_instruction_without_a_branch() -> None:
    blockers: list[Blocker] = []
    result = merge_gate.conflict_fix_decision(
        blockers, None, MergeBlockClass.DIRTY, "deadbeef",
    )
    assert result is True
    branch_head = next(b for b in blockers if b.name == "branch_head")
    assert branch_head.instruction is None


def test_readiness_after_closing_links_check_clean_pass_when_all_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create(
        "closing-links-clean", "Ship it.\n\nDelivers-Issues: 12", root=tmp_path / "ws",
    )
    monkeypatch.setattr(
        _pr_closing, "gh_pr_closing_issue_numbers", lambda *_a, **_kw: frozenset({12})
    )
    readiness = _readiness(ok=True)
    result = merge_gate.readiness_after_closing_links_check(
        ws, 42, readiness, DEFAULT_FORGE_TARGET,
    )
    assert result is readiness

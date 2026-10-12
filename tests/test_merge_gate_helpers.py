from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import e2e_failures, merge_gate, merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.park import Blocker
from alan.teams import _pr_branch, _pr_freshness, _pr_reconcile


def _rows(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (tmp_path / "logs" / "merge_gate_audit.jsonl").read_text().splitlines()
    ]


def test_record_merge_gate_audit_writes_brief_attached_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    merge_gate.record_merge_gate_audit(
        "task-x", 42, merge_recovery.MergeBlockClass.GENUINE, False, "implementation_fix",
        brief_attached=True,
    )
    rows = _rows(tmp_path)
    assert rows[-1]["brief_attached"] is True
    assert rows[-1]["block_class"] == "genuine"
    assert rows[-1]["outcome"] == "implementation_fix"


def test_record_merge_gate_audit_defaults_brief_attached_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    merge_gate.record_merge_gate_audit(
        "task-x", 42, merge_recovery.MergeBlockClass.TRANSIENT_FLAKE, True, "pr_merge",
    )
    rows = _rows(tmp_path)
    assert rows[-1]["brief_attached"] is False


def test_record_merge_gate_audit_writes_null_block_class_for_attempt_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    merge_gate.record_merge_gate_audit("task-x", 42, None, False, "attempt_failed")
    rows = _rows(tmp_path)
    assert rows[-1]["block_class"] is None
    assert rows[-1]["outcome"] == "attempt_failed"


def test_stale_measured_baseline_forces_check_delegates_to_freshness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []

    def _fake(pr_number: int, task_id: str, **_kw: object) -> bool:
        calls.append((pr_number, task_id))
        return True

    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", _fake)
    result = merge_gate.stale_measured_baseline_forces_check(
        42, "task-x", DEFAULT_FORGE_TARGET,
    )
    assert result is True
    assert calls == [(42, "task-x")]


def test_append_unblock_instruction_appends_recipe_for_known_class() -> None:
    blockers: list[Blocker] = []
    merge_gate.append_unblock_instruction(
        blockers, merge_recovery.MergeBlockClass.SONAR_HOTSPOT, 42,
    )
    assert len(blockers) == 1
    assert blockers[0].instruction is not None


def test_append_unblock_instruction_noop_for_unrecognized_class() -> None:
    blockers: list[Blocker] = []
    merge_gate.append_unblock_instruction(blockers, merge_recovery.MergeBlockClass.GENUINE, 42)
    assert blockers == []


def test_enrich_transient_flake_blockers_noop_for_non_flake_class() -> None:
    blockers: list[Blocker] = []
    merge_gate.enrich_transient_flake_blockers(
        blockers, merge_recovery.MergeBlockClass.GENUINE, 42, DEFAULT_FORGE_TARGET,
    )
    assert blockers == []


def test_enrich_transient_flake_blockers_noop_without_pr_number() -> None:
    blockers: list[Blocker] = []
    merge_gate.enrich_transient_flake_blockers(
        blockers, merge_recovery.MergeBlockClass.TRANSIENT_FLAKE, None,
        DEFAULT_FORGE_TARGET,
    )
    assert blockers == []


def test_enrich_transient_flake_blockers_names_failing_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch, "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (e2e_failures.FailingE2ETest(spec="e2e/x.spec.ts", title="renders"),),
    )
    blockers: list[Blocker] = []
    merge_gate.enrich_transient_flake_blockers(
        blockers, merge_recovery.MergeBlockClass.TRANSIENT_FLAKE, 42,
        DEFAULT_FORGE_TARGET,
    )
    assert len(blockers) == 1
    assert blockers[0].kind == "failing_test"
    assert blockers[0].name == "e2e/x.spec.ts"


def test_enrich_transient_flake_blockers_unavailable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    blockers: list[Blocker] = []
    merge_gate.enrich_transient_flake_blockers(
        blockers, merge_recovery.MergeBlockClass.TRANSIENT_FLAKE, 42,
        DEFAULT_FORGE_TARGET,
    )
    assert len(blockers) == 1
    assert blockers[0].name == "e2e_detail_unavailable"


def test_render_remediation_instructions_includes_block_class_reasons_and_blockers() -> None:
    blockers = (
        Blocker(kind="failing_check", name="ci/test", detail="check failing: ci/test"),
    )
    text = merge_gate.render_remediation_instructions(
        merge_recovery.MergeBlockClass.GENUINE, "failing checks: ci/test", blockers,
    )
    assert "genuine" in text
    assert "failing checks: ci/test" in text
    assert "check failing: ci/test" in text


def test_render_remediation_instructions_omits_reasons_line_when_blank() -> None:
    text = merge_gate.render_remediation_instructions(
        merge_recovery.MergeBlockClass.GENUINE, "", (),
    )
    assert "Reasons:" not in text


def test_render_remediation_instructions_includes_blocker_recipe_instruction() -> None:
    blockers = (
        Blocker(
            kind="reason", name="unblock", detail="sonar hotspot review required",
            instruction="Open the SonarCloud security-hotspots view.",
        ),
    )
    text = merge_gate.render_remediation_instructions(
        merge_recovery.MergeBlockClass.SONAR_HOTSPOT, "", blockers,
    )
    assert "Open the SonarCloud security-hotspots view." in text


def test_enrich_conflict_blockers_strips_pending_check_kind() -> None:
    blockers: list[Blocker] = [
        Blocker(kind="pending_check", name="ci/test", detail="check pending: ci/test"),
        Blocker(kind="review_decision", name="APPROVED", detail="PR review decision: APPROVED"),
    ]
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT)
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    assert [b.kind for b in blockers] == ["review_decision", "reason"]


def test_enrich_conflict_blockers_strips_pending_checks_failed_reason() -> None:
    blockers: list[Blocker] = [
        Blocker(
            kind="failed_reason", name="pending checks: ci/test",
            detail="pending checks: ci/test",
        ),
        Blocker(
            kind="failed_reason", name="mergeStateStatus=DIRTY",
            detail="mergeStateStatus=DIRTY",
        ),
    ]
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT)
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    assert [b.name for b in blockers if b.kind == "failed_reason"] == ["mergeStateStatus=DIRTY"]


def test_enrich_conflict_blockers_appends_conflicted_paths() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT,
        conflicted_paths=("alan/foo.py", "alan/bar.py"),
    )
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    conflicted = [b for b in blockers if b.kind == "conflicted_path"]
    assert [b.name for b in conflicted] == ["alan/foo.py", "alan/bar.py"]
    assert not any(b.name == "conflicted_paths_truncated" for b in blockers)


def test_enrich_conflict_blockers_caps_and_truncates() -> None:
    blockers: list[Blocker] = []
    paths = tuple(f"alan/file{i}.py" for i in range(12))
    rec = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT, conflicted_paths=paths,
    )
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    conflicted = [b for b in blockers if b.kind == "conflicted_path"]
    assert len(conflicted) == 10
    truncated = next(b for b in blockers if b.name == "conflicted_paths_truncated")
    assert "2" in truncated.detail


def test_enrich_conflict_blockers_no_conflicted_paths_adds_no_conflicted_blockers() -> None:
    blockers: list[Blocker] = []
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT)
    merge_gate.enrich_conflict_blockers(blockers, rec, "deadbeef")
    assert [b.kind for b in blockers] == ["reason"]


def test_reconcile_outcome_str_none_for_none_rec() -> None:
    assert merge_gate.reconcile_outcome_str(None) is None


def test_reconcile_outcome_str_reads_outcome_value() -> None:
    rec = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RECONCILED)
    assert merge_gate.reconcile_outcome_str(rec) == "reconciled"

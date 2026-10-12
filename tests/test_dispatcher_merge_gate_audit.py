from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _FLAKY_GATE_NAME,
    _build_dispatcher_with_pr_merge,
    _pr_merge_handover,
    _read_merge_gate_audit,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, gh_unreachable


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_pr_merge_gate_audit_recovered_writes_pr_merge_outcome(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    _active_fake_forge: FakeForge,
) -> None:
    from alan.teams import _pr_branch, _pr_ready

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("mg-audit-recovered", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BEHIND",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=BEHIND",), check_count=1,
        ),
    )
    always(_active_fake_forge, "request_branch_update", api_result())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "behind_plus_transient"
    assert rows[0]["recovered"] is True
    assert rows[0]["outcome"] == "pr_merge"
    assert rows[0]["pr_number"] == 99


def test_pr_merge_gate_audit_blocked_writes_impl_fix_outcome(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-audit-blocked", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "genuine"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "implementation_fix"


def test_pr_merge_gate_audit_still_computing_writes_pr_merge_outcome(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-audit-computing", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            pending_checks=("ci/test",),
            failed_reasons=("pending checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "still_computing"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "pr_merge"


def test_pr_merge_gate_audit_stale_review_dismiss_fails_writes_pr_merge_retry(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("mg-audit-stale-fail", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",),
            check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "stale_review"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "pr_merge"


def test_pr_merge_gate_audit_stale_review_remediation_cap_exhausted_writes_await_human(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("mg-audit-stale-cap", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",),
            check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "stale_review"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "await_human"


def test_pr_merge_gate_audit_dirty_reconciled_writes_reconcile_outcome_field(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_reconcile

    ws = workspace_mod.create("mg-audit-dirty-reconciled", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="DIRTY",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",),
        ),
    )
    reconciled_result = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RECONCILED)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.DIRTY, reconciled_result),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "dirty"
    assert rows[0]["recovered"] is True
    assert rows[0]["outcome"] == "pr_merge"
    assert rows[0]["reconcile_outcome"] == "reconciled"


def test_pr_merge_gate_audit_dirty_conflict_terminal_writes_implementation_fix_outcome(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_reconcile

    ws = workspace_mod.create("mg-audit-dirty-conflict", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="DIRTY",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",),
        ),
    )
    conflict_result = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, conflict_result),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "dirty"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "implementation_fix"
    assert rows[0]["reconcile_outcome"] == "conflict"


def test_pr_merge_gate_audit_omits_reconcile_outcome_for_non_dirty_classes(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-audit-no-reconcile-field", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=BLOCKED",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert "reconcile_outcome" not in rows[0]


def test_pr_merge_gate_audit_post_update_red_writes_impl_fix_outcome(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_ready, _pr_state_paths

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("mg-audit-post-update-red", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _pr_state_paths.save_system_head(99, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("failing checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "post_update_red"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "implementation_fix"


def test_pr_merge_gate_audit_transient_flake_exhausted_writes_pr_merge_retry(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("mg-audit-flake-exhausted", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "transient_flake"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "pr_merge"


def test_pr_merge_gate_audit_unavailable_readiness_writes_no_gate_or_recovery_row(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    _active_fake_forge: FakeForge,
) -> None:
    from alan.teams import _pr_audit_log, _pr_freshness, _pr_ready

    ws = workspace_mod.create("mg-audit-unavailable", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    gh_unreachable()
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.ReadinessUnavailable("gh not installed"),
    )
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert _read_merge_gate_audit(tmp_path) == []
    impl_audit = _pr_audit_log.audit_dir() / _pr_audit_log.AUDIT_FILE
    assert impl_audit.parent == tmp_path / "logs"
    recovery_rows = (
        [line for line in impl_audit.read_text().splitlines() if "merge_recovery_attempt" in line]
        if impl_audit.exists()
        else []
    )
    assert recovery_rows == []

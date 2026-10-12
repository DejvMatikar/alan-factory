from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _build_dispatcher_with_pr_merge,
    _monkeypatch_unready_readiness,
    _pr_merge_handover,
)


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


@pytest.fixture(autouse=True)
def _isolated_pr_state_root(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.isolate_pr_state_root(monkeypatch)


def _ready_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams import _pr_ready

    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=True,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
        ),
    )


def test_reverse_direction_blocks_a_stale_pr_after_a_baseline_moved_on_main(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_branch, _pr_freshness

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-baseline-stale", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _ready_readiness(monkeypatch)
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.UPDATED,
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0
    assert recording.called == 0
    assert all(j.team != "implementation_fix" for j in queue.list_jobs())


def test_unaffected_pr_merges_without_an_extra_update_or_wait(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_branch, _pr_freshness

    ws = workspace_mod.create("pr-gate-baseline-unaffected", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _ready_readiness(monkeypatch)
    calls: list[int] = []

    def _probe(*_a: object, **_kw: object) -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", _probe)

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("must not attempt any recovery for an unaffected PR")

    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert calls == [1]
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is None
    assert recording.called == 0


def test_probe_is_not_called_when_readiness_already_failed(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_freshness, _pr_ready

    ws = workspace_mod.create("pr-gate-baseline-already-failed", "d", root=root)
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

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("freshness probe must not run when readiness already failed")

    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]


def test_conflicting_update_never_dispatches_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_branch, _pr_freshness, _pr_state_paths

    ws = workspace_mod.create("pr-gate-baseline-conflict", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _ready_readiness(monkeypatch)
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.CONFLICT,
    )
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "stale_measured_baseline_conflict"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_revisit_cap_exhaustion_routes_to_await_human_with_the_stale_baseline_recipe(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_branch, _pr_freshness, _pr_state_paths

    ws = workspace_mod.create("pr-gate-baseline-cap", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _ready_readiness(monkeypatch)
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_kw: _pr_branch.BranchUpdateOutcome.FAILED,
    )
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "stale_measured_baseline"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" not in [j.team for j in queue.list_jobs()]
    assert recording.called == 0
    unblock_blocker = next(
        b for b in reloaded.meta.blockers if b.kind == "reason" and b.name == "unblock"
    )
    assert unblock_blocker.instruction is not None
    assert "re-dispatch pr_merge" in unblock_blocker.instruction
    assert "measured-baseline" in unblock_blocker.detail


def test_recovered_stale_baseline_reuses_the_existing_ci_reset_delay(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass

    ws = workspace_mod.create("pr-gate-baseline-reset-delay", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.STALE_MEASURED_BASELINE, None),
    )
    _monkeypatch_unready_readiness(monkeypatch)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0

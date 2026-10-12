from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import dispatcher as dispatcher_mod
from alan import workspace as workspace_mod
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _build_dispatcher_with_pr_merge, _pr_merge_handover


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


def _no_check_results_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams import _pr_ready

    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failed_reasons=("no check results",),
            check_count=0,
        ),
    )


def test_no_check_results_is_a_bounded_retry_remediation_class() -> None:
    assert (
        MergeBlockClass.NO_CHECK_RESULTS
        in dispatcher_mod._BOUNDED_RETRY_REMEDIATION_CLASSES  # noqa: SLF001
    )


def test_gate_hit_re_enqueues_pr_merge_and_bumps_retry_counter(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_state_paths

    ws = workspace_mod.create("pr-gate-no-check-results", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _no_check_results_readiness(monkeypatch)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    bumped: list[int] = []
    monkeypatch.setattr(
        _pr_state_paths, "bump_remediation_retry_count", lambda p, _f: bumped.append(p)
    )
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0
    assert bumped == [99]
    assert recording.called == 0
    assert all(j.team != "implementation_fix" for j in queue.list_jobs())


def test_revisit_cap_exhaustion_awaits_human_with_no_check_results_block_class(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_state_paths

    ws = workspace_mod.create("pr-gate-no-check-results-cap", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _no_check_results_readiness(monkeypatch)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "no_check_results"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" not in [j.team for j in queue.list_jobs()]
    assert recording.called == 0
    unblock_blocker = next(
        b for b in reloaded.meta.blockers if b.kind == "reason" and b.name == "unblock"
    )
    assert unblock_blocker.instruction is not None
    assert "re-dispatch pr_merge" in unblock_blocker.instruction

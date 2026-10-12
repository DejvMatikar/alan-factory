from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import deploy_gate_sweep, deploy_observe, deploy_signal
from alan.deploy_verify import DeployObservation, GateRecord
from alan.dispatcher import Dispatcher
from alan.handover import DeployWaitSignal, Handover
from alan.queue import JobStatus, Queue
from alan.teams import _deploy_restart_probe
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_teams import _DeployVerifyNoopTeam, _PrMergeTerminalTeam


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_ceiling_gate_promotes_and_becomes_claimable_once_signal_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge_sha = "d" * 40
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (False, "hot_paths", merge_sha),
    )
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: False)

    root = tmp_path / "workspaces"
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        ws = create("gate-lifecycle", "d", root=root)
        dispatcher = Dispatcher(
            queue,
            {"pr_merge": _PrMergeTerminalTeam(), "deploy_verify": _DeployVerifyNoopTeam()},
            workspaces_root=root,
        )
        queue.enqueue(ws.task_id, "pr_merge")
        dispatcher.run_once()

        dv_jobs = [j for j in queue.list_jobs(task_id=ws.task_id) if j.team == "deploy_verify"]
        assert len(dv_jobs) == 1
        job = dv_jobs[0]
        assert job.status == JobStatus.PENDING
        assert job.eligible_at is not None
        assert job.eligible_at > time.time()
        record = GateRecord.from_json(job.instructions)
        assert record is not None
        assert record.path == "ceiling"
        assert record.merge_sha == merge_sha

        assert queue.claim() is None
        assert deploy_gate_sweep.promote_covered_jobs(queue) == 0

        monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: sha == merge_sha)
        assert deploy_gate_sweep.promote_covered_jobs(queue) == 1

        reloaded = queue.get_job(job.id)
        assert reloaded is not None
        assert reloaded.eligible_at is not None
        assert reloaded.eligible_at <= time.time() + 1.0
        record_after = GateRecord.from_json(reloaded.instructions)
        assert record_after is not None
        assert record_after.path == "signal"
        assert record_after.merge_sha == merge_sha

        claimed = queue.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        queue.close()


class _DeployVerifyWaitTeam:
    name = "deploy_verify"
    supports_resume = False
    restart_rederivable = True

    def __init__(self, merge_sha: str, merged_at: float) -> None:
        self._merge_sha = merge_sha
        self._merged_at = merged_at

    def plan_workflow(
        self, _workspace: Workspace, _prior: object
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: object,
        _emit: object,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: object = None,
        task_id: str | None = None,
    ) -> Handover:
        del resume_inv_id, instructions, on_proc_started, task_id
        return Handover(
            handover_id="h_dv_wait",
            handover_type="terminal",
            task_id=workspace.task_id,
            from_team="deploy_verify",
            from_invocation_id=inv_id,
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="confirming the merge is live",
            deploy_wait=DeployWaitSignal(
                merge_sha=self._merge_sha,
                deadline=time.time() + 5400.0,
                race_deadline=None,
                reason="confirming the merge is live",
                merged_at=self._merged_at,
            ),
        )


def test_merged_wait_reparks_as_awaiting_live_and_sweep_promotes_on_local_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge_sha = "f" * 40
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", merge_sha),
    )
    merged_at = time.time() - 30.0

    root = tmp_path / "workspaces"
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        ws = create("gate-lifecycle-deploy-run", "d", root=root)
        dispatcher = Dispatcher(
            queue,
            {
                "pr_merge": _PrMergeTerminalTeam(),
                "deploy_verify": _DeployVerifyWaitTeam(merge_sha, merged_at),
            },
            workspaces_root=root,
        )
        queue.enqueue(ws.task_id, "pr_merge")
        dispatcher.run_once()
        dispatcher.run_once()

        dv_jobs = [
            j
            for j in queue.list_jobs(task_id=ws.task_id)
            if j.team == "deploy_verify" and j.status == JobStatus.PENDING
        ]
        assert len(dv_jobs) == 1
        job = dv_jobs[0]
        record = GateRecord.from_json(job.instructions)
        assert record is not None
        assert record.path == "awaiting_live"
        assert record.merged_at == merged_at

        monkeypatch.setattr(
            deploy_observe,
            "observe",
            lambda sha, *, health_url: DeployObservation(
                arm="restart_free",
                checkout_contains_merge=True,
                slot_contains_merge=True,
                served_contains_merge=False,
                health_ok=True,
            ),
        )
        assert deploy_gate_sweep.promote_covered_jobs(queue) == 1

        reloaded = queue.get_job(job.id)
        assert reloaded is not None
        assert reloaded.eligible_at is not None
        assert reloaded.eligible_at <= time.time() + 1.0

        claimed = queue.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        queue.close()


def test_awaiting_live_job_not_promoted_while_local_observation_is_not_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge_sha = "f" * 40
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", merge_sha),
    )
    merged_at = time.time() - 30.0

    root = tmp_path / "workspaces"
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        ws = create("gate-lifecycle-deploy-run-notlive", "d", root=root)
        dispatcher = Dispatcher(
            queue,
            {
                "pr_merge": _PrMergeTerminalTeam(),
                "deploy_verify": _DeployVerifyWaitTeam(merge_sha, merged_at),
            },
            workspaces_root=root,
        )
        queue.enqueue(ws.task_id, "pr_merge")
        dispatcher.run_once()
        dispatcher.run_once()

        monkeypatch.setattr(
            deploy_observe,
            "observe",
            lambda sha, *, health_url: DeployObservation(
                arm="restart_free",
                checkout_contains_merge=False,
                slot_contains_merge=False,
                served_contains_merge=False,
                health_ok=True,
            ),
        )
        assert deploy_gate_sweep.promote_covered_jobs(queue) == 0
    finally:
        queue.close()


def test_restart_free_merge_is_claimable_immediately_no_ceiling_involved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", "e" * 40),
    )

    def _fail_if_called(sha: str) -> bool:
        raise AssertionError("merge_is_served must not be consulted on the restart-free path")

    monkeypatch.setattr(deploy_signal, "merge_is_served", _fail_if_called)

    root = tmp_path / "workspaces"
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        ws = create("gate-lifecycle-free", "d", root=root)
        dispatcher = Dispatcher(
            queue,
            {"pr_merge": _PrMergeTerminalTeam(), "deploy_verify": _DeployVerifyNoopTeam()},
            workspaces_root=root,
        )
        queue.enqueue(ws.task_id, "pr_merge")
        dispatcher.run_once()

        dv_jobs = [j for j in queue.list_jobs(task_id=ws.task_id) if j.team == "deploy_verify"]
        assert len(dv_jobs) == 1
        job = dv_jobs[0]
        assert job.eligible_at is None
        record = GateRecord.from_json(job.instructions)
        assert record is not None
        assert record.path == "restart_free"

        claimed = queue.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        queue.close()

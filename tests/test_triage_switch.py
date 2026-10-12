from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from alan import default_profile, triage_switch
from alan import reconciler as reconciler_mod
from alan import workspace as workspace_mod
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config import Posture
from alan.queue import JobStatus, Queue
from alan.teams.triage import TriageTeam
from alan.workspace import Workspace

_OWNER = "acme"
_REPO = "widget"


def _profile(*, triage_sweep: bool) -> DefaultProfile:
    return DefaultProfile(
        owner=_OWNER, repo=_REPO, posture=Posture.autonomous, triage_sweep=triage_sweep
    )


def _install(
    monkeypatch: pytest.MonkeyPatch, *, triage_sweep: bool, source: Source = Source.profile
) -> None:
    resolved = Resolved(_profile(triage_sweep=triage_sweep), source)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


def _triage_ws(ws_root: Path) -> Workspace:
    return next(
        ws for ws in workspace_mod.list_workspaces(root=ws_root) if ws.meta.team == "triage"
    )


def test_triage_sweep_enabled_reads_only_the_resolved_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, triage_sweep=True)
    assert default_profile.triage_sweep_enabled() is True
    _install(monkeypatch, triage_sweep=False)
    assert default_profile.triage_sweep_enabled() is False


def test_off_with_no_prior_state_seeds_nothing(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, triage_sweep=False)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert workspace_mod.list_workspaces(root=ws_root) == []
    assert queue.list_jobs() == []


def test_off_removes_a_rescheduled_deferred_job(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    queue.enqueue(ws.task_id, "triage", eligible_at=time.time() + 3600)
    _install(monkeypatch, triage_sweep=False)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert queue.list_jobs(task_id=ws.task_id) == []
    assert workspace_mod.read(ws.task_id, root=ws_root) is not None


def test_off_removes_a_pending_job(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    queue.enqueue(ws.task_id, "triage")
    _install(monkeypatch, triage_sweep=False)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_off_removes_a_resumable_failed_job(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    job = queue.enqueue(ws.task_id, "triage")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "boom", resume_inv_id="inv_001")
    _install(monkeypatch, triage_sweep=False)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_off_never_holds_or_pauses_the_workspace(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    queue.enqueue(ws.task_id, "triage")
    _install(monkeypatch, triage_sweep=False)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert queue.is_task_paused(ws.task_id) is False
    assert queue.list_paused_tasks() == []


def test_off_swallows_a_listing_failure_and_logs(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(*, root: Path | None = None) -> list[Workspace]:
        raise RuntimeError("disk exploded")

    _install(monkeypatch, triage_sweep=False)
    monkeypatch.setattr(workspace_mod, "list_workspaces", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.triage_switch"):
        triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert any("failed to clear triage jobs" in r.message for r in caplog.records)


def test_off_logs_the_removed_count(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    queue.enqueue(ws.task_id, "triage")
    _install(monkeypatch, triage_sweep=False)
    with caplog.at_level(logging.INFO, logger="alan.triage_switch"):
        triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert any("removed 1 job" in r.getMessage() for r in caplog.records)


def test_on_delegates_to_reconciler_seed_triage_sweep_through_the_module_attribute(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Queue, Path | None]] = []

    def _fake_seed(*, queue: Queue, workspaces_root: Path | None) -> None:
        calls.append((queue, workspaces_root))

    monkeypatch.setattr(reconciler_mod, "seed_triage_sweep", _fake_seed)
    _install(monkeypatch, triage_sweep=True)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert calls == [(queue, ws_root)]


def test_on_seeds_a_workspace_with_one_pending_job_still_subject_to_the_readiness_hold(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, triage_sweep=True)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    triage_ws = _triage_ws(ws_root)
    jobs = queue.list_jobs(task_id=triage_ws.task_id)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.PENDING


def test_unconfigured_install_seeds_and_runs_nothing(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = Resolved(default_profile.UNCONFIGURED_PROFILE, Source.unconfigured)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    triage_switch.start_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert workspace_mod.list_workspaces(root=ws_root) == []
    assert queue.list_jobs() == []


def test_schedule_next_sweep_skips_reschedule_when_switch_off(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    _install(monkeypatch, triage_sweep=False)
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_schedule_next_sweep_reschedules_when_switch_on(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    _install(monkeypatch, triage_sweep=True)
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1

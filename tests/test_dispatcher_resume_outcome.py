from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan._dispatcher_resume_outcome import record_terminal_resume_outcome
from alan.dispatcher import Dispatcher
from alan.queue import Job, JobStatus, Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_teams import _PrMergeTerminalTeam


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


def _job(*, job_id: int, resume_inv_id: str | None) -> Job:
    return Job(
        id=job_id, task_id="t1", workspace_id="t1", team="verification",
        status=JobStatus.RUNNING, enqueued_at=datetime.now(UTC), resume_inv_id=resume_inv_id,
    )


def _audit_rows(logs_root: Path) -> list[dict[str, object]]:
    audit = logs_root / "resume_audit.jsonl"
    if not audit.exists():
        return []
    return [json.loads(ln) for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_record_terminal_resume_outcome_is_noop_without_resume_inv_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    job = _job(job_id=1, resume_inv_id=None)
    record_terminal_resume_outcome(job, succeeded=True)
    assert _audit_rows(logs_dir) == []


def test_record_terminal_resume_outcome_writes_resume_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    job = _job(job_id=2, resume_inv_id="inv_005")
    record_terminal_resume_outcome(job, succeeded=True)
    rows = _audit_rows(logs_dir)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "resume_succeeded"
    assert rows[0]["inv_id"] == "inv_005"
    assert rows[0]["team"] == "verification"
    assert rows[0]["job_id"] == 2


def test_record_terminal_resume_outcome_writes_resume_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    job = _job(job_id=3, resume_inv_id="inv_006")
    record_terminal_resume_outcome(job, succeeded=False)
    rows = _audit_rows(logs_dir)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "resume_failed"


def test_fail_job_records_resume_failed_when_job_carries_resume_inv_id(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    workspace_mod.create("resume-fail-ws", "d", root=root)
    job = queue.enqueue("resume-fail-ws", "verification")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_resume_target")
    claimed = queue.claim()
    assert claimed is not None
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(claimed, "boom")  # noqa: SLF001
    rows = _audit_rows(logs_dir)
    assert any(r["outcome"] == "resume_failed" and r["inv_id"] == "inv_resume_target" for r in rows)


def test_fail_job_records_nothing_when_job_has_no_resume_inv_id(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    workspace_mod.create("plain-fail-ws", "d", root=root)
    queue.enqueue("plain-fail-ws", "verification")
    claimed = queue.claim()
    assert claimed is not None
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(claimed, "boom")  # noqa: SLF001
    assert _audit_rows(logs_dir) == []


def test_route_after_handover_records_resume_succeeded_on_completion(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    ws = workspace_mod.create("resume-success-ws", "d", root=root)
    job = queue.enqueue(ws.task_id, "pr_merge", workspace_id=ws.workspace_id)
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_resume_success")
    d = Dispatcher(queue, {"pr_merge": _PrMergeTerminalTeam()}, workspaces_root=root)
    d.run_once()
    rows = _audit_rows(logs_dir)
    assert any(
        r["outcome"] == "resume_succeeded" and r["inv_id"] == "inv_resume_success" for r in rows
    )
    finished = queue.list_jobs(task_id=ws.task_id)[0]
    assert finished.status is JobStatus.COMPLETED

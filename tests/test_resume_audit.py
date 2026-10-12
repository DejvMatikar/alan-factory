from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import reconciler
from alan.queue import Job, JobStatus, Queue
from alan.teams._resume_audit import record_resume_outcome


def _read_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "outcome", ["attached", "attach_lost", "requeued_for_resume", "interrupted_failed"]
)
def test_record_resume_outcome_row_shape_per_outcome(
    tmp_path: Path, outcome: str
) -> None:
    path = tmp_path / "resume_audit.jsonl"
    record_resume_outcome(
        path, task_id="t1", team="implementation", inv_id="inv_001",
        outcome=outcome,  # type: ignore[arg-type]
        node_id="implementer",
        extra={"job_id": 7},
    )
    rows = _read_rows(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "t1"
    assert row["team"] == "implementation"
    assert row["inv_id"] == "inv_001"
    assert row["outcome"] == outcome
    assert row["node_id"] == "implementer"
    assert row["job_id"] == 7
    assert isinstance(row["timestamp_utc"], str)
    datetime.fromisoformat(row["timestamp_utc"])


def test_record_resume_outcome_omits_node_id_when_none(tmp_path: Path) -> None:
    path = tmp_path / "resume_audit.jsonl"
    record_resume_outcome(
        path, task_id="t1", team="pr_merge", inv_id="inv_002", outcome="requeued_for_resume",
    )
    row = _read_rows(path)[0]
    assert "node_id" not in row


def test_record_resume_outcome_extra_merges_over_base_fields(tmp_path: Path) -> None:
    path = tmp_path / "resume_audit.jsonl"
    record_resume_outcome(
        path, task_id="t1", team="pr_merge", inv_id="inv_002", outcome="requeued_for_resume",
        extra={"job_id": 9, "interruption_count": 3},
    )
    row = _read_rows(path)[0]
    assert row["job_id"] == 9
    assert row["interruption_count"] == 3


def test_node_attach_record_attach_audit_carries_team_for_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _node_attach

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _node_attach._record_attach_audit(  # noqa: SLF001
        "t1", "inv_001", "implementer", "attached", team="implementation"
    )
    row = _read_rows(tmp_path / "logs" / "resume_audit.jsonl")[0]
    assert row["team"] == "implementation"
    assert row["outcome"] == "attached"


def test_node_attach_record_attach_audit_carries_team_for_attach_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _node_attach

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _node_attach._record_attach_audit(  # noqa: SLF001
        "t1", "inv_001", "implementer", "attach_lost", team="verification"
    )
    row = _read_rows(tmp_path / "logs" / "resume_audit.jsonl")[0]
    assert row["team"] == "verification"
    assert row["outcome"] == "attach_lost"


def _make_job(**overrides: object) -> Job:
    base: dict[str, object] = dict(
        id=42, task_id="t1", team="research", status=JobStatus.RUNNING,
        workspace_id="ws-1", triggered_by_invocation_id="inv_001",
        interruption_count=1, requeue_count=0, enqueued_at=datetime.now(UTC),
    )
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def test_reconciler_record_resume_audit_preserves_job_id_and_interruption_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    job = _make_job()
    reconciler._record_resume_audit(job, "requeued_for_resume", "inv_001")  # noqa: SLF001
    row = _read_rows(tmp_path / "logs" / "resume_audit.jsonl")[0]
    assert row["job_id"] == 42
    assert row["interruption_count"] == 2
    assert row["outcome"] == "requeued_for_resume"
    assert row["inv_id"] == "inv_001"
    assert row["team"] == "research"


def test_reconciler_record_resume_audit_interrupted_failed_no_inv_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    job = _make_job()
    reconciler._record_resume_audit(job, "interrupted_failed")  # noqa: SLF001
    row = _read_rows(tmp_path / "logs" / "resume_audit.jsonl")[0]
    assert row["outcome"] == "interrupted_failed"
    assert row["inv_id"] == ""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path):  # type: ignore[no-untyped-def]
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        q.close()


def test_interrupted_failed_recorded_when_mark_interrupted_and_fail_succeeds(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.events import PubSub

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research", workspace_id=ws.workspace_id)
    queue.claim()

    report = reconciler.reconcile_on_startup(queue=queue, workspaces_root=root, pubsub=PubSub())
    assert report.interrupted == [job.id]
    rows = _read_rows(tmp_path / "logs" / "resume_audit.jsonl")
    outcomes = [r["outcome"] for r in rows]
    assert "interrupted_failed" in outcomes


def test_interrupted_failed_not_recorded_when_mark_interrupted_and_fail_raises(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.events import PubSub

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research", workspace_id=ws.workspace_id)
    queue.claim()

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(queue, "mark_interrupted_and_fail", _boom)
    report = reconciler.reconcile_on_startup(queue=queue, workspaces_root=root, pubsub=PubSub())
    assert report.errors
    audit_path = tmp_path / "logs" / "resume_audit.jsonl"
    if audit_path.exists():
        outcomes = [r["outcome"] for r in _read_rows(audit_path)]
        assert "interrupted_failed" not in outcomes

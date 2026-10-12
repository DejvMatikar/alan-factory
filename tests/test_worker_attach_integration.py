from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alan import reconciler
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli_journal import journal_err_path
from alan.events import PubSub
from alan.queue import JobStatus, Queue
from alan.teams._base import run_or_resume_node
from alan.workflow import PendingTaskSpec


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def test_reconcile_requeue_attach_preserves_invocation_identity(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    ws = workspace_mod.create("t1", "d", root=root)
    queue = Queue(db_path=tmp_path / "queue.db")

    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("research", role="research", model="sonnet")
    graph.set_session_id("research", "sess-integration")
    workflow_mod.save_workflow(ws, graph.doc)

    job = queue.enqueue(
        ws.task_id, "research", workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id,
    )
    queue.claim()

    stream_path = workflow_mod.stream_path(ws, inv_id, "research")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({"type": "result", "subtype": "success", "result": "attached body"}) + "\n"
    )
    journal_err_path(stream_path).touch()

    import subprocess

    from alan import worker_records
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)

    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=job.id, task_id=ws.task_id, workspace_id=ws.workspace_id, team="research",
            inv_id=inv_id, node_id="research", role="research", model="sonnet",
            engine="default", slot_path=str(tmp_path / "slot_0"), session_id="sess-integration",
            output_path=str(stream_path), pid=proc.pid, started_at=1.0, lstart=None, cmdline=None,
            deadline_at=None, idle_timeout_s=5.0, max_turns=150,
            service_pid=999999, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=424242, journal_offset=0, err_offset=0,
        )
    )

    report = reconciler.reconcile_on_startup(queue=queue, workspaces_root=root, pubsub=PubSub())
    assert report.resumed == [job.id]

    requeued = queue.get_job(job.id)
    assert requeued is not None
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id

    claimed = queue.claim()
    assert claimed is not None
    assert claimed.triggered_by_invocation_id == inv_id

    graph2 = workflow_mod.load_graph(ws, inv_id)
    assert graph2.doc.tasks[0].status == "interrupted"

    result = run_or_resume_node(
        ws, graph2, inv_id, lambda _e: None,
        node_id="research", role="research", model="sonnet", depends_on=[],
        system_prompt="sys", user_text="txt",
    )
    assert result == "attached body"
    assert worker_records.load_all() == []

    graph3 = workflow_mod.load_graph(ws, inv_id)
    assert graph3.doc.tasks[0].status == "success"

    from alan.roots import logs_root

    audit_lines = (logs_root() / "resume_audit.jsonl").read_text(encoding="utf-8").splitlines()
    outcomes = [json.loads(line)["outcome"] for line in audit_lines]
    assert "requeued_for_resume" in outcomes
    assert "attached" in outcomes
    queue.close()

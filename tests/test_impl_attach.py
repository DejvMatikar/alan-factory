from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from alan import worker_records
from alan import workflow as workflow_mod
from alan._dispatcher_base import ProcRegistry
from alan.claude_cli import AgentStreamingResult
from alan.claude_cli_journal import journal_err_path
from alan.teams import _node_attach
from alan.teams import _pr_handover as ph
from alan.teams._base import WorkerOverrides
from alan.workspace import Workspace, create

_THIS_SERVICE_STUB = (1, "Thu Jan  1 00:00:00 1970")


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("impl-attach-task", "d", root=tmp_path / "ws")


def _seed_interrupted_impl(
    workspace: Workspace, *, session_id: str
) -> tuple[str, workflow_mod.WorkflowGraph]:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", session_id)
    graph.mark_interrupted("implementer", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    return inv_id, workflow_mod.load_graph(workspace, inv_id)


def _write_survivor(
    *, session_id: str, task_id: str, inv_id: str, pid: int | None, pgid: int | None,
    node_id: str = "implementer", slot_path: str = "/fake/slot_0",
    idle_timeout_s: float | None = 5.0,
) -> None:
    identity = worker_records.ps_identity(pid) if pid is not None else None
    lstart, cmdline = identity if identity is not None else (None, None)
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id=task_id, workspace_id=task_id, team="implementation",
            inv_id=inv_id, node_id=node_id, role="implementer", model="sonnet",
            engine="default", slot_path=slot_path, session_id=session_id,
            output_path=f"{slot_path}/stream.jsonl",
            pid=pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=idle_timeout_s, max_turns=150,
            service_pid=_THIS_SERVICE_STUB[0], service_started_at=_THIS_SERVICE_STUB[1],
            host=worker_records.current_host(),
            pgid=pgid, journal_offset=0, err_offset=0,
        )
    )


def _run_or_resume(
    workspace: Workspace, graph: workflow_mod.WorkflowGraph, inv_id: str,
    *, effort: str | None = "xhigh",
) -> str:
    return ph.run_or_resume(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet", effort=effort,
        system_prompt="sys", user_text="txt",
        overrides=WorkerOverrides(), resume_inv_id=None,
    )


def test_attach_happy_path_no_second_claude_spawn(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_impl(workspace, session_id="sess-happy")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "implementer")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({"type": "result", "subtype": "success", "result": "attached body"}) + "\n"
    )
    journal_err_path(stream_path).touch()
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)
    _write_survivor(session_id="sess-happy", task_id=workspace.task_id, inv_id=inv_id,
                     pid=proc.pid, pgid=424242)
    monkeypatch.setattr(
        "alan.teams._base.run_agent_streaming",
        lambda *_a, **_kw: pytest.fail(
            "must not spawn a second claude while a survivor is attached"
        ),
    )
    result = _run_or_resume(workspace, graph, inv_id)
    assert result == "attached body"
    assert worker_records.load_all() == []
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.tasks[0].status == "success"


def test_attach_lost_falls_through_to_resume(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_impl(workspace, session_id="sess-lost")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "implementer")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.touch()
    journal_err_path(stream_path).touch()
    _write_survivor(session_id="sess-lost", task_id=workspace.task_id, inv_id=inv_id,
                     pid=999999, pgid=777, idle_timeout_s=0.05)
    monkeypatch.setattr(_node_attach, "abs_timeout_seconds", lambda: 100.0)

    resumed: list[str | None] = []

    def fake(
        system_prompt: str, user_text: str, *,
        resume_session_id: str | None = None, stream_path: Path | None = None, **_kw: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        resumed.append(resume_session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": "resumed body"}) + "\n")
        return AgentStreamingResult(result_text="resumed body")

    monkeypatch.setattr("alan.teams._base.run_agent_streaming", fake)
    result = _run_or_resume(workspace, graph, inv_id)
    assert result == "resumed body"
    assert resumed == ["sess-lost"]
    assert worker_records.load_all() == []


def test_stop_requested_against_attached_job_degrades_cleanly(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_impl(workspace, session_id="sess-stop")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "implementer")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.touch()
    journal_err_path(stream_path).touch()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True,
        start_new_session=True,
    )
    try:
        _write_survivor(
            session_id="sess-stop", task_id=workspace.task_id, inv_id=inv_id,
            pid=proc.pid, pgid=os.getpgid(proc.pid), idle_timeout_s=30.0,
        )
        assert ProcRegistry().terminate(1) is True
        proc.wait(timeout=5.0)

        monkeypatch.setattr(
            "alan.teams._base.run_agent_streaming",
            lambda *_a, **_kw: pytest.fail("must fall through to resume, not spawn fresh"),
        )

        def fake_resume(*_a: object, **_kw: object) -> str:
            return "resumed after stop"

        monkeypatch.setattr("alan.teams._base.resume_workflow_node", fake_resume)
        result = _run_or_resume(workspace, graph, inv_id)
        assert result == "resumed after stop"
        assert worker_records.load_all() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_degrade_terminates_live_survivor_before_resume_spawns_fresh(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_impl(workspace, session_id="sess-live")
    _write_survivor(session_id="sess-live", task_id=workspace.task_id, inv_id=inv_id,
                     pid=4242, pgid=4242)
    monkeypatch.setattr(
        workflow_mod, "stream_path",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk gone")),
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )
    monkeypatch.setattr(worker_records, "guard_no_group_signal", lambda pid: None)
    terminate_order: list[str] = []
    monkeypatch.setattr(
        worker_records, "terminate_group",
        lambda pgid: terminate_order.append(f"terminate:{pgid}"),
    )

    def fake_resume(*_a: object, **_kw: object) -> str:
        assert terminate_order == ["terminate:4242"]
        return "resumed after live degrade"

    monkeypatch.setattr("alan.teams._base.resume_workflow_node", fake_resume)
    result = _run_or_resume(workspace, graph, inv_id)
    assert result == "resumed after live degrade"
    assert terminate_order == ["terminate:4242"]


def test_degrade_does_not_terminate_when_survivor_not_live(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_impl(workspace, session_id="sess-dead")
    _write_survivor(session_id="sess-dead", task_id=workspace.task_id, inv_id=inv_id,
                     pid=None, pgid=5252)
    monkeypatch.setattr(
        workflow_mod, "stream_path",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk gone")),
    )
    terminate_order: list[str] = []
    monkeypatch.setattr(
        worker_records, "terminate_group",
        lambda pgid: terminate_order.append(f"terminate:{pgid}"),
    )

    def fake_resume(*_a: object, **_kw: object) -> str:
        return "resumed after not-live degrade"

    monkeypatch.setattr("alan.teams._base.resume_workflow_node", fake_resume)
    result = _run_or_resume(workspace, graph, inv_id)
    assert result == "resumed after not-live degrade"
    assert terminate_order == []


def test_cached_success_reuses_stored_result_without_reinvoking_model(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.complete_node("implementer")
    workflow_mod.save_workflow(workspace, graph.doc)
    workflow_mod.save_node_result(
        workspace, inv_id, "implementer", role="implementer", model="sonnet",
        result_text="cached synthesis",
    )
    monkeypatch.setattr(
        "alan.teams._base.run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-invoke the model for a cached success node"),
    )
    result = _run_or_resume(workspace, graph, inv_id)
    assert result == "cached synthesis"


def test_cached_success_missing_cache_raises_workflow_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.complete_node("implementer")
    workflow_mod.save_workflow(workspace, graph.doc)
    monkeypatch.setattr(
        "alan.teams._base.run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-invoke the model for a cached success node"),
    )
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        _run_or_resume(workspace, graph, inv_id)


def test_effort_passthrough_reaches_only_fresh_arm(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    captured: list[str | None] = []

    def fake(
        system_prompt: str, user_text: str, *,
        effort: str | None = None, stream_path: Path | None = None, **_kw: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        captured.append(effort)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": "fresh"}) + "\n")
        return AgentStreamingResult(result_text="fresh")

    monkeypatch.setattr("alan.teams._base.run_agent_streaming", fake)
    result = _run_or_resume(workspace, graph, inv_id, effort="xhigh")
    assert result == "fresh"
    assert captured == ["xhigh"]

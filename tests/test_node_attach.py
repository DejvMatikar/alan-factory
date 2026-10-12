from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alan import worker_records
from alan import workflow as workflow_mod
from alan.claude_cli import ClaudeCliError
from alan.claude_cli_journal import journal_err_path
from alan.teams import _node_attach
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create

_THIS_SERVICE_STUB = (1, "Thu Jan  1 00:00:00 1970")


class _NullHandle:
    def poll(self) -> int | None:
        return None


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("attach-task", "d", root=tmp_path / "ws")


def _seed_interrupted_node(
    workspace: Workspace, *, session_id: str
) -> tuple[str, workflow_mod.WorkflowGraph]:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="research",
        tasks=[PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("impl", role="impl", model="sonnet")
    graph.set_session_id("impl", session_id)
    graph.mark_interrupted("impl", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    return inv_id, workflow_mod.load_graph(workspace, inv_id)


def _write_survivor_record(
    *, session_id: str, task_id: str, inv_id: str, node_id: str,
    pid: int | None, pgid: int | None, slot_path: str = "/fake/slot_0",
    journal_offset: int = 0, err_offset: int = 0, idle_timeout_s: float | None = 5.0,
) -> None:
    identity = worker_records.ps_identity(pid) if pid is not None else None
    lstart, cmdline = identity if identity is not None else (None, None)
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id=task_id, workspace_id=task_id, team="research",
            inv_id=inv_id, node_id=node_id, role="impl", model="sonnet",
            engine="default", slot_path=slot_path, session_id=session_id,
            output_path=f"{slot_path}/stream.jsonl",
            pid=pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=idle_timeout_s, max_turns=150,
            service_pid=_THIS_SERVICE_STUB[0], service_started_at=_THIS_SERVICE_STUB[1],
            host=worker_records.current_host(),
            pgid=pgid, journal_offset=journal_offset, err_offset=err_offset,
        )
    )


def test_find_survivor_records_empty_when_no_records() -> None:
    assert _node_attach.find_survivor_records("t1", "inv_001", "impl") == []


def test_find_survivor_records_filters_by_task_inv_node(tmp_path: Path) -> None:
    _write_survivor_record(session_id="a", task_id="t1", inv_id="inv_001", node_id="impl",
                            pid=None, pgid=111)
    _write_survivor_record(session_id="b", task_id="t2", inv_id="inv_001", node_id="impl",
                            pid=None, pgid=111)
    matches = _node_attach.find_survivor_records("t1", "inv_001", "impl")
    assert [r.session_id for r in matches] == ["a"]


def test_find_survivor_records_excludes_non_detached_records() -> None:
    _write_survivor_record(session_id="a", task_id="t1", inv_id="inv_001", node_id="impl",
                            pid=None, pgid=None)
    assert _node_attach.find_survivor_records("t1", "inv_001", "impl") == []


def test_find_survivor_records_newest_first() -> None:
    _write_survivor_record(session_id="old", task_id="t1", inv_id="inv_001", node_id="impl",
                            pid=None, pgid=1)
    worker_records.stamp_started("old", pid=1, started_at=1.0, lstart=None, cmdline=None)
    _write_survivor_record(session_id="new", task_id="t1", inv_id="inv_001", node_id="impl",
                            pid=None, pgid=2)
    worker_records.stamp_started("new", pid=2, started_at=99.0, lstart=None, cmdline=None)
    matches = _node_attach.find_survivor_records("t1", "inv_001", "impl")
    assert [r.session_id for r in matches] == ["new", "old"]


def test_attach_returns_none_when_no_survivor(workspace: Workspace) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-none")
    result = _node_attach.attach(
        workspace, graph, inv_id, lambda _e: None, node_id="impl", task_id=workspace.task_id,
    )
    assert result is None


def test_attach_happy_path_completes_node(workspace: Workspace) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-happy")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "impl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({"type": "result", "subtype": "success", "result": "attached ok"}) + "\n"
    )
    journal_err_path(stream_path).touch()

    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)
    _write_survivor_record(
        session_id="sess-happy", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=proc.pid, pgid=424242,
    )
    events: list[TeamEvent] = []
    result = _node_attach.attach(
        workspace, graph, inv_id, events.append, node_id="impl", task_id=workspace.task_id,
    )
    assert result is not None
    assert result.completed is True
    assert result.result_text == "attached ok"
    assert worker_records.load_all() == []
    assert any(e.kind == "node_started" for e in events)

    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.tasks[0].status == "success"


def test_attach_error_result_raises_and_records_attached_audit(workspace: Workspace) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-err")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "impl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({
            "type": "result", "is_error": True,
            "subtype": "error_during_execution", "result": "boom",
        }) + "\n"
    )
    journal_err_path(stream_path).touch()

    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)
    _write_survivor_record(
        session_id="sess-err", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=proc.pid, pgid=424242,
    )
    with pytest.raises(ClaudeCliError):
        _node_attach.attach(
            workspace, graph, inv_id, lambda _e: None,
            node_id="impl", task_id=workspace.task_id,
        )
    assert worker_records.load_all() == []


def test_attach_error_result_folds_recorded_offset_stderr_into_message(
    workspace: Workspace,
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-err-stderr")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "impl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({
            "type": "result", "is_error": True,
            "subtype": "error_during_execution", "result": "boom",
        }) + "\n"
    )
    journal_err_path(stream_path).write_text("stale\n" + "traceback: kaboom\n")

    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)
    _write_survivor_record(
        session_id="sess-err-stderr", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=proc.pid, pgid=424242, err_offset=len("stale\n"),
    )
    with pytest.raises(ClaudeCliError, match="traceback: kaboom") as exc_info:
        _node_attach.attach(
            workspace, graph, inv_id, lambda _e: None,
            node_id="impl", task_id=workspace.task_id,
        )
    assert "stale" not in str(exc_info.value)


def test_attach_terminates_duplicate_records_keeping_newest(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-new")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "impl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.write_text(
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}) + "\n"
    )
    journal_err_path(stream_path).touch()

    _write_survivor_record(session_id="sess-old", task_id=workspace.task_id, inv_id=inv_id,
                            node_id="impl", pid=None, pgid=555)
    worker_records.stamp_started("sess-old", pid=1, started_at=1.0, lstart=None, cmdline=None)
    _write_survivor_record(session_id="sess-new", task_id=workspace.task_id, inv_id=inv_id,
                            node_id="impl", pid=999999, pgid=556)
    worker_records.stamp_started("sess-new", pid=999999, started_at=99.0, lstart=None, cmdline=None)

    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)

    result = _node_attach.attach(
        workspace, graph, inv_id, lambda _e: None, node_id="impl", task_id=workspace.task_id,
    )
    assert 555 in terminated
    assert result is not None


def test_terminate_duplicate_skips_guard_when_pid_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="sess-nopid",
        output_path="/fake/o", pid=None, started_at=1.0, lstart=None, cmdline=None,
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=999, journal_offset=0, err_offset=0,
    )
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    _node_attach._terminate_duplicate(record)  # noqa: SLF001
    assert terminated == [999]


def test_terminate_duplicate_refuses_when_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="sess-guarded",
        output_path="/fake/o", pid=42, started_at=1.0, lstart=None, cmdline=None,
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=999, journal_offset=0, err_offset=0,
    )

    def raise_guard(pid: int) -> None:
        raise worker_records.GroupSignalGuardError("nope")

    monkeypatch.setattr(worker_records, "guard_no_group_signal", raise_guard)
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    _node_attach._terminate_duplicate(record)  # noqa: SLF001
    assert terminated == []


def test_attach_degrades_when_deadline_expires_with_no_result(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-lost")
    stream_path = workflow_mod.stream_path(workspace, inv_id, "impl")
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    stream_path.touch()
    journal_err_path(stream_path).touch()

    _write_survivor_record(
        session_id="sess-lost", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=999999, pgid=777, idle_timeout_s=0.05,
    )
    monkeypatch.setattr(_node_attach, "abs_timeout_seconds", lambda: 100.0)

    result = _node_attach.attach(
        workspace, graph, inv_id, lambda _e: None, node_id="impl", task_id=workspace.task_id,
    )
    assert result is not None
    assert result.completed is False
    assert result.session_id == "sess-lost"
    assert worker_records.load_all() == []

    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.tasks[0].status == "interrupted"
    assert reloaded.doc.tasks[0].claude_session_id == "sess-lost"


def test_attach_unexpected_oserror_degrades_instead_of_raising(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-oserr")
    _write_survivor_record(
        session_id="sess-oserr", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=None, pgid=888,
    )

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(workflow_mod, "stream_path", boom)
    result = _node_attach.attach(
        workspace, graph, inv_id, lambda _e: None, node_id="impl", task_id=workspace.task_id,
    )
    assert result is not None
    assert result.completed is False


def test_abs_start_uses_recorded_deadline_when_present() -> None:
    from alan.clock import Epoch, Monotonic

    class _FixedClock:
        def monotonic(self) -> "Monotonic":
            return Monotonic(1000.0)

        def now(self) -> "Epoch":
            return Epoch(500.0)

    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=None, started_at=1.0, lstart=None, cmdline=None,
        deadline_at=600.0, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=None, journal_offset=0, err_offset=0,
    )
    result = _node_attach._abs_start(record, clock=_FixedClock(), abs_timeout=50.0)  # noqa: SLF001
    assert result == 1000.0 + (600.0 - 500.0) - 50.0


def test_remove_record_best_effort_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_session_id: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(worker_records, "remove_record", boom)
    _node_attach._remove_record_best_effort("sess-x")  # noqa: SLF001 - must not raise


def test_degrade_logs_when_workflow_save_fails(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-degrade-io")
    _write_survivor_record(
        session_id="sess-degrade-io", task_id=workspace.task_id, inv_id=inv_id, node_id="impl",
        pid=None, pgid=333,
    )

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(workflow_mod, "save_workflow", boom)
    record = worker_records.load_one("sess-degrade-io")
    assert record is not None
    result = _node_attach._degrade(  # noqa: SLF001
        workspace, graph, record, node_id="impl", inv_id=inv_id, task_id=workspace.task_id,
    )
    assert result.completed is False


def test_kill_on_deadline_returns_when_pgid_missing_despite_live_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=42, started_at=1.0, lstart="x", cmdline="y",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=None, journal_offset=0, err_offset=0,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    kill = _node_attach._kill_on_deadline(record, _THIS_SERVICE_STUB)  # noqa: SLF001
    kill(_NullHandle())
    assert terminated == []


def test_kill_on_deadline_terminates_group_when_live_and_guard_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=42, started_at=1.0, lstart="x", cmdline="y",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=1234, journal_offset=0, err_offset=0,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )
    monkeypatch.setattr(worker_records, "guard_no_group_signal", lambda pid: None)
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    kill = _node_attach._kill_on_deadline(record, _THIS_SERVICE_STUB)  # noqa: SLF001
    kill(_NullHandle())
    assert terminated == [1234]


def test_kill_on_deadline_refuses_when_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=42, started_at=1.0, lstart="x", cmdline="y",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=1234, journal_offset=0, err_offset=0,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )

    def raise_guard(pid: int) -> None:
        raise worker_records.GroupSignalGuardError("nope")

    monkeypatch.setattr(worker_records, "guard_no_group_signal", raise_guard)
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    kill = _node_attach._kill_on_deadline(record, _THIS_SERVICE_STUB)  # noqa: SLF001
    kill(_NullHandle())
    assert terminated == []


def test_kill_on_deadline_falls_through_when_pid_genuinely_vanished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait(timeout=5.0)
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=proc.pid, started_at=1.0, lstart="x", cmdline="y",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=proc.pid, journal_offset=0, err_offset=0,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )
    kill = _node_attach._kill_on_deadline(record, _THIS_SERVICE_STUB)  # noqa: SLF001
    kill(_NullHandle())


def test_record_attach_audit_includes_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _node_attach._record_attach_audit(  # noqa: SLF001
        "t1", "inv_001", "impl", "attached", team="research"
    )
    audit_path = tmp_path / "logs" / "resume_audit.jsonl"
    entry = json.loads(audit_path.read_text().strip())
    assert entry["team"] == "research"
    assert entry["outcome"] == "attached"
    assert entry["node_id"] == "impl"


def test_degrade_terminates_live_survivor_before_recording_attach_lost(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-degrade-live")
    _write_survivor_record(
        session_id="sess-degrade-live", task_id=workspace.task_id, inv_id=inv_id,
        node_id="impl", pid=42, pgid=4242,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.LIVE
    )
    monkeypatch.setattr(worker_records, "guard_no_group_signal", lambda pid: None)
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    record = worker_records.load_one("sess-degrade-live")
    assert record is not None
    result = _node_attach._degrade(  # noqa: SLF001
        workspace, graph, record, node_id="impl", inv_id=inv_id, task_id=workspace.task_id,
    )
    assert terminated == [4242]
    assert result.completed is False
    assert worker_records.load_all() == []


def test_degrade_does_not_terminate_when_survivor_not_live(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted_node(workspace, session_id="sess-degrade-dead")
    _write_survivor_record(
        session_id="sess-degrade-dead", task_id=workspace.task_id, inv_id=inv_id,
        node_id="impl", pid=999999, pgid=5252,
    )
    monkeypatch.setattr(
        worker_records, "probe", lambda *_a, **_kw: worker_records.ProbeVerdict.DEAD
    )
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    record = worker_records.load_one("sess-degrade-dead")
    assert record is not None
    result = _node_attach._degrade(  # noqa: SLF001
        workspace, graph, record, node_id="impl", inv_id=inv_id, task_id=workspace.task_id,
    )
    assert terminated == []
    assert result.completed is False
    assert worker_records.load_all() == []


def test_kill_on_deadline_noop_when_already_dead(workspace: Workspace) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION, job_id=1, task_id="t1",
        workspace_id="t1", team="research", inv_id="inv_001", node_id="impl", role="impl",
        model="sonnet", engine="default", slot_path="/fake", session_id="s",
        output_path="/fake/o", pid=999999, started_at=1.0, lstart="x", cmdline="y",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="x", host=worker_records.current_host(),
        pgid=1234, journal_offset=0, err_offset=0,
    )
    kill = _node_attach._kill_on_deadline(record, _THIS_SERVICE_STUB)  # noqa: SLF001
    kill(_NullHandle())

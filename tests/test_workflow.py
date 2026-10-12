from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alan import workflow as workflow_mod
from alan.workflow import (
    INTERRUPTED_BY_RESTART_ERROR,
    LLMSTEP_TYPE,
    SCHEMA_VERSION,
    NodeUsage,
    WorkflowDoc,
    WorkflowError,
    WorkflowGraph,
    WorkflowTask,
    agent_meta_path,
    create_invocation_dir,
    list_invocations,
    load_graph,
    load_workflow,
    load_workflow_cached,
    save_workflow,
    seed_pending_workflow,
    stream_path,
    workflow_path,
)
from alan.workflow import PendingTaskSpec as _PendingTaskSpec
from alan.workspace import Workspace, create
from tests.fs_timestamp_support import bump_mtime


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path, title: str = "t1") -> Workspace:
    return create(title, "desc", root=ws_root)


def test_schema_constants() -> None:
    assert SCHEMA_VERSION == "1.7"
    assert LLMSTEP_TYPE == "io.alan.LLMStep"


def test_graph_start_node_records_metadata() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    node = g.start_node("orchestrator", role="orchestrator", model="opus")
    assert node.id == "orchestrator"
    assert node.role == "orchestrator"
    assert node.model == "opus"
    assert node.status == "running"
    assert node.started_at is not None
    assert node.completed_at is None
    assert node.depends_on == []
    assert node.stream_path == "agents/orchestrator.stream.jsonl"
    assert node.effort is None
    assert g.doc.tasks == [node]


def test_graph_start_node_records_effort() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="implementation")
    node = g.start_node(
        "implementer", role="implementer", model="sonnet", effort="xhigh"
    )
    assert node.effort == "xhigh"


def test_graph_start_node_with_depends_on() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("a", role="a", model="sonnet")
    node = g.start_node("b", role="b", model="sonnet", depends_on=["a"])
    assert node.depends_on == ["a"]


def test_graph_start_node_rejects_duplicate() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("a", role="a", model="sonnet")
    with pytest.raises(WorkflowError, match="duplicate node id"):
        g.start_node("a", role="a", model="sonnet")


def test_graph_complete_node_sets_success_and_dispatched() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("orch", role="orch", model="opus")
    done = g.complete_node("orch", dispatched=["w1", "w2"])
    assert done.status == "success"
    assert done.completed_at is not None
    assert done.dispatched == ["w1", "w2"]


def test_graph_complete_node_without_dispatched_keeps_empty() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    done = g.complete_node("w")
    assert done.dispatched == []


def test_graph_complete_node_unknown_raises() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    with pytest.raises(WorkflowError, match="unknown node"):
        g.complete_node("missing")


def test_graph_fail_node_sets_failed() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    done = g.fail_node("w")
    assert done.status == "failed"
    assert done.completed_at is not None
    assert done.error is None


def test_graph_fail_node_records_error_message() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    done = g.fail_node("w", error="claude CLI timed out after 120s")
    assert done.status == "failed"
    assert done.error == "claude CLI timed out after 120s"


def test_graph_fail_node_unknown_raises() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    with pytest.raises(WorkflowError, match="unknown node"):
        g.fail_node("missing")


def test_graph_finalize_sets_status_and_completed() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    doc = g.finalize(status="success")
    assert doc.status == "success"
    assert doc.completed_at is not None


def test_graph_doc_namespace_uses_task_id() -> None:
    g = WorkflowGraph(task_id="abc", inv_id="inv_001", team="research")
    assert g.doc.namespace == "alan.workspace.abc"


def test_workflow_graph_init_has_usable_lock() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    assert isinstance(g.lock, type(threading.Lock()))
    assert g.lock.acquire(blocking=False)
    g.lock.release()


def test_workflow_graph_from_doc_has_usable_lock() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.finalize(status="success")
    graph2 = WorkflowGraph.from_doc(g.doc)
    assert isinstance(graph2.lock, type(threading.Lock()))
    assert graph2.lock.acquire(blocking=False)
    graph2.lock.release()


def test_workflow_graph_from_doc_lock_is_independent_instance() -> None:
    g1 = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g2 = WorkflowGraph.from_doc(g1.doc)
    assert g1.lock is not g2.lock


_LOCK_RACE_ITERATIONS = 30


def test_graph_lock_no_lost_update_under_concurrent_start_and_complete(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root, "lock-race")
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="verification")
    start_barrier = threading.Barrier(2)
    complete_barrier = threading.Barrier(2)

    def worker(prefix: str) -> None:
        for i in range(_LOCK_RACE_ITERATIONS):
            node_id = f"{prefix}_{i}"
            start_barrier.wait()
            with g.lock:
                g.start_node(node_id, role=prefix, model="sonnet")
                save_workflow(ws, g.doc)
            complete_barrier.wait()
            with g.lock:
                g.complete_node(node_id)
                save_workflow(ws, g.doc)

    threads = [
        threading.Thread(target=worker, args=(prefix,))
        for prefix in ("diff_reviewer", "scenario_runner")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reloaded = load_graph(ws, inv_id)
    expected_ids = {
        f"{prefix}_{i}"
        for prefix in ("diff_reviewer", "scenario_runner")
        for i in range(_LOCK_RACE_ITERATIONS)
    }
    assert {t.id for t in reloaded.doc.tasks} == expected_ids
    assert all(t.status == "success" for t in reloaded.doc.tasks)


def test_workflowtask_defaults() -> None:
    task = WorkflowTask(id="x", role="x", model="sonnet", status="pending")
    assert task.type == LLMSTEP_TYPE
    assert task.depends_on == []
    assert task.dispatched == []
    assert task.effort is None


def test_workflowdoc_schema_defaults() -> None:
    from datetime import UTC, datetime

    doc = WorkflowDoc(
        id="inv_001",
        namespace="ns",
        team="research",
        status="running",
        started_at=datetime.now(UTC),
    )
    assert doc.schema_version == SCHEMA_VERSION
    assert doc.tasks == []
    assert doc.completed_at is None
    assert doc.slot_index is None


def test_create_invocation_dir_first_slot(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    assert inv_id == "inv_001"
    assert (ws.path / "invocations" / "inv_001" / "agents").is_dir()


def test_create_invocation_dir_increments(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    assert create_invocation_dir(ws) == "inv_001"
    assert create_invocation_dir(ws) == "inv_002"
    assert create_invocation_dir(ws) == "inv_003"


def test_create_invocation_dir_exhausted(monkeypatch: pytest.MonkeyPatch, ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    monkeypatch.setattr(workflow_mod, "_MAX_INV_SLOT", 1)
    create_invocation_dir(ws)
    with pytest.raises(WorkflowError, match="no free invocation slot"):
        create_invocation_dir(ws)


def test_prune_invocation_dirs_removes_oldest_beyond_keep(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 2)
    for _ in range(4):
        create_invocation_dir(ws)
    base = ws.path / "invocations"
    (base / "stray.txt").write_text("not a slot", encoding="utf-8")
    for seq, mtime in enumerate([1.0, 2.0, 3.0, 4.0], start=1):
        os.utime(base / f"inv_{seq:03d}", (mtime, mtime))
    removed = workflow_mod.prune_invocation_dirs(ws)
    assert removed == 2
    names = sorted(p.name for p in base.iterdir())
    assert names == ["inv_003", "inv_004", "stray.txt"]


def test_prune_invocation_dirs_noop_under_keep(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    assert workflow_mod.prune_invocation_dirs(ws) == 0
    assert (ws.path / "invocations" / "inv_001").is_dir()


def test_prune_invocation_dirs_missing_dir_returns_zero(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    assert workflow_mod.prune_invocation_dirs(ws) == 0


def test_prune_invocation_dirs_frees_slots_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    monkeypatch.setattr(workflow_mod, "_MAX_INV_SLOT", 3)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    base = ws.path / "invocations"
    for seq, mtime in enumerate([1.0, 2.0, 3.0], start=1):
        create_invocation_dir(ws)
        os.utime(base / f"inv_{seq:03d}", (mtime, mtime))
    with pytest.raises(WorkflowError, match="no free invocation slot"):
        create_invocation_dir(ws)
    assert workflow_mod.prune_invocation_dirs(ws) == 2
    assert create_invocation_dir(ws) == "inv_001"


def test_save_and_load_workflow_round_trip(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("o", role="o", model="opus")
    g.complete_node("o", dispatched=["w"])
    g.start_node("w", role="w", model="sonnet", depends_on=["o"])
    g.complete_node("w")
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    assert path == workflow_path(ws, inv_id)
    loaded = load_workflow(path)
    assert loaded.id == inv_id
    assert loaded.team == "research"
    assert loaded.status == "success"
    assert {t.id for t in loaded.tasks} == {"o", "w"}


def test_workflowgraph_slot_index_accessor() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="implementation")
    assert g.slot_index is None
    g.set_slot_index(4)
    assert g.slot_index == 4
    assert g.doc.slot_index == 4


def test_slot_index_round_trips_through_save_load(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="implementation")
    g.set_slot_index(3)
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    loaded = load_workflow(path)
    assert loaded.slot_index == 3


def test_base_ref_sha_accessor() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="implementation")
    assert g.doc.base_ref_sha is None
    g.set_base_ref_sha("abc123")
    assert g.doc.base_ref_sha == "abc123"
    assert g.doc.schema_version == "1.7"


def test_base_ref_sha_round_trips_through_save_load(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="implementation")
    g.set_base_ref_sha("deadbeef1234")
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    loaded = load_workflow(path)
    assert loaded.base_ref_sha == "deadbeef1234"


def test_legacy_1_6_doc_without_base_ref_sha_loads(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = workflow_path(ws, inv_id)
    path.write_text(
        "schema_version: '1.6'\n"
        f"id: {inv_id}\n"
        "namespace: ns\n"
        "team: implementation\n"
        "status: running\n"
        "started_at: '2026-06-11T00:00:00+00:00'\n"
        "tasks: []\n",
        encoding="utf-8",
    )
    loaded = load_workflow(path)
    assert loaded.schema_version == "1.6"
    assert loaded.base_ref_sha is None


def test_legacy_1_5_doc_without_slot_index_loads(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = workflow_path(ws, inv_id)
    path.write_text(
        "schema_version: '1.5'\n"
        f"id: {inv_id}\n"
        "namespace: ns\n"
        "team: implementation\n"
        "status: running\n"
        "started_at: '2026-06-11T00:00:00+00:00'\n"
        "tasks: []\n",
        encoding="utf-8",
    )
    loaded = load_workflow(path)
    assert loaded.schema_version == "1.5"
    assert loaded.slot_index is None


def test_save_workflow_atomic_no_tmp_left(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    tmp = workflow_path(ws, inv_id).with_suffix(".yaml.tmp")
    assert not tmp.exists()


def test_save_workflow_cleans_tmp_on_yaml_failure(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.finalize(status="success")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(yaml, "safe_dump", boom)
    with pytest.raises(OSError, match="disk gone"):
        save_workflow(ws, g.doc)
    tmp = workflow_path(ws, inv_id).with_suffix(".yaml.tmp")
    assert not tmp.exists()


def test_load_workflow_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkflowError, match="missing file"):
        load_workflow(tmp_path / "nope.yaml")


def test_load_workflow_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("not: valid: yaml: [oops", encoding="utf-8")
    with pytest.raises(WorkflowError, match="invalid YAML"):
        load_workflow(p)


def test_load_workflow_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="expected mapping"):
        load_workflow(p)


def test_load_workflow_schema_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("schema_version: '1.0'\nid: x\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="schema mismatch"):
        load_workflow(p)


def test_workflowdoc_validation_rejects_unknown_status() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValidationError, match="status"):
        WorkflowDoc(
            id="x",
            namespace="ns",
            team="t",
            status="invalid",  # type: ignore[arg-type]
            started_at=datetime.now(UTC),
        )


def test_list_invocations_empty_when_no_dir(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    assert list_invocations(ws) == []


def test_list_invocations_only_includes_complete_workflows(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv1 = create_invocation_dir(ws)
    create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv1, team="research")
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    assert list_invocations(ws) == [inv1]


def test_path_helpers(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    assert workflow_path(ws, "inv_001") == (
        ws.path / "invocations" / "inv_001" / "workflow.yaml"
    )
    assert stream_path(ws, "inv_001", "worker") == (
        ws.path / "invocations" / "inv_001" / "agents" / "worker.stream.jsonl"
    )
    assert agent_meta_path(ws, "inv_001", "worker") == (
        ws.path / "invocations" / "inv_001" / "agents" / "worker.meta.yaml"
    )


def test_report_path_shape(ws_root: Path) -> None:
    from alan.workflow import report_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    assert report_path(ws, inv_id) == (
        ws.path / "invocations" / inv_id / "report.md"
    )


def test_save_invocation_report_roundtrip(ws_root: Path) -> None:
    from alan.workflow import report_path, save_invocation_report

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    text = "# Report\n\nMulti-paragraph unicode content: café, 日本語.\n"
    path = save_invocation_report(ws, inv_id, text)
    assert path == report_path(ws, inv_id)
    assert path.read_text(encoding="utf-8") == text
    assert not path.with_suffix(".md.tmp").exists()


def test_save_invocation_report_file_mode_is_owner_only(ws_root: Path) -> None:
    import stat

    from alan.workflow import report_path, save_invocation_report

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_invocation_report(ws, inv_id, "secret report")
    mode = stat.S_IMODE(report_path(ws, inv_id).stat().st_mode)
    assert mode == 0o600


def test_save_invocation_report_overwrites_on_resume(ws_root: Path) -> None:
    from alan.workflow import report_path, save_invocation_report

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_invocation_report(ws, inv_id, "first pass")
    save_invocation_report(ws, inv_id, "second pass")
    assert report_path(ws, inv_id).read_text(encoding="utf-8") == "second pass"


def test_save_invocation_report_oserror_propagates(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan.workflow import report_path, save_invocation_report

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)

    def boom(self: Path, *_a: object, **_k: object) -> None:
        del self
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk gone"):
        save_invocation_report(ws, inv_id, "text")
    assert not report_path(ws, inv_id).with_suffix(".md.tmp").exists()


@pytest.mark.parametrize(
    "bad_inv",
    ["..", "../escape", "inv_1", "inv_1000", "inv_001/x", "INV_001", ""],
)
def test_workflow_path_rejects_unsafe_inv_id(ws_root: Path, bad_inv: str) -> None:
    ws = _make_ws(ws_root)
    with pytest.raises(WorkflowError, match="invalid invocation id"):
        workflow_path(ws, bad_inv)


@pytest.mark.parametrize(
    "bad_role",
    ["..", "../etc/passwd", "a/b", "Worker", "1worker", "", "agent.stream"],
)
def test_stream_path_rejects_unsafe_role(ws_root: Path, bad_role: str) -> None:
    ws = _make_ws(ws_root)
    with pytest.raises(WorkflowError, match="invalid node id"):
        stream_path(ws, "inv_001", bad_role)


def test_agent_meta_path_validates_both_segments(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    with pytest.raises(WorkflowError, match="invalid invocation id"):
        agent_meta_path(ws, "bad", "worker")
    with pytest.raises(WorkflowError, match="invalid node id"):
        agent_meta_path(ws, "inv_001", "../bad")


def test_graph_start_node_rejects_unsafe_id() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    with pytest.raises(WorkflowError, match="invalid node id"):
        g.start_node("../x", role="x", model="sonnet")


def test_workflowtask_claude_session_id_defaults_none() -> None:
    task = WorkflowTask(id="x", role="x", model="sonnet", status="pending")
    assert task.claude_session_id is None


def test_set_session_id_records_on_task() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-abc")
    assert g.doc.tasks[0].claude_session_id == "sid-abc"


def test_set_session_id_unknown_node_raises() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    with pytest.raises(WorkflowError, match="unknown node"):
        g.set_session_id("missing", "sid")


def test_workflowdoc_accepts_legacy_schema_1_0() -> None:
    from datetime import UTC, datetime

    doc = WorkflowDoc.model_validate(
        {
            "schema_version": "1.0",
            "id": "inv_001",
            "namespace": "ns",
            "team": "research",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    assert doc.schema_version == "1.0"


def test_seeded_effort_round_trips_through_activation(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="implementation",
        tasks=[
            _PendingTaskSpec(
                id="implementer", role="implementer", model="sonnet", effort="xhigh"
            )
        ],
    )
    g = load_graph(ws, inv_id)
    assert g.doc.tasks[0].effort == "xhigh"
    activated = g.start_node(
        "implementer", role="implementer", model="sonnet", effort="xhigh"
    )
    assert activated.effort == "xhigh"


def test_activate_pending_clears_error_field(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="research",
        tasks=[_PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    g = load_graph(ws, inv_id)
    g.doc.tasks[0].error = "interrupted_by_restart"
    save_workflow(ws, g.doc)
    reloaded = load_graph(ws, inv_id)
    activated = reloaded.start_node("w", role="w", model="sonnet")
    assert activated.error is None


def test_seed_pending_workflow_stores_source_task_id(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="research",
        tasks=[_PendingTaskSpec(id="o", role="o", model="opus")],
        source_task_id="sub-task-1",
    )
    g = load_graph(ws, inv_id)
    assert g.doc.source_task_id == "sub-task-1"


def test_seed_pending_workflow_source_task_id_defaults_none(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="research",
        tasks=[_PendingTaskSpec(id="o", role="o", model="opus")],
    )
    g = load_graph(ws, inv_id)
    assert g.doc.source_task_id is None


@pytest.fixture(autouse=True)
def _clear_workflow_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_mod, "_WORKFLOW_CACHE", {})


def test_load_workflow_cached_hit_calls_load_once(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    call_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    doc1 = load_workflow_cached(path)
    doc2 = load_workflow_cached(path)
    assert call_count == 1
    assert doc1.id == doc2.id == inv_id


def test_load_workflow_cached_miss_on_mtime_change(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    call_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    load_workflow_cached(path)
    assert call_count == 1
    load_workflow_cached(bump_mtime(save_workflow(ws, g.doc)))
    assert call_count == 2


def test_load_workflow_cached_fallthrough_on_stat_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    path = tmp_path / "ghost.yaml"
    with pytest.raises(WorkflowError, match="missing file"):
        load_workflow_cached(path)
    assert call_count == 1
    assert path not in workflow_mod._WORKFLOW_CACHE


def test_graph_mark_interrupted_records_status_and_error() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-abc")
    task = g.mark_interrupted("w", error="paused")
    assert task.status == "interrupted"
    assert task.error == "paused"
    assert task.claude_session_id == "sid-abc"


def test_graph_resume_node_flips_interrupted_to_running() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-abc")
    g.mark_interrupted("w", error="paused")
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    g.doc.tasks[0].completed_at = _dt.now(_UTC)
    task = g.resume_node("w")
    assert task.status == "running"
    assert task.completed_at is None
    assert task.claude_session_id == "sid-abc"


def test_graph_resume_node_rejects_non_interrupted() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    with pytest.raises(WorkflowError, match="cannot resume"):
        g.resume_node("w")


def test_graph_resume_node_unknown_raises() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    with pytest.raises(WorkflowError, match="unknown node"):
        g.resume_node("missing")


def test_graph_mark_interrupted_without_error_keeps_existing() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    g.doc.tasks[0].error = "prior"
    task = g.mark_interrupted("w")
    assert task.error == "prior"


def test_resume_node_clears_error_string() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    g.mark_interrupted("w", error=INTERRUPTED_BY_RESTART_ERROR)
    assert g.doc.tasks[0].error == INTERRUPTED_BY_RESTART_ERROR
    g.resume_node("w")
    assert g.doc.tasks[0].error is None


def test_save_and_load_node_result_round_trip(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path, save_node_result

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(
        ws, inv_id, "architect", role="architect", model="opus", result_text="plan body"
    )
    assert load_node_result(ws, inv_id, "architect") == "plan body"
    assert result_path(ws, inv_id, "architect").exists()


def test_save_node_result_atomic_no_tmp_left(ws_root: Path) -> None:
    from alan.workflow import result_path, save_node_result

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(
        ws, inv_id, "w", role="w", model="sonnet", result_text="ok"
    )
    tmp = result_path(ws, inv_id, "w").with_suffix(".json.tmp")
    assert not tmp.exists()


def test_save_node_result_delegates_to_store_write_text_atomic(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    import json as _json

    from alan import store as store_mod
    from alan.workflow import result_path, save_node_result

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    calls: list[tuple[Path, str, int | None]] = []

    def spy(path: Path, text: str, *, mode: int | None = None) -> None:
        calls.append((path, text, mode))

    monkeypatch.setattr(store_mod, "write_text_atomic", spy)
    save_node_result(ws, inv_id, "w", role="w", model="sonnet", result_text="ok")
    assert len(calls) == 1
    path, text, mode = calls[0]
    assert path == result_path(ws, inv_id, "w")
    assert mode == 0o600
    payload = _json.loads(text)
    assert payload["role"] == "w"
    assert payload["model"] == "sonnet"
    assert payload["result_text"] == "ok"


def test_load_node_result_missing_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result

    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    assert load_node_result(ws, "inv_001", "nope") is None


def test_load_node_result_malformed_json_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_node_result(ws, inv_id, "w") is None


def test_load_node_result_non_dict_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_node_result(ws, inv_id, "w") is None


def test_load_node_result_empty_result_text_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"result_text": ""}', encoding="utf-8")
    assert load_node_result(ws, inv_id, "w") is None


def test_load_node_result_non_string_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"result_text": 42}', encoding="utf-8")
    assert load_node_result(ws, inv_id, "w") is None


def test_load_node_result_invalid_utf8_returns_none(ws_root: Path) -> None:
    from alan.workflow import load_node_result, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\xfd not valid utf-8")
    assert load_node_result(ws, inv_id, "w") is None


def test_save_node_result_file_mode_is_owner_only(ws_root: Path) -> None:
    import stat

    from alan.workflow import result_path, save_node_result

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(
        ws, inv_id, "w", role="w", model="sonnet", result_text="secret"
    )
    mode = stat.S_IMODE(result_path(ws, inv_id, "w").stat().st_mode)
    assert mode == 0o600


def test_invalidate_node_result_deletes_cache(ws_root: Path) -> None:
    from alan.workflow import (
        invalidate_node_result,
        result_path,
        save_node_result,
    )

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(ws, inv_id, "w", role="w", model="sonnet", result_text="x")
    assert result_path(ws, inv_id, "w").exists()
    invalidate_node_result(ws, inv_id, "w")
    assert not result_path(ws, inv_id, "w").exists()


def test_invalidate_node_result_idempotent_when_missing(ws_root: Path) -> None:
    from alan.workflow import invalidate_node_result

    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    invalidate_node_result(ws, "inv_001", "w")


def test_invalidate_node_result_swallows_os_errors(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan.workflow import invalidate_node_result, save_node_result

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(ws, inv_id, "w", role="w", model="sonnet", result_text="x")
    original_unlink = Path.unlink

    def boom(self: Path, missing_ok: bool = False) -> None:
        del self, missing_ok
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", boom)
    invalidate_node_result(ws, inv_id, "w")
    monkeypatch.setattr(Path, "unlink", original_unlink)


def test_load_node_result_doc_returns_doc_on_valid_file(ws_root: Path) -> None:
    from alan.workflow import (
        NodeResultDoc,
        load_node_result_doc,
        save_node_result,
    )

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_result(ws, inv_id, "w", role="w", model="sonnet", result_text="hello")
    doc = load_node_result_doc(ws, inv_id, "w")
    assert doc is not None
    assert doc["role"] == "w"
    assert doc["model"] == "sonnet"
    assert doc["result_text"] == "hello"
    assert isinstance(doc["completed_at"], str)
    assert isinstance(doc, dict)
    _: NodeResultDoc = doc


def test_load_node_result_doc_returns_none_when_missing(ws_root: Path) -> None:
    from alan.workflow import load_node_result_doc

    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    assert load_node_result_doc(ws, "inv_001", "nope") is None


def test_load_node_result_doc_returns_none_on_unicode_error(ws_root: Path) -> None:
    from alan.workflow import load_node_result_doc, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe bad utf-8")
    assert load_node_result_doc(ws, inv_id, "w") is None


def test_load_node_result_doc_returns_none_on_bad_json(ws_root: Path) -> None:
    from alan.workflow import load_node_result_doc, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")
    assert load_node_result_doc(ws, inv_id, "w") is None


def test_load_node_result_doc_returns_none_when_not_dict(ws_root: Path) -> None:
    from alan.workflow import load_node_result_doc, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_node_result_doc(ws, inv_id, "w") is None


def test_load_node_result_doc_returns_none_when_field_not_str(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import load_node_result_doc, result_path

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = result_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"role": "w", "model": "sonnet", "result_text": 99, "completed_at": "t"}
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_result_doc(ws, inv_id, "w") is None


def test_config_path_validates_ids(ws_root: Path) -> None:
    from alan.workflow import config_path

    ws = _make_ws(ws_root)
    with pytest.raises(WorkflowError, match="invalid invocation id"):
        config_path(ws, "bad", "w")
    with pytest.raises(WorkflowError, match="invalid node id"):
        config_path(ws, "inv_001", "../escape")


def test_save_and_load_node_config_round_trip(ws_root: Path) -> None:
    from alan.workflow import config_path, load_node_config, save_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_config(
        ws,
        inv_id,
        "w",
        system_prompt="sys",
        user_text="user",
        allowed_tools=["Read", "Grep"],
        max_turns=10,
        permission_mode="auto",
        model="sonnet",
        turns_source="static",
        derived_max_turns=10,
    )
    doc = load_node_config(ws, inv_id, "w")
    assert doc is not None
    assert doc["system_prompt"] == "sys"
    assert doc["user_text"] == "user"
    assert doc["allowed_tools"] == ["Read", "Grep"]
    assert doc["max_turns"] == 10
    assert doc["permission_mode"] == "auto"
    assert doc["model"] == "sonnet"
    assert doc["turns_source"] == "static"
    assert doc["derived_max_turns"] == 10
    assert config_path(ws, inv_id, "w").exists()


def test_save_node_config_file_mode_is_owner_only(ws_root: Path) -> None:
    import stat

    from alan.workflow import config_path, save_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    save_node_config(
        ws, inv_id, "w",
        system_prompt="s", user_text="u", allowed_tools=[],
        max_turns=5, permission_mode="auto", model="opus",
        turns_source="static", derived_max_turns=5,
    )
    mode = stat.S_IMODE(config_path(ws, inv_id, "w").stat().st_mode)
    assert mode == 0o600


def test_save_node_config_delegates_to_store_write_text_atomic(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    import json as _json

    from alan import store as store_mod
    from alan.workflow import config_path, save_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    calls: list[tuple[Path, str, int | None]] = []

    def spy(path: Path, text: str, *, mode: int | None = None) -> None:
        calls.append((path, text, mode))

    monkeypatch.setattr(store_mod, "write_text_atomic", spy)
    save_node_config(
        ws, inv_id, "w",
        system_prompt="s", user_text="u", allowed_tools=[],
        max_turns=5, permission_mode="auto", model="opus",
        turns_source="static", derived_max_turns=5,
    )
    assert len(calls) == 1
    path, text, mode = calls[0]
    assert path == config_path(ws, inv_id, "w")
    assert mode == 0o600
    payload = _json.loads(text)
    assert payload["system_prompt"] == "s"
    assert payload["model"] == "opus"


def test_load_node_config_returns_none_when_missing(ws_root: Path) -> None:
    from alan.workflow import load_node_config

    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    assert load_node_config(ws, "inv_001", "nope") is None


def test_load_node_config_returns_none_on_unicode_error(ws_root: Path) -> None:
    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe bad utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_on_bad_json(ws_root: Path) -> None:
    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_not_dict(ws_root: Path) -> None:
    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_str_field_wrong(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": 42, "user_text": "u",
        "allowed_tools": [], "max_turns": 5,
        "permission_mode": "auto", "model": "sonnet",
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_allowed_tools_wrong(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": "s", "user_text": "u",
        "allowed_tools": "not-a-list", "max_turns": 5,
        "permission_mode": "auto", "model": "sonnet",
        "turns_source": "static", "derived_max_turns": 5,
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_allowed_tools_element_not_str(
    ws_root: Path,
) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": "s", "user_text": "u",
        "allowed_tools": [1, 2, 3], "max_turns": 5,
        "permission_mode": "auto", "model": "sonnet",
        "turns_source": "static", "derived_max_turns": 5,
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_max_turns_wrong(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": "s", "user_text": "u",
        "allowed_tools": [], "max_turns": "not-int",
        "permission_mode": "auto", "model": "sonnet",
        "turns_source": "static", "derived_max_turns": 5,
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_turns_source_missing(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": "s", "user_text": "u",
        "allowed_tools": [], "max_turns": 5,
        "permission_mode": "auto", "model": "sonnet",
        "derived_max_turns": 5,
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_load_node_config_returns_none_when_derived_max_turns_wrong(ws_root: Path) -> None:
    import json as _json

    from alan.workflow import config_path, load_node_config

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = config_path(ws, inv_id, "w")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "system_prompt": "s", "user_text": "u",
        "allowed_tools": [], "max_turns": 5,
        "permission_mode": "auto", "model": "sonnet",
        "turns_source": "static", "derived_max_turns": "not-int",
    }
    path.write_text(_json.dumps(payload), encoding="utf-8")
    assert load_node_config(ws, inv_id, "w") is None


def test_node_usage_defaults_all_none() -> None:
    usage = NodeUsage()
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.cache_creation_input_tokens is None
    assert usage.cache_read_input_tokens is None
    assert usage.total_cost_usd is None
    assert usage.usage_raw is None


def test_complete_node_stores_usage_on_task() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    usage = NodeUsage(
        input_tokens=100,
        output_tokens=250,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=50,
        total_cost_usd=0.0012,
        usage_raw={"input_tokens": 100, "output_tokens": 250},
    )
    done = g.complete_node("w", usage=usage)
    assert done.input_tokens == 100
    assert done.output_tokens == 250
    assert done.cache_creation_input_tokens == 0
    assert done.cache_read_input_tokens == 50
    assert done.total_cost_usd == 0.0012
    assert done.usage_raw == {"input_tokens": 100, "output_tokens": 250}


def test_complete_node_without_usage_leaves_tokens_null() -> None:
    g = WorkflowGraph(task_id="t1", inv_id="inv_001", team="research")
    g.start_node("w", role="w", model="sonnet")
    done = g.complete_node("w")
    assert done.input_tokens is None
    assert done.output_tokens is None
    assert done.total_cost_usd is None
    assert done.usage_raw is None


def test_save_and_load_workflow_preserves_usage_fields(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    usage = NodeUsage(
        input_tokens=42,
        output_tokens=88,
        total_cost_usd=0.0007,
        usage_raw={"input_tokens": 42, "output_tokens": 88},
    )
    g.complete_node("w", usage=usage)
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    loaded = load_workflow(workflow_path(ws, inv_id))
    task = loaded.tasks[0]
    assert task.input_tokens == 42
    assert task.output_tokens == 88
    assert task.total_cost_usd == 0.0007
    assert task.usage_raw == {"input_tokens": 42, "output_tokens": 88}


def test_save_and_load_workflow_preserves_zero_cache_tokens(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    usage = NodeUsage(
        input_tokens=10,
        output_tokens=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        usage_raw={"input_tokens": 10, "output_tokens": 20},
    )
    g.complete_node("w", usage=usage)
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    loaded = load_workflow(workflow_path(ws, inv_id))
    task = loaded.tasks[0]
    assert task.cache_creation_input_tokens == 0
    assert task.cache_read_input_tokens == 0


def test_reset_failed_nodes_for_rerun_resets_failed_to_pending(ws_root: Path) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-xyz")
    g.fail_node("w", error="node exploded")
    save_workflow(ws, g.doc)
    reset_failed_nodes_for_rerun(ws, inv_id)
    loaded = load_workflow(workflow_path(ws, inv_id))
    task = loaded.tasks[0]
    assert task.status == "pending"
    assert task.error is None
    assert task.started_at is None
    assert task.completed_at is None
    assert task.claude_session_id is None


def test_reset_failed_nodes_for_rerun_resets_running_to_pending(ws_root: Path) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-abc")
    save_workflow(ws, g.doc)
    reset_failed_nodes_for_rerun(ws, inv_id)
    loaded = load_workflow(workflow_path(ws, inv_id))
    assert loaded.tasks[0].status == "pending"
    assert loaded.tasks[0].claude_session_id is None


def test_reset_failed_nodes_for_rerun_preserves_success_nodes(ws_root: Path) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("a", role="a", model="opus")
    g.complete_node("a")
    g.start_node("b", role="b", model="sonnet", depends_on=["a"])
    g.fail_node("b", error="boom")
    save_workflow(ws, g.doc)
    reset_failed_nodes_for_rerun(ws, inv_id)
    loaded = load_workflow(workflow_path(ws, inv_id))
    statuses = {t.id: t.status for t in loaded.tasks}
    assert statuses == {"a": "success", "b": "pending"}


def test_reset_failed_nodes_for_rerun_noop_on_missing_file(ws_root: Path) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    reset_failed_nodes_for_rerun(ws, "inv_001")


def test_reset_failed_nodes_for_rerun_noop_when_all_success(ws_root: Path) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.complete_node("w")
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    mtime_before = workflow_path(ws, inv_id).stat().st_mtime_ns
    reset_failed_nodes_for_rerun(ws, inv_id)
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime_before


def test_reset_failed_nodes_for_rerun_swallows_save_oserror(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan.workflow import reset_failed_nodes_for_rerun

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.fail_node("w", error="boom")
    save_workflow(ws, g.doc)

    import yaml as _yaml

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_yaml, "safe_dump", boom)
    reset_failed_nodes_for_rerun(ws, inv_id)


def test_workflowdoc_accepts_legacy_schema_1_1() -> None:
    from datetime import UTC, datetime

    doc = WorkflowDoc.model_validate(
        {
            "schema_version": "1.1",
            "id": "inv_001",
            "namespace": "ns",
            "team": "research",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    assert doc.schema_version == "1.1"


def test_workflowdoc_source_task_id_defaults_none() -> None:
    from datetime import UTC, datetime

    doc = WorkflowDoc(
        id="inv_001",
        namespace="ns",
        team="research",
        status="running",
        started_at=datetime.now(UTC),
    )
    assert doc.source_task_id is None


def test_seed_pending_workflow_threads_source_task_id(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="implementation",
        tasks=[_PendingTaskSpec(id="impl", role="impl", model="sonnet")],
        source_task_id="sub-task-001",
    )
    loaded = load_workflow(workflow_path(ws, inv_id))
    assert loaded.source_task_id == "sub-task-001"


def test_seed_pending_workflow_source_task_id_none_by_default(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="research",
        tasks=[_PendingTaskSpec(id="o", role="o", model="opus")],
    )
    loaded = load_workflow(workflow_path(ws, inv_id))
    assert loaded.source_task_id is None


def test_workflowdoc_accepts_legacy_schema_1_4(ws_root: Path) -> None:
    from datetime import UTC, datetime

    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    path = workflow_path(ws, inv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    data = {
        "schema_version": "1.4",
        "id": inv_id,
        "namespace": f"alan.workspace.{ws.task_id}",
        "team": "research",
        "status": "success",
        "started_at": datetime.now(UTC).isoformat(),
        "tasks": [],
    }
    path.write_text(_yaml.safe_dump(data), encoding="utf-8")
    loaded = load_workflow(path)
    assert loaded.schema_version == "1.4"
    assert loaded.source_task_id is None


@pytest.fixture(autouse=True)
def _clear_workflow_summary_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_mod, "_WORKFLOW_SUMMARY_CACHE", {})


def test_load_workflow_summary_cached_hit_parses_once(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    call_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    s1 = workflow_mod.load_workflow_summary_cached(path)
    s2 = workflow_mod.load_workflow_summary_cached(path)
    assert call_count == 1
    assert s1 == s2
    assert s1.team == "research"
    assert s1.status == "success"
    assert s1.cost is None


def test_load_workflow_summary_cached_miss_on_rewrite_and_sums_cost(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("researcher", role="researcher", model="claude-sonnet-5")
    g.complete_node("researcher", usage=NodeUsage(total_cost_usd=0.004))
    g.finalize(status="success")
    path = save_workflow(ws, g.doc)
    call_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal call_count
        call_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    assert workflow_mod.load_workflow_summary_cached(path).cost == pytest.approx(0.004)
    assert call_count == 1
    workflow_mod.load_workflow_summary_cached(bump_mtime(save_workflow(ws, g.doc)))
    assert call_count == 2


def test_load_workflow_summary_cached_fallthrough_on_stat_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ghost.yaml"
    with pytest.raises(WorkflowError, match="missing file"):
        workflow_mod.load_workflow_summary_cached(path)
    assert path not in workflow_mod._WORKFLOW_SUMMARY_CACHE


def test_sentinel_model_constant() -> None:
    assert workflow_mod.SENTINEL_MODEL == "none"


def test_active_intervals_excludes_sentinel_model_node(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="deploy_verify",
        status="success",
        started_at=started,
        completed_at=started,
        tasks=[
            WorkflowTask(
                id="deploy_verify",
                role="deploy_verify",
                model="none",
                status="success",
                started_at=started,
                completed_at=started,
            )
        ],
    )
    path = save_workflow(ws, doc)
    summary = workflow_mod.load_workflow_summary_cached(path)
    assert summary.active_intervals == ()


def test_active_intervals_excludes_node_never_started(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="running",
        started_at=started,
        tasks=[WorkflowTask(id="w", role="w", model="sonnet", status="pending")],
    )
    path = save_workflow(ws, doc)
    summary = workflow_mod.load_workflow_summary_cached(path)
    assert summary.active_intervals == ()


def test_active_intervals_closed_node_uses_own_completed_at(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC)
    completed = started + timedelta(seconds=30)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="success",
        started_at=started,
        completed_at=completed,
        tasks=[
            WorkflowTask(
                id="w",
                role="w",
                model="sonnet",
                status="success",
                started_at=started,
                completed_at=completed,
            )
        ],
    )
    path = save_workflow(ws, doc)
    summary = workflow_mod.load_workflow_summary_cached(path)
    assert summary.active_intervals == ((started, completed),)


def test_active_intervals_clamps_open_node_to_doc_completed_at_when_terminal(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC)
    doc_completed = started + timedelta(seconds=45)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="failed",
        started_at=started,
        completed_at=doc_completed,
        tasks=[
            WorkflowTask(
                id="w",
                role="w",
                model="sonnet",
                status="failed",
                started_at=started,
                completed_at=None,
            )
        ],
    )
    path = save_workflow(ws, doc)
    summary = workflow_mod.load_workflow_summary_cached(path)
    assert summary.active_intervals == ((started, doc_completed),)


def test_active_intervals_leaves_node_open_when_doc_non_terminal(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="running",
        started_at=started,
        completed_at=None,
        tasks=[
            WorkflowTask(
                id="w",
                role="w",
                model="sonnet",
                status="running",
                started_at=started,
                completed_at=None,
            )
        ],
    )
    path = save_workflow(ws, doc)
    summary = workflow_mod.load_workflow_summary_cached(path)
    assert summary.active_intervals == ((started, None),)

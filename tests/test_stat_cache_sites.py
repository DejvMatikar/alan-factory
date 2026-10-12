from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from alan import handover as handover_mod
from alan import incident as incident_mod
from alan import manager_summary as ms
from alan import workflow as workflow_mod
from alan import workspace as ws_mod
from alan.incident import IncidentClass
from alan.run_summary import RunSummary, ToolUseCount, derive_run_summary
from alan.teams import _base as base


@pytest.fixture(autouse=True)
def _clear_stat_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handover_mod, "_HANDOVER_CACHE", {})
    monkeypatch.setattr(ms, "_USAGE_CACHE", {})
    monkeypatch.setattr(ws_mod, "_META_CACHE", {})
    monkeypatch.setattr(workflow_mod, "_WORKFLOW_CACHE", {})
    monkeypatch.setattr(workflow_mod, "_WORKFLOW_SUMMARY_CACHE", {})
    monkeypatch.setattr(base, "_ROLE_TURNS_CACHE", {})
    monkeypatch.setattr(incident_mod, "_AUDIT_ROWS_CACHE", {})


_FIXTURE_DECISION = {
    "id": "fixture-d1",
    "statement": "fixture decision",
    "rationale": "fixture rationale",
    "confidence": 0.9,
    "reversibility": "medium",
}


def _make_handover(task_id: str, full_synthesis: str = "ready") -> handover_mod.Handover:
    return handover_mod.Handover.model_validate({
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "forward",
        "task_id": task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "decisions": [dict(_FIXTURE_DECISION)],
        "full_synthesis": full_synthesis,
    })


def _write_usage_event(path: Path, *, cost: float, input_tokens: int, output_tokens: int) -> None:
    event = {
        "type": "result",
        "total_cost_usd": cost,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def test_load_handover_cached_hit_returns_independent_deep_copy(ws_root: Path) -> None:
    ws = ws_mod.create("cache-deepcopy", "d", root=ws_root)
    path = handover_mod.save_handover(ws, _make_handover(ws.task_id))
    first = handover_mod.load_handover_cached(path)
    first.assumptions.append("mutated")
    second = handover_mod.load_handover_cached(path)
    assert second.assumptions == []


def test_load_handover_cached_miss_when_file_replaced(ws_root: Path) -> None:
    ws = ws_mod.create("cache-miss", "d", root=ws_root)
    path = handover_mod.save_handover(ws, _make_handover(ws.task_id, full_synthesis="short"))
    first = handover_mod.load_handover_cached(path)
    assert first.full_synthesis == "short"
    replacement = _make_handover(ws.task_id, full_synthesis="a much longer synthesis body")
    path.write_text(
        yaml.safe_dump(replacement.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    second = handover_mod.load_handover_cached(path)
    assert second.full_synthesis == "a much longer synthesis body"


def test_read_manager_summary_usage_caches_a_legitimate_none_without_recomputing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    stream.write_text("not json\n", encoding="utf-8")
    call_count = 0
    original = derive_run_summary

    def spy(
        stream_path: Path, *, system_prompt: str, model: str = "haiku"
    ) -> RunSummary | None:
        nonlocal call_count
        call_count += 1
        return original(stream_path, system_prompt=system_prompt, model=model)

    monkeypatch.setattr(ms, "derive_run_summary", spy)
    first = ms.read_manager_summary_usage(tmp_path)
    second = ms.read_manager_summary_usage(tmp_path)
    assert first is None
    assert second is None
    assert call_count == 1


def test_read_manager_summary_usage_hit_returns_independent_deep_copy(tmp_path: Path) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_usage_event(stream, cost=0.001, input_tokens=10, output_tokens=5)
    first = ms.read_manager_summary_usage(tmp_path)
    assert first is not None
    first.tool_counts.append(ToolUseCount(name="Read", count=1))
    second = ms.read_manager_summary_usage(tmp_path)
    assert second is not None
    assert second.tool_counts == []


def test_read_meta_cached_hit_returns_independent_deep_copy(ws_root: Path) -> None:
    ws = ws_mod.create("meta-deepcopy", "d", root=ws_root)
    first = ws_mod._read_meta_cached(ws.path)  # noqa: SLF001
    first.assumptions.append("mutated")
    second = ws_mod._read_meta_cached(ws.path)  # noqa: SLF001
    assert second.assumptions == []


def test_read_meta_cached_stat_oserror_falls_back_to_loader(ws_root: Path) -> None:
    ws = ws_mod.create("meta-fallback", "d", root=ws_root)
    (ws.path / ws_mod._META_FILE).unlink()  # noqa: SLF001
    with pytest.raises(ws_mod.WorkspaceError, match="missing file"):
        ws_mod._read_meta_cached(ws.path)  # noqa: SLF001


def test_load_workflow_cached_hit_returns_independent_deep_copy(ws_root: Path) -> None:
    ws = ws_mod.create("wf-deepcopy", "d", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    graph = workflow_mod.WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    graph.start_node("w", role="w", model="sonnet")
    graph.finalize(status="success")
    path = workflow_mod.save_workflow(ws, graph.doc)
    first = workflow_mod.load_workflow_cached(path)
    first.tasks[0].role = "mutated"
    second = workflow_mod.load_workflow_cached(path)
    assert second.tasks[0].role == "w"


def test_load_workflow_cached_miss_when_doc_replaced(ws_root: Path) -> None:
    ws = ws_mod.create("wf-miss", "d", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    graph = workflow_mod.WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    graph.finalize(status="success")
    path = workflow_mod.save_workflow(ws, graph.doc)
    first = workflow_mod.load_workflow_cached(path)
    assert first.tasks == []
    graph.start_node("w", role="w", model="sonnet")
    workflow_mod.save_workflow(ws, graph.doc)
    second = workflow_mod.load_workflow_cached(path)
    assert [t.id for t in second.tasks] == ["w"]


def test_load_workflow_summary_cached_hit_returns_same_object_no_copy(ws_root: Path) -> None:
    ws = ws_mod.create("wf-summary-identity", "d", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    graph = workflow_mod.WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    graph.finalize(status="success")
    path = workflow_mod.save_workflow(ws, graph.doc)
    first = workflow_mod.load_workflow_summary_cached(path)
    second = workflow_mod.load_workflow_summary_cached(path)
    assert second is first


def test_load_workflow_summary_cached_stat_oserror_falls_back_to_derived_computation(
    ws_root: Path,
) -> None:
    ws = ws_mod.create("wf-summary-fallback", "d", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    graph = workflow_mod.WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    graph.finalize(status="success")
    path = workflow_mod.save_workflow(ws, graph.doc)
    path.unlink()
    with pytest.raises(workflow_mod.WorkflowError, match="missing file"):
        workflow_mod.load_workflow_summary_cached(path)


def test_cached_role_turns_hit_returns_same_object_no_copy(tmp_path: Path) -> None:
    audit_path = tmp_path / "node_turns_audit.jsonl"
    audit_path.write_text(
        json.dumps({"role": "implementer", "turns": 10}) + "\n", encoding="utf-8"
    )
    first = base._cached_role_turns(audit_path)  # noqa: SLF001
    second = base._cached_role_turns(audit_path)  # noqa: SLF001
    assert second is first


def test_cached_role_turns_missing_file_falls_back_to_fixed_empty_dict(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "node_turns_audit.jsonl"
    assert base._cached_role_turns(missing) == {}  # noqa: SLF001
    assert missing not in base._ROLE_TURNS_CACHE  # noqa: SLF001


def test_rows_from_file_cached_hit_returns_same_object_no_copy(tmp_path: Path) -> None:
    path = tmp_path / "job_failure_audit.jsonl"
    path.write_text(json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8")
    first = incident_mod._rows_from_file_cached(path, IncidentClass.JOB_FAILURE)  # noqa: SLF001
    second = incident_mod._rows_from_file_cached(path, IncidentClass.JOB_FAILURE)  # noqa: SLF001
    assert second is first


def test_rows_from_file_cached_stat_oserror_falls_back_to_fixed_empty_list(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing_audit.jsonl"
    assert incident_mod._rows_from_file_cached(missing, IncidentClass.JOB_FAILURE) == []  # noqa: SLF001
    assert (missing, IncidentClass.JOB_FAILURE) not in incident_mod._AUDIT_ROWS_CACHE  # noqa: SLF001


def test_rows_from_file_cached_keys_on_path_and_incident_class_independently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shared_audit.jsonl"
    path.write_text(json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8")
    job_rows = incident_mod._rows_from_file_cached(path, IncidentClass.JOB_FAILURE)  # noqa: SLF001
    heal_rows = incident_mod._rows_from_file_cached(path, IncidentClass.SELF_HEAL)  # noqa: SLF001
    assert job_rows[0].incident_class == IncidentClass.JOB_FAILURE
    assert heal_rows[0].incident_class == IncidentClass.SELF_HEAL
    assert (path, IncidentClass.JOB_FAILURE) in incident_mod._AUDIT_ROWS_CACHE  # noqa: SLF001
    assert (path, IncidentClass.SELF_HEAL) in incident_mod._AUDIT_ROWS_CACHE  # noqa: SLF001


def test_rows_from_file_cached_miss_when_file_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "job_failure_audit.jsonl"
    path.write_text(json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8")
    first = incident_mod._rows_from_file_cached(path, IncidentClass.JOB_FAILURE)  # noqa: SLF001
    assert len(first) == 1
    path.write_text(
        json.dumps({"task_id": "t1"}) + "\n" + json.dumps({"task_id": "t2"}) + "\n",
        encoding="utf-8",
    )
    second = incident_mod._rows_from_file_cached(path, IncidentClass.JOB_FAILURE)  # noqa: SLF001
    assert len(second) == 2

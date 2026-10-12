from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import claude_cli_journal
from alan.claude_cli_journal import open_journal_handles, open_stream_sink
from alan.handover import Handover, save_handover
from alan.retention import compress_archived_streams
from alan.workflow import WorkflowGraph, save_workflow
from alan.workspace import Status, Workspace, create, transition

_PRIVATE = 0o600


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _make_ws(ws_root: Path, title: str = "modes") -> Workspace:
    return create(title, "desc", root=ws_root)


def _make_handover(task_id: str) -> Handover:
    return Handover.model_validate({
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "forward",
        "task_id": task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC),
        "next_hop": None,
        "decisions": [{
            "id": "d1",
            "statement": "fixture decision",
            "rationale": "fixture rationale",
            "confidence": 0.9,
            "reversibility": "medium",
        }],
        "open_questions": [],
        "assumptions": [],
        "constraints_inherited": [],
        "artifacts_produced": [],
        "full_synthesis": "plan ready; proceed to implementation",
    })


def test_saved_handover_yaml_is_not_group_or_world_readable(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    path = save_handover(ws, _make_handover(ws.task_id))
    assert _mode(path) == _PRIVATE


def test_second_handover_slot_is_also_private(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    first = save_handover(ws, _make_handover(ws.task_id))
    second = save_handover(ws, _make_handover(ws.task_id))
    assert first != second
    assert _mode(first) == _PRIVATE
    assert _mode(second) == _PRIVATE


def test_saved_workflow_yaml_is_not_group_or_world_readable(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    graph = WorkflowGraph(task_id=ws.task_id, inv_id="inv_001", team="research")
    path = save_workflow(ws, graph.doc)
    assert _mode(path) == _PRIVATE


def test_resaved_workflow_yaml_keeps_the_private_mode(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    graph = WorkflowGraph(task_id=ws.task_id, inv_id="inv_001", team="research")
    save_workflow(ws, graph.doc)
    graph.start_node("orchestrator", role="orchestrator", model="opus")
    path = save_workflow(ws, graph.doc)
    assert _mode(path) == _PRIVATE


def test_compressed_stream_archive_is_not_group_or_world_readable(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("archived", "desc", root=ws_root)
    ws = transition(ws, Status.ARCHIVED)
    agents = ws.path / "invocations" / "inv_001" / "agents"
    agents.mkdir(parents=True)
    stream = agents / "implementer.stream.jsonl"
    stream.write_text('{"n": 1}\n', encoding="utf-8")
    stream.chmod(_PRIVATE)

    assert compress_archived_streams(workspaces_root=ws_root) == 1

    assert _mode(stream.with_name(stream.name + ".gz")) == _PRIVATE


def test_stream_sink_creates_a_private_file(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "role.stream.jsonl"
    with open_stream_sink(path) as sink:
        assert sink is not None
        sink.write("{}\n")
    assert _mode(path) == _PRIVATE


def test_stream_sink_tightens_a_preexisting_world_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "role.stream.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o644)
    with open_stream_sink(path) as sink:
        assert sink is not None
    assert _mode(path) == _PRIVATE


def test_a_failing_chmod_closes_the_handle_instead_of_leaking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def boom(fd: int, mode: int) -> None:
        seen.append(fd)
        raise OSError("chmod refused")

    monkeypatch.setattr(os, "fchmod", boom)
    with pytest.raises(OSError, match="chmod refused"):
        claude_cli_journal._open_private_append(tmp_path / "leak.jsonl")  # noqa: SLF001
    assert len(seen) == 1
    with pytest.raises(OSError):
        os.fstat(seen[0])


def test_journal_and_err_handles_are_private(tmp_path: Path) -> None:
    journal = tmp_path / "agents" / "role.stream.jsonl"
    jh, eh, offset, err_offset = open_journal_handles(journal)
    try:
        assert (offset, err_offset) == (0, 0)
    finally:
        jh.close()
        eh.close()
    assert _mode(journal) == _PRIVATE
    assert _mode(journal.with_suffix(".err")) == _PRIVATE

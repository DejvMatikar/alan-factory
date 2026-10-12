from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import alan.merge_gate as merge_gate_mod
import alan.project as project_mod
from alan import workspace as workspace_mod
from alan.app import workspace_creation
from alan.app.workspace_create_models import CreateWorkspaceBody
from alan.errors import AppError
from alan.events import PubSub
from alan.queue import Queue
from alan.scope_audit import ScopeGapFinding
from alan.workspace import Task, TaskStatus

_SKIPPED_GATE = merge_gate_mod.MergeGateDecision.skipped("no pull request in this test")


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def _finding() -> ScopeGapFinding:
    return ScopeGapFinding(
        kind="prior_workspace",
        summary="near-duplicate workspace found",
        references=["old-ws"],
        suggested_action="needs_user_call",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("   \n", None), ("  keep me \n", "keep me")],
)
def test_normalize_instructions_strips_and_collapses_blank_to_none(
    raw: str | None, expected: str | None
) -> None:
    assert workspace_creation.normalize_instructions(raw) == expected


def test_task_title_for_audit_prefers_the_subtask_title(ws_root: Path) -> None:
    tasks = [Task(id="sub-a", title="Sub A", status=TaskStatus.PENDING)]
    ws = workspace_mod.create("Parent Title", "d", root=ws_root, tasks=tasks)
    assert workspace_creation.task_title_for_audit(ws, "sub-a") == "Sub A"


def test_task_title_for_audit_falls_back_to_the_workspace_title(ws_root: Path) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = workspace_mod.create("Parent Title", "d", root=ws_root, tasks=tasks)
    assert workspace_creation.task_title_for_audit(ws, "sub-a") == "Parent Title"
    assert workspace_creation.task_title_for_audit(ws, "unknown") == "Parent Title"


def test_resolve_engine_key_names_the_project_when_its_bound_engine_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_mod,
        "cached_read",
        lambda _project_id, _cache: project_mod.Project.model_construct(engine_key="no/such"),
    )
    with pytest.raises(AppError) as exc_info:
        workspace_creation.resolve_engine_key(None, "proj-1", engine_key_provided=False)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "project's bound engine no longer exists"


def test_resolve_engine_key_is_none_when_no_key_and_no_project() -> None:
    assert workspace_creation.resolve_engine_key(None, None, engine_key_provided=False) is None


async def test_scan_scope_findings_returns_the_scan_result(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def scan(title: str, description: str, **kwargs: Any) -> list[ScopeGapFinding]:
        calls.append({"title": title, "description": description, **kwargs})
        return [_finding()]

    monkeypatch.setattr(workspace_creation, "scan_predispatch", scan)
    monkeypatch.setattr(workspace_creation, "read_scope_section", lambda _project_id: "anti-scope")
    body = CreateWorkspaceBody(title="T", description="D", project_id="proj-1")
    findings = await workspace_creation.scan_scope_findings(body, ws_root, "acme/widget")
    assert findings == [_finding()]
    assert calls == [
        {
            "title": "T",
            "description": "D",
            "workspaces_root": ws_root,
            "anti_scope": "anti-scope",
            "scope_engine_key": "acme/widget",
        }
    ]


async def test_scan_scope_findings_is_empty_when_the_scan_raises(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> list[ScopeGapFinding]:
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(workspace_creation, "scan_predispatch", boom)
    body = CreateWorkspaceBody(title="T")
    assert await workspace_creation.scan_scope_findings(body, ws_root, None) == []


async def test_scan_scope_findings_ignores_a_failing_anti_scope_lookup(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    seen: list[Any] = []

    def scan(*_args: Any, **kwargs: Any) -> list[ScopeGapFinding]:
        seen.append(kwargs["anti_scope"])
        return []

    def boom(_project_id: str) -> str:
        raise RuntimeError("scope exploded")

    monkeypatch.setattr(workspace_creation, "scan_predispatch", scan)
    monkeypatch.setattr(workspace_creation, "read_scope_section", boom)
    body = CreateWorkspaceBody(title="T", project_id="proj-1")
    assert await workspace_creation.scan_scope_findings(body, ws_root, None) == []
    assert seen == [None]


def test_append_predispatch_log_writes_one_json_line_to_the_configured_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "predispatch.jsonl"
    monkeypatch.setenv("ALAN_PREDISPATCH_LOG", str(log_path))
    workspace_creation.append_predispatch_log("ws-1", "Title", [_finding()])
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["task_id"] == "ws-1"
    assert entry["title"] == "Title"
    assert entry["findings"][0]["kind"] == "prior_workspace"


async def test_dispatch_root_task_threads_the_supplied_merge_gate_to_dispatch(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Gate Thread", "d", root=ws_root, pending_first_hop="research")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        workspace_creation, "dispatch_team_core", lambda **kwargs: captured.update(kwargs)
    )
    await workspace_creation.dispatch_root_task_or_rollback(
        ws, "research", "do it", None, queue, PubSub(), ws_root, merge_gate=_SKIPPED_GATE
    )
    assert captured["merge_gate"] is _SKIPPED_GATE
    assert captured["instructions"] == "do it"
    assert captured["task_id"] == ws.task_id


async def test_dispatch_root_task_rolls_back_the_workspace_and_reraises_on_failure(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Rollback", "d", root=ws_root, pending_first_hop="research")

    def boom(**_kwargs: Any) -> None:
        raise RuntimeError("enqueue exploded")

    monkeypatch.setattr(workspace_creation, "dispatch_team_core", boom)
    with pytest.raises(RuntimeError, match="enqueue exploded"):
        await workspace_creation.dispatch_root_task_or_rollback(
            ws, "research", None, None, queue, PubSub(), ws_root, merge_gate=_SKIPPED_GATE
        )
    assert workspace_mod.list_workspaces(root=ws_root) == []


async def test_dispatch_root_task_rejects_an_unknown_team_with_an_app_error_and_rolls_back(
    ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Unknown Team", "d", root=ws_root)
    with pytest.raises(AppError) as exc_info:
        await workspace_creation.dispatch_root_task_or_rollback(
            ws, "ghost-team", None, None, queue, PubSub(), ws_root, merge_gate=_SKIPPED_GATE
        )
    assert exc_info.value.status_code == 400
    assert workspace_mod.list_workspaces(root=ws_root) == []


async def test_refine_and_dispatch_writes_the_refine_source_for_a_fresh_workspace(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Refine", "d", root=ws_root, pending_first_hop="research")
    written: list[str] = []
    dispatched: dict[str, Any] = {}

    async def write_source(target: workspace_mod.Workspace) -> None:
        written.append(target.task_id)

    monkeypatch.setattr(
        workspace_creation, "dispatch_team_core", lambda **kwargs: dispatched.update(kwargs)
    )
    body = CreateWorkspaceBody(title="Refine", team="research", instructions="  focus  ")
    await workspace_creation.refine_and_dispatch_root_task(
        ws, "research", body, queue, PubSub(), ws_root, write_source, _SKIPPED_GATE
    )
    assert written == [ws.task_id]
    assert dispatched["team"].name == "brief_refine"
    assert dispatched["instructions"] is None
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions[ws.task_id].text == "focus"


async def test_refine_and_dispatch_skips_the_refine_source_when_refinement_is_over(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("No Refine", "d", root=ws_root, pending_first_hop="research")
    written: list[str] = []
    dispatched: dict[str, Any] = {}

    async def write_source(target: workspace_mod.Workspace) -> None:
        written.append(target.task_id)

    monkeypatch.setattr(workspace_creation, "should_refine", lambda _ws: False)
    monkeypatch.setattr(
        workspace_creation, "dispatch_team_core", lambda **kwargs: dispatched.update(kwargs)
    )
    body = CreateWorkspaceBody(title="No Refine", team="research", instructions="  focus  ")
    await workspace_creation.refine_and_dispatch_root_task(
        ws, "research", body, queue, PubSub(), ws_root, write_source, _SKIPPED_GATE
    )
    assert written == []
    assert dispatched["team"].name == "research"
    assert dispatched["instructions"] == "focus"

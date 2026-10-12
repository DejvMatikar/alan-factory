from __future__ import annotations

from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.teams import _node_attach
from alan.teams._base import run_or_resume_node
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("attach-base-test", "d", root=tmp_path / "ws")


def _seed_interrupted(workspace: Workspace) -> tuple[str, workflow_mod.WorkflowGraph]:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-attach")
    graph.mark_interrupted("w", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    return inv_id, graph


def test_run_or_resume_node_returns_attach_result_when_completed(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted(workspace)

    def fake_attach(*_a: object, **_kw: object) -> _node_attach.AttachOutcome:
        return _node_attach.AttachOutcome(
            completed=True, result_text="attached body", session_id="sid-attach"
        )

    monkeypatch.setattr(_node_attach, "attach", fake_attach)
    monkeypatch.setattr(
        "alan.teams._base.resume_workflow_node",
        lambda *_a, **_kw: pytest.fail("must not resume when attach completed"),
    )
    result = run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="w", role="w", model="sonnet", depends_on=[],
        system_prompt="sys", user_text="txt",
    )
    assert result == "attached body"


def test_run_or_resume_node_falls_back_to_resume_when_attach_returns_none(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted(workspace)
    monkeypatch.setattr(_node_attach, "attach", lambda *_a, **_kw: None)
    resumed: list[str | None] = []

    def fake_resume(
        _ws: object, _graph: object, _inv: str, _emit: object, *, node_id: str, **_kw: object
    ) -> str:
        resumed.append(node_id)
        return "resumed body"

    monkeypatch.setattr("alan.teams._base.resume_workflow_node", fake_resume)
    result = run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="w", role="w", model="sonnet", depends_on=[],
        system_prompt="sys", user_text="txt",
    )
    assert result == "resumed body"
    assert resumed == ["w"]


def test_run_or_resume_node_falls_back_to_resume_when_attach_degrades(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_interrupted(workspace)

    def fake_attach(*_a: object, **_kw: object) -> _node_attach.AttachOutcome:
        return _node_attach.AttachOutcome(
            completed=False, result_text=None, session_id="sid-attach"
        )

    monkeypatch.setattr(_node_attach, "attach", fake_attach)
    resumed: list[str | None] = []

    def fake_resume(
        _ws: object, _graph: object, _inv: str, _emit: object, *, node_id: str, **_kw: object
    ) -> str:
        resumed.append(node_id)
        return "resumed after attach loss"

    monkeypatch.setattr("alan.teams._base.resume_workflow_node", fake_resume)
    result = run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="w", role="w", model="sonnet", depends_on=[],
        system_prompt="sys", user_text="txt",
    )
    assert result == "resumed after attach loss"
    assert resumed == ["w"]

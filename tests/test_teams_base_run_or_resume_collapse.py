from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.teams import _base as base
from alan.teams._static_pipeline import Role, StaticPipelineTeam
from alan.workspace import Workspace, create


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(root: Path) -> Workspace:
    return create("collapse test", "d", root=root)


def _patch_streaming(monkeypatch: pytest.MonkeyPatch, output: str = "agent output") -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        on_session_id: object = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        if session_id is not None and callable(on_session_id):
            on_session_id(session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": output}) + "\n"
            )
        return AgentStreamingResult(result_text=output)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def test_run_workflow_node_is_not_a_public_attribute_of_teams_base() -> None:
    assert not hasattr(base, "run_workflow_node")


def test_run_or_resume_node_reuses_recorded_success_for_retry_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        base, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-run a success node"),
    )
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("judge_retry", role="judge", model="opus")
    graph.complete_node("judge_retry")
    workflow_mod.save_workflow(workspace, graph.doc)
    workflow_mod.save_node_result(
        workspace, inv_id, "judge_retry", role="judge", model="opus",
        result_text="VERDICT: APPROVE\n",
    )
    result = base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="judge_retry", role="judge", model="opus", depends_on=["judge"],
        system_prompt="sys", user_text="txt",
    )
    assert result == "VERDICT: APPROVE\n"


def test_run_or_resume_node_resumes_interrupted_retry_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        resume_session_id: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        captured.append(resume_session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "resumed"}) + "\n"
            )
        return AgentStreamingResult(result_text="resumed")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("clause_auditor_retry", role="clause_auditor", model="sonnet")
    graph.set_session_id("clause_auditor_retry", "sid-retry")
    graph.mark_interrupted("clause_auditor_retry", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    result = base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="clause_auditor_retry", role="clause_auditor", model="sonnet",
        depends_on=["clause_auditor"], system_prompt="sys", user_text="txt",
    )
    assert result == "resumed"
    assert captured == ["sid-retry"]
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    assert doc.tasks[0].status == "success"


def test_run_or_resume_node_starts_fresh_when_node_absent_from_graph(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_streaming(monkeypatch, output="fresh output")
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    result = base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="judge_retry", role="judge", model="opus", depends_on=["judge"],
        system_prompt="sys", user_text="txt",
    )
    assert result == "fresh output"
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    assert doc.tasks[0].id == "judge_retry"
    assert doc.tasks[0].status == "success"


def test_cached_result_for_raises_workflow_error_when_result_doc_unreadable(
    workspace: Workspace,
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("judge_retry", role="judge", model="opus")
    graph.complete_node("judge_retry")
    workflow_mod.save_workflow(workspace, graph.doc)
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        base._cached_result_for(workspace, graph, inv_id, "judge_retry")  # noqa: SLF001


def test_cached_result_for_raises_workflow_error_when_result_doc_read_raises_oserror(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("judge_retry", role="judge", model="opus")
    graph.complete_node("judge_retry")
    workflow_mod.save_workflow(workspace, graph.doc)
    workflow_mod.save_node_result(
        workspace, inv_id, "judge_retry", role="judge", model="opus", result_text="ok"
    )

    def _raise(*_a: object, **_kw: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(workflow_mod, "load_node_result_doc", _raise)
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        base._cached_result_for(workspace, graph, inv_id, "judge_retry")  # noqa: SLF001


def test_cached_result_for_returns_empty_result_text_verbatim(
    workspace: Workspace,
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(workspace, team="verification", tasks=[])
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("judge_retry", role="judge", model="opus")
    graph.complete_node("judge_retry")
    workflow_mod.save_workflow(workspace, graph.doc)
    workflow_mod.save_node_result(
        workspace, inv_id, "judge_retry", role="judge", model="opus", result_text=""
    )
    assert base._cached_result_for(workspace, graph, inv_id, "judge_retry") == ""  # noqa: SLF001


def test_fresh_and_resume_paths_write_identical_result_doc_shape(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        on_session_id: object = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        if session_id is not None and callable(on_session_id):
            on_session_id(session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "body text"}) + "\n"
            )
        return AgentStreamingResult(result_text="body text")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    base._run_fresh_node(  # noqa: SLF001
        workspace, graph, inv_id, lambda _e: None,
        node_id="w", role="w", model="sonnet", depends_on=[],
        system_prompt="p", user_text="u",
    )
    fresh_doc = workflow_mod.load_node_result_doc(workspace, inv_id, "w")
    assert fresh_doc is not None

    graph.mark_interrupted("w", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    graph = workflow_mod.load_graph(workspace, inv_id)
    base.resume_workflow_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="w", system_prompt="p", user_text="u",
    )
    resumed_doc = workflow_mod.load_node_result_doc(workspace, inv_id, "w")
    assert resumed_doc is not None
    assert set(fresh_doc) == set(resumed_doc)


def test_success_node_with_deleted_result_raises_for_static_pipeline_consumer(
    workspace: Workspace,
) -> None:
    role = Role("only_role", "system prompt", "sonnet")
    team = StaticPipelineTeam(name="probe", roles=(role,), to_team_suggestions=())
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team=team.name,
        tasks=[workflow_mod.PendingTaskSpec(id="only_role", role="only_role", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("only_role", role="only_role", model="sonnet")
    graph.complete_node("only_role")
    workflow_mod.save_workflow(workspace, graph.doc)
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        team.run_pipeline(workspace, None, lambda _e: None, inv_id=inv_id)

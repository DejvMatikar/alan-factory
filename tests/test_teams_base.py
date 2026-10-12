from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import turn_policy
from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Handover
from alan.teams import TEAMS
from alan.teams import _base as base
from alan.teams._base import (
    BASE_ALLOWED_TOOLS,
    MAX_TURNS,
    SingleAgentTeam,
    Team,
    TeamEvent,
    WorkerOverrides,
    build_forward_handover,
    make_handover_id,
)
from alan.workflow import config_path, load_node_config, load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import make_handover, seed_and_run


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(root: Path) -> Workspace:
    return create("base test", "d", root=root)


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    output: str = "agent output",
    *,
    captured: list[tuple[str, str, str]] | None = None,
    max_turns_seen: list[int] | None = None,
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if captured is not None:
            captured.append((system_prompt, user_text, model))
        if max_turns_seen is not None:
            max_turns_seen.append(max_turns)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": output}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": output})
        return AgentStreamingResult(result_text=output)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def test_make_handover_id_is_unique_and_prefixed() -> None:
    a = make_handover_id("task-x")
    b = make_handover_id("task-x")
    assert a.startswith("handover_task-x_")
    assert a != b


def test_build_forward_handover_first_iteration(workspace: Workspace) -> None:
    h = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary="ok",
        prior=None,
    )
    assert h.handover_type == "forward"
    assert h.iteration == 1
    assert h.revisit_of is None
    assert h.from_team == "research"
    assert h.task_id == workspace.task_id


def test_build_forward_handover_increments_on_prior(workspace: Workspace) -> None:
    prior = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary="first",
        prior=None,
    )
    later = build_forward_handover(
        workspace,
        from_team="implementation_plan",
        to_team_suggestions=["implementation"],
        summary="second",
        prior=prior,
    )
    assert later.iteration == 2
    assert later.revisit_of == prior.handover_id


def test_build_forward_handover_carries_prior_test_scenarios(
    workspace: Workspace,
) -> None:
    from alan.handover import TestScenario

    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    prior = build_forward_handover(
        workspace,
        from_team="implementation",
        to_team_suggestions=["verification"],
        summary="impl",
        prior=Handover(
            handover_id="h0",
            handover_chain_id="c0",
            handover_type="forward",
            task_id=workspace.task_id,
            from_team="implementation_plan",
            from_invocation_id="inv_000",
            to_team_suggestions=["implementation"],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="plan",
            test_scenarios=[scenario],
        ),
    )
    assert [s.id for s in prior.test_scenarios] == ["s1"]


def test_build_forward_handover_empty_scenarios_when_no_prior(
    workspace: Workspace,
) -> None:
    h = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary="ok",
        prior=None,
    )
    assert h.test_scenarios == []


def test_build_forward_handover_verifier_metrics_default_none(
    workspace: Workspace,
) -> None:
    h = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=[],
        summary="ok",
        prior=None,
    )
    assert h.verifier_completeness is None
    assert h.verifier_confidence is None
    assert h.verifier_gaps == []
    assert h.open_questions == []
    assert h.artifacts_produced == []


def test_build_forward_handover_threads_verifier_metrics(
    workspace: Workspace,
) -> None:
    from alan.handover import ArtifactRef, OpenQuestion

    question = OpenQuestion(
        id="low_completeness", question="score 0.4", resolver_role="human", blocking=True
    )
    artifact = ArtifactRef(path="plan.md", description="the plan")
    h = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=[],
        summary="ok",
        prior=None,
        open_questions=[question],
        artifacts_produced=[artifact],
        verifier_completeness=0.4,
        verifier_confidence=0.6,
        verifier_gaps=["gap a"],
    )
    assert h.open_questions == [question]
    assert h.artifacts_produced == [artifact]
    assert h.verifier_completeness == 0.4
    assert h.verifier_confidence == 0.6
    assert h.verifier_gaps == ["gap a"]


def test_build_forward_handover_preserves_long_synthesis(workspace: Workspace) -> None:
    long = "x" * 5000
    h = build_forward_handover(
        workspace,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary=long,
        prior=None,
    )
    assert h.full_synthesis == long
    assert len(h.full_synthesis) == 5000


def test_single_agent_team_run_no_prior(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    events: list[TeamEvent] = []
    handover = seed_and_run(team, workspace, None, events.append)
    assert captured[0][0] == f"you are research\n\nTurn budget for this run: {MAX_TURNS}"
    assert workspace.meta.title in captured[0][1]
    assert "Prior handover" not in captured[0][1]
    assert handover.from_team == "research"
    assert handover.full_synthesis == "agent output"
    assert handover.from_invocation_id == "inv_001"
    assert [e.kind for e in events] == [
        "node_started",
        "stream",
        "node_completed",
    ]
    for evt in events:
        assert evt.payload["inv_id"] == "inv_001"


def test_single_agent_team_run_with_prior_appends_prior_summary(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, output="next", captured=captured)
    prior = Handover(
        handover_id="handover_x_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior findings",
    )
    team = SingleAgentTeam(
        name="implementation_plan",
        system_prompt="you are planner",
        to_team_suggestions=("implementation",),
    )
    handover = seed_and_run(team, workspace, prior, lambda _e: None)
    assert "Prior handover from research" in captured[0][1]
    assert "prior findings" in captured[0][1]
    assert handover.iteration == 2
    assert handover.revisit_of == "handover_x_001"


def test_single_agent_team_writes_workflow_yaml(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_streaming(monkeypatch)
    team = SingleAgentTeam(
        name="architecture",
        system_prompt="p",
        to_team_suggestions=("implementation_plan",),
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    doc = load_workflow(workflow_path(workspace, "inv_001"))
    assert doc.status == "success"
    assert doc.team == "architecture"
    assert len(doc.tasks) == 1
    assert doc.tasks[0].id == "architecture"
    assert doc.tasks[0].status == "success"
    assert doc.tasks[0].role == "architecture"


def test_single_agent_team_failure_finalizes_workflow_failed(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("stream blew up")

    monkeypatch.setattr(base, "run_agent_streaming", boom)
    team = SingleAgentTeam(
        name="architecture",
        system_prompt="p",
        to_team_suggestions=("implementation_plan",),
    )
    events: list[TeamEvent] = []
    with pytest.raises(RuntimeError, match="stream blew up"):
        seed_and_run(team, workspace, None, events.append)
    doc = load_workflow(workflow_path(workspace, "inv_001"))
    assert doc.status == "failed"
    assert doc.tasks[0].status == "failed"
    assert doc.tasks[0].error == "stream blew up"
    failed_completed = [
        e for e in events
        if e.kind == "node_completed" and '"status":"failed"' in e.payload["node"]
    ]
    assert len(failed_completed) == 1
    assert '"error":"stream blew up"' in failed_completed[0].payload["node"]
    cfg_path = config_path(workspace, "inv_001", "architecture")
    assert cfg_path.exists(), "config must be persisted before run_agent_streaming is called"
    cfg = load_node_config(workspace, "inv_001", "architecture")
    assert cfg is not None
    assert cfg["system_prompt"] == f"p\n\nTurn budget for this run: {MAX_TURNS}"
    assert cfg["model"] == "sonnet"
    assert cfg["permission_mode"] == "auto"
    assert cfg["max_turns"] == MAX_TURNS
    assert cfg["allowed_tools"] == list(BASE_ALLOWED_TOOLS)
    assert isinstance(cfg["user_text"], str) and cfg["user_text"]


def test_single_agent_team_custom_model_propagates(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = SingleAgentTeam(
        name="architecture",
        system_prompt="p",
        to_team_suggestions=(),
        model="opus",
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    assert captured[0][2] == "opus"
    doc = load_workflow(workflow_path(workspace, "inv_001"))
    assert doc.tasks[0].model == "opus"


def test_team_event_namedtuple() -> None:
    evt = TeamEvent("node_started", {"role": "x"})
    assert evt.kind == "node_started"
    assert evt.payload == {"role": "x"}


def test_team_protocol_runtime_check() -> None:
    team: Team = SingleAgentTeam(
        name="x", system_prompt="p", to_team_suggestions=()
    )
    assert team.name == "x"


def test_teams_registry_contains_all_charters() -> None:
    assert set(TEAMS.keys()) == {
        "architecture",
        "brainstorming",
        "brief_refine",
        "decomposer",
        "dependency_sweep",
        "deploy_verify",
        "implementation",
        "implementation_fix",
        "implementation_plan",
        "maintenance",
        "opportunity_scout",
        "pr_merge",
        "research",
        "ui_review",
        "verification",
    }


def test_teams_registry_each_name_matches_key() -> None:
    for key, team in TEAMS.items():
        assert team.name == key


def test_team_to_team_suggestions_per_charter() -> None:
    expected = {
        "architecture": (),
        "brainstorming": (),
        "implementation": ("verification",),
        "implementation_plan": (),
        "maintenance": (),
        "opportunity_scout": (),
        "ui_review": (),
        "verification": (),
    }
    for name, expected_suggestions in expected.items():
        team = TEAMS[name]
        suggestions = getattr(team, "to_team_suggestions", None)
        assert suggestions == expected_suggestions


def test_implementation_team_is_registered() -> None:
    from alan.teams.implementation import ImplementationTeam

    assert isinstance(TEAMS["implementation"], ImplementationTeam)
    assert TEAMS["implementation"].name == "implementation"


def _team_callable_signature_holds(team: Team) -> None:
    assert callable(team.run)


def test_implementation_plan_team_is_in_registry() -> None:
    from alan.teams.implementation_plan import ImplementationPlanTeam

    assert isinstance(TEAMS["implementation_plan"], ImplementationPlanTeam)


def test_team_callable_signature_holds_for_every_team() -> None:
    for team in TEAMS.values():
        _team_callable_signature_holds(team)


def test_single_agent_team_run_with_revisit_prepends_cross_team_context(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.handover import save_handover

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, output="next", captured=captured)

    seed = Handover(
        handover_id=f"handover_{workspace.task_id}_aaa",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="we picked option B",
    )
    save_handover(workspace, seed)

    prior = Handover(
        handover_id=f"handover_{workspace.task_id}_bbb",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_plan"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="updated thinking",
    )
    team = SingleAgentTeam(
        name="implementation_plan",
        system_prompt="you are planner",
        to_team_suggestions=("implementation",),
    )
    seed_and_run(team, workspace, prior, lambda _e: None)
    assert "Prior context for implementation_plan" in captured[0][1]
    assert "we picked option B" in captured[0][1]


def test_workflow_lifecycle_rejects_team_mismatch(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workflow as workflow_mod
    from alan.workflow import PendingTaskSpec, WorkflowError

    _patch_streaming(monkeypatch)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="architecture",
        tasks=[PendingTaskSpec(id="architecture", role="architecture", model="sonnet")],
    )
    team = SingleAgentTeam(
        name="research",
        system_prompt="p",
        to_team_suggestions=(),
    )
    with pytest.raises(WorkflowError, match="team mismatch"):
        team.run(workspace, None, lambda _e: None, inv_id=inv_id)


def test_max_turns_is_flat_ceiling() -> None:
    assert MAX_TURNS == 150


def test_run_or_resume_node_passes_max_turns(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("turns-test", "d", root=root)
    seen: list[int] = []
    _patch_streaming(monkeypatch, max_turns_seen=seen)
    team = SingleAgentTeam(
        name="implementation",
        system_prompt="p",
        to_team_suggestions=("verification",),
    )
    seed_and_run(team, ws, None, lambda _e: None)
    assert seen == [MAX_TURNS]


def test_single_agent_team_run_revisit_with_no_cross_team_falls_back(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, output="next", captured=captured)
    prior = Handover(
        handover_id=f"handover_{workspace.task_id}_only",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_plan"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="some thinking",
    )
    team = SingleAgentTeam(
        name="implementation_plan",
        system_prompt="you are planner",
        to_team_suggestions=("implementation",),
    )
    seed_and_run(team, workspace, prior, lambda _e: None)
    assert "Prior context for" not in captured[0][1]
    assert "Prior handover from brainstorming" in captured[0][1]


def test_run_or_resume_node_persists_session_id_to_disk(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_sid: list[str] = []
    workflow_states_during_call: list[str | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        on_session_id: Callable[[str], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        assert session_id is not None
        assert on_session_id is not None
        captured_sid.append(session_id)
        on_session_id(session_id)
        doc = load_workflow(workflow_path(workspace, "inv_001"))
        workflow_states_during_call.append(doc.tasks[0].claude_session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    assert len(captured_sid) == 1
    assert workflow_states_during_call == [captured_sid[0]]


def test_reject_resume_inv_id_raises_when_set() -> None:
    from alan.teams._base import ResumeUnsupportedError, reject_resume_inv_id

    with pytest.raises(ResumeUnsupportedError, match="does not support resume"):
        reject_resume_inv_id("research", "inv_001")


def test_reject_resume_inv_id_noop_when_none() -> None:
    from alan.teams._base import reject_resume_inv_id

    reject_resume_inv_id("research", None)


def test_single_agent_team_resume_replays_via_resume_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_resume_sids: list[str | None] = []
    captured_session_ids: list[str | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        resume_session_id: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        captured_resume_sids.append(resume_session_id)
        captured_session_ids.append(session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team=team.name,
        tasks=[workflow_mod.PendingTaskSpec(id=team.name, role=team.name, model=team.model)],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node(team.name, role=team.name, model=team.model)
    graph.set_session_id(team.name, "sid-prior")
    graph.mark_interrupted(team.name, error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    events: list[TeamEvent] = []
    handover = team.run(
        workspace, None, events.append, inv_id=inv_id, resume_inv_id=inv_id
    )
    assert captured_resume_sids == ["sid-prior"]
    assert captured_session_ids == [None]
    assert handover.full_synthesis == "ok"


def _capture_effort_fake(
    captured: list[str | None],
) -> Callable[..., AgentStreamingResult]:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        effort: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        captured.append(effort)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    return fake


def test_run_or_resume_node_forwards_effort_to_streaming(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    monkeypatch.setattr(base, "run_agent_streaming", _capture_effort_fake(captured))
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="implementation"
    )
    base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet",
        effort="xhigh", depends_on=[],
        system_prompt="p", user_text="u",
    )
    assert captured == ["xhigh"]


def test_resume_workflow_node_forwards_persisted_effort(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    monkeypatch.setattr(base, "run_agent_streaming", _capture_effort_fake(captured))
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[
            workflow_mod.PendingTaskSpec(
                id="implementer", role="implementer", model="sonnet", effort="xhigh"
            )
        ],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet", effort="xhigh")
    graph.set_session_id("implementer", "sid-prior")
    graph.mark_interrupted("implementer", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    base.resume_workflow_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", system_prompt="p", user_text="u",
    )
    assert captured == ["xhigh"]


def test_single_agent_team_supports_resume_flag() -> None:
    team = SingleAgentTeam(
        name="research",
        system_prompt="x",
        to_team_suggestions=("implementation_plan",),
    )
    assert team.supports_resume is True


def test_resume_workflow_node_rejects_node_without_session_id(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import resume_workflow_node

    monkeypatch.setattr(
        base, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("should not run when session_id is missing"),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.mark_interrupted("w")
    workflow_mod.save_workflow(workspace, graph.doc)
    events: list[TeamEvent] = []
    with pytest.raises(workflow_mod.WorkflowError, match="no claude_session_id"):
        resume_workflow_node(
            workspace,
            graph,
            inv_id,
            events.append,
            node_id="w",
            system_prompt="sys",
            user_text="txt",
        )
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    assert doc.tasks[0].status == "interrupted"
    assert events == []


def test_run_or_resume_node_returns_cached_when_node_success(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import run_or_resume_node

    monkeypatch.setattr(
        base, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-run a success node"),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.complete_node("w")
    workflow_mod.save_workflow(workspace, graph.doc)
    workflow_mod.save_node_result(
        workspace, inv_id, "w", role="w", model="sonnet", result_text="cached body"
    )
    result = run_or_resume_node(
        workspace,
        graph,
        inv_id,
        lambda _e: None,
        node_id="w",
        role="w",
        model="sonnet",
        depends_on=[],
        system_prompt="sys",
        user_text="txt",
    )
    assert result == "cached body"


def test_run_or_resume_node_raises_when_success_node_cache_missing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import run_or_resume_node

    monkeypatch.setattr(
        base, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-run a success node"),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.complete_node("w")
    workflow_mod.save_workflow(workspace, graph.doc)
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        run_or_resume_node(
            workspace,
            graph,
            inv_id,
            lambda _e: None,
            node_id="w",
            role="w",
            model="sonnet",
            depends_on=[],
            system_prompt="sys",
            user_text="txt",
        )


def test_run_or_resume_node_resumes_interrupted_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import run_or_resume_node

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
                json.dumps({"type": "result", "result": "resumed body"}) + "\n"
            )
        return AgentStreamingResult(result_text="resumed body")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-x")
    graph.mark_interrupted("w", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    result = run_or_resume_node(
        workspace,
        graph,
        inv_id,
        lambda _e: None,
        node_id="w",
        role="w",
        model="sonnet",
        depends_on=[],
        system_prompt="sys",
        user_text="txt",
    )
    assert result == "resumed body"
    assert captured == ["sid-x"]


def test_run_or_resume_node_runs_fresh_when_node_missing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import run_or_resume_node

    captured: list[str | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        captured.append(session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "fresh body"}) + "\n"
            )
        return AgentStreamingResult(result_text="fresh body")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="orchestrator", role="orchestrator", model="opus")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    result = run_or_resume_node(
        workspace,
        graph,
        inv_id,
        lambda _e: None,
        node_id="worker_new",
        role="worker_new",
        model="sonnet",
        depends_on=["orchestrator"],
        system_prompt="sys",
        user_text="txt",
    )
    assert result == "fresh body"
    assert len(captured) == 1


def test_resume_workflow_node_unknown_node_id_does_not_emit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._base import resume_workflow_node

    monkeypatch.setattr(
        base, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("should not run for unknown node"),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    events: list[TeamEvent] = []
    with pytest.raises(workflow_mod.WorkflowError, match="unknown node"):
        resume_workflow_node(
            workspace,
            graph,
            inv_id,
            events.append,
            node_id="phantom",
            system_prompt="sys",
            user_text="txt",
        )
    assert events == []


def test_run_or_resume_node_uses_unique_session_ids_per_call(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        session_id: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        assert session_id is not None
        seen.append(session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    other = create("other base test", "d", root=workspace.path.parent)
    seed_and_run(team, other, None, lambda _e: None)
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_run_or_resume_node_concurrent_siblings_do_not_corrupt_workflow(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2, timeout=5.0)

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        barrier.wait(timeout=5.0)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[
            workflow_mod.PendingTaskSpec(id="a", role="a", model="sonnet"),
            workflow_mod.PendingTaskSpec(id="b", role="b", model="sonnet"),
        ],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def run(node_id: str) -> None:
        try:
            results[node_id] = base.run_or_resume_node(
                workspace, graph, inv_id, lambda _e: None,
                node_id=node_id, role=node_id, model="sonnet", depends_on=[],
                system_prompt="p", user_text="u",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(nid,)) for nid in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors
    assert results == {"a": "ok", "b": "ok"}
    doc = load_workflow(workflow_path(workspace, inv_id))
    assert {t.id for t in doc.tasks} == {"a", "b"}
    for task in doc.tasks:
        assert task.status == "success"


def test_overrides_to_kwargs_defaults_to_base_allowed_tools() -> None:
    kwargs = base._overrides_to_kwargs(WorkerOverrides())
    assert kwargs["allowed_tools"] == list(BASE_ALLOWED_TOOLS)
    assert "permission_mode" not in kwargs


def test_overrides_to_kwargs_explicit_allowed_tools_wins() -> None:
    kwargs = base._overrides_to_kwargs(
        WorkerOverrides(allowed_tools=["Edit", "Write"])
    )
    assert kwargs["allowed_tools"] == ["Edit", "Write"]


def test_ux_lens_constant_defined() -> None:
    from alan.teams._base import UX_LENS

    assert isinstance(UX_LENS, str)
    assert "WCAG" in UX_LENS
    assert "empty/loading/error" in UX_LENS
    assert "DESIGN_SYSTEM.md" in UX_LENS
    assert "screens.ts" in UX_LENS
    assert "alan/web/e2e/mobile-audit/baseline.json" in UX_LENS


def test_planning_ux_lens_constant_defined() -> None:
    from alan.teams._base import PLANNING_UX_LENS

    assert isinstance(PLANNING_UX_LENS, str)
    assert "DESIGN_SYSTEM.md" in PLANNING_UX_LENS
    assert "tokens" in PLANNING_UX_LENS
    assert "shared primitives" in PLANNING_UX_LENS
    assert "modal-key convention" in PLANNING_UX_LENS
    assert "new-screen ratchet rule" in PLANNING_UX_LENS


def test_planning_ux_lens_phrased_distinctly_from_ux_lens() -> None:
    from alan.teams._base import PLANNING_UX_LENS, UX_LENS

    assert PLANNING_UX_LENS != UX_LENS
    assert "hand-rolling" not in PLANNING_UX_LENS
    assert "screens.ts" not in PLANNING_UX_LENS
    assert "baseline.json" not in PLANNING_UX_LENS


def test_planning_ux_lens_has_no_severity_vocabulary() -> None:
    from alan.teams._base import PLANNING_UX_LENS

    lowered = PLANNING_UX_LENS.lower()
    for banned in ("must fail", "blocker", "critical", "gate", "fail"):
        assert banned not in lowered, f"{banned!r} must not appear in PLANNING_UX_LENS"


def test_planning_ux_lens_has_no_curly_braces() -> None:
    from alan.teams._base import PLANNING_UX_LENS

    assert "{" not in PLANNING_UX_LENS
    assert "}" not in PLANNING_UX_LENS


def test_planning_ux_lens_within_size_ceiling() -> None:
    from alan.teams._base import PLANNING_UX_LENS

    assert len(PLANNING_UX_LENS) <= 600


def test_planning_screen_capture_instruction_constant_defined() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert isinstance(PLANNING_SCREEN_CAPTURE_INSTRUCTION, str)
    assert "alan/web/" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "capture:screens" in PLANNING_SCREEN_CAPTURE_INSTRUCTION


def test_planning_screen_capture_instruction_reads_own_invocations_pid_dir() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "screen-captures/pid-*" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "new is" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "Never read a directory that was already there" in (
        PLANNING_SCREEN_CAPTURE_INSTRUCTION
    )


def test_planning_screen_capture_instruction_has_exact_degrade_wording() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "capture unavailable — reviewed from markup" in (
        PLANNING_SCREEN_CAPTURE_INSTRUCTION
    )


def test_planning_screen_capture_instruction_states_evidence_boundary() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "composition and visual density" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "empty, loading, or error" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "modals" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "focus behaviour" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "component source remains the ground truth" in (
        PLANNING_SCREEN_CAPTURE_INSTRUCTION
    )


def test_planning_screen_capture_instruction_is_advisory_only() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "must never block" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "plan step" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "decision entry" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "open-question blocker" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "finding" in PLANNING_SCREEN_CAPTURE_INSTRUCTION


def test_planning_screen_capture_instruction_forbids_structured_field_leak() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "free prose" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "fenced yaml block" in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "machine-parsed field" in PLANNING_SCREEN_CAPTURE_INSTRUCTION


def test_planning_screen_capture_instruction_has_no_curly_braces() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert "{" not in PLANNING_SCREEN_CAPTURE_INSTRUCTION
    assert "}" not in PLANNING_SCREEN_CAPTURE_INSTRUCTION


def test_planning_screen_capture_instruction_within_size_ceiling() -> None:
    from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION

    assert len(PLANNING_SCREEN_CAPTURE_INSTRUCTION) <= 2000


def test_design_system_doc_lists_planning_prompts_as_advisory() -> None:
    text = (Path(__file__).parent.parent / "docs" / "DESIGN_SYSTEM.md").read_text()
    idx = text.index("How it's enforced")
    enforced_section = text[idx:]
    assert "brainstorming worker prompt" in enforced_section
    assert "frontend_analyst" in enforced_section
    assert "devil_advocate" in enforced_section
    assert "advisory" in enforced_section
    assert "judge" in enforced_section


def test_base_allowed_tools_contains_documented_gate_patterns() -> None:
    assert "Read" in BASE_ALLOWED_TOOLS
    assert "Grep" in BASE_ALLOWED_TOOLS
    assert "Glob" in BASE_ALLOWED_TOOLS
    assert "Bash(uv run:*)" in BASE_ALLOWED_TOOLS
    assert "Bash(npm run:*)" in BASE_ALLOWED_TOOLS
    assert "Bash(git diff:*)" in BASE_ALLOWED_TOOLS
    assert "Edit" not in BASE_ALLOWED_TOOLS
    assert "Write" not in BASE_ALLOWED_TOOLS
    assert "Bash(git:*)" not in BASE_ALLOWED_TOOLS


def test_single_agent_team_forwards_base_allowed_tools_to_streaming(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str] | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        allowed_tools: list[str] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        seen.append(allowed_tools)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    assert seen == [list(BASE_ALLOWED_TOOLS)]


def test_run_or_resume_node_propagates_usage_to_workflow_task(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(
            result_text="ok",
            usage={"input_tokens": 77, "output_tokens": 123, "total_cost_usd": 0.0009},
        )

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    doc = load_workflow(workflow_path(workspace, "inv_001"))
    task = doc.tasks[0]
    assert task.input_tokens == 77
    assert task.output_tokens == 123
    assert task.total_cost_usd == 0.0009
    assert task.usage_raw is not None
    assert task.usage_raw["input_tokens"] == 77


def test_node_usage_from_dict_float_tokens() -> None:
    from alan.teams._usage import node_usage_from_dict

    usage = node_usage_from_dict({"input_tokens": 100.0, "output_tokens": 200.0})
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 200


def test_node_usage_from_dict_bool_tokens_return_none() -> None:
    from alan.teams._usage import node_usage_from_dict

    usage = node_usage_from_dict({"input_tokens": True, "output_tokens": False})
    assert usage is not None
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_node_usage_from_dict_bool_cost_returns_none() -> None:
    from alan.teams._usage import node_usage_from_dict

    usage = node_usage_from_dict({"total_cost_usd": True})
    assert usage is not None
    assert usage.total_cost_usd is None


def test_format_dispatch_instructions_none_returns_empty() -> None:
    from alan.teams._base import format_dispatch_instructions

    assert format_dispatch_instructions(None) == ""


def test_format_dispatch_instructions_blank_returns_empty() -> None:
    from alan.teams._base import format_dispatch_instructions

    assert format_dispatch_instructions("   \n  \t") == ""


def test_format_dispatch_instructions_strips_and_labels() -> None:
    from alan.teams._base import format_dispatch_instructions

    rendered = format_dispatch_instructions("  Focus on alan/foo.py.  ")
    assert "User dispatch instructions" in rendered
    assert "Focus on alan/foo.py." in rendered


def test_single_agent_team_threads_instructions_into_user_text(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_user_texts: list[str] = []

    def fake(*args: object, **kwargs: object) -> AgentStreamingResult:
        user_text = args[1] if len(args) >= 2 else kwargs.get("user_text", "")
        captured_user_texts.append(str(user_text))
        stream_path = kwargs.get("stream_path")
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "ok"}) + "\n"
            )
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(
        name="research",
        system_prompt="you are research",
        to_team_suggestions=("implementation_plan",),
    )
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team=team.name,
        tasks=[workflow_mod.PendingTaskSpec(id=team.name, role=team.name, model=team.model)],
    )
    team.run(
        workspace,
        None,
        lambda _e: None,
        inv_id=inv_id,
        instructions="Focus narrowly on alan/foo.py:42.",
    )
    assert len(captured_user_texts) == 1
    assert "User dispatch instructions" in captured_user_texts[0]
    assert "Focus narrowly on alan/foo.py:42." in captured_user_texts[0]


def _real_source_lines(func: Callable[..., object]) -> list[str]:
    import inspect

    return inspect.getsourcelines(func)[0]


def test_run_fresh_node_fits_lizard_gate() -> None:
    from alan.teams._base import _run_fresh_node  # noqa: SLF001

    src_lines = _real_source_lines(_run_fresh_node)
    assert len(src_lines) < 46, (
        f"_run_fresh_node is {len(src_lines)} lines; must be ≤45 for Phase-D headroom"
    )


def test_resume_workflow_node_fits_lizard_gate() -> None:
    from alan.teams._base import resume_workflow_node

    src_lines = _real_source_lines(resume_workflow_node)
    assert len(src_lines) < 46, (
        f"resume_workflow_node is {len(src_lines)} lines; must be ≤45 for Phase-D headroom"
    )


def test_complete_node_saves_result_before_completing_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.claude_cli import AgentStreamingResult
    from alan.teams._base import complete_node

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="research",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    call_order: list[str] = []

    orig_save_result = workflow_mod.save_node_result
    orig_save_workflow = workflow_mod.save_workflow
    orig_complete = graph.complete_node

    def _rec_save_result(*a: object, **kw: object) -> None:
        call_order.append("save_node_result")
        orig_save_result(*a, **kw)  # type: ignore[arg-type]

    def _rec_save_workflow(*a: object, **kw: object) -> None:
        call_order.append("save_workflow")
        orig_save_workflow(*a, **kw)  # type: ignore[arg-type]

    def _rec_complete_node(
        nid: str, *, usage: workflow_mod.NodeUsage | None = None
    ) -> workflow_mod.WorkflowTask:
        call_order.append("complete_node")
        return orig_complete(nid, usage=usage)

    monkeypatch.setattr(workflow_mod, "save_node_result", _rec_save_result)
    monkeypatch.setattr(workflow_mod, "save_workflow", _rec_save_workflow)
    monkeypatch.setattr(graph, "complete_node", _rec_complete_node)

    complete_node(
        workspace, graph, inv_id, lambda _e: None, "w",
        AgentStreamingResult(result_text="body"), role="w", model="sonnet",
    )
    assert call_order == ["save_node_result", "complete_node", "save_workflow"]


def test_terseness_block_is_nonempty_string() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    assert isinstance(TERSENESS_BLOCK, str)
    assert len(TERSENESS_BLOCK) > 20
    assert "OUTPUT STYLE" in TERSENESS_BLOCK


def test_compose_system_prompt_default_no_terseness_no_dynamic() -> None:
    from alan.teams._base import TERSENESS_BLOCK, compose_system_prompt

    result = compose_system_prompt("static content")
    assert result == "static content"
    assert TERSENESS_BLOCK not in result


def test_compose_system_prompt_terse_true_appends_terseness() -> None:
    from alan.teams._base import TERSENESS_BLOCK, compose_system_prompt

    result = compose_system_prompt("static", terse=True)
    assert "static" in result
    assert TERSENESS_BLOCK in result


def test_compose_system_prompt_terse_false_no_terseness() -> None:
    from alan.teams._base import TERSENESS_BLOCK, compose_system_prompt

    result = compose_system_prompt("static", terse=False, dynamic_block="dyn")
    assert TERSENESS_BLOCK not in result
    assert "static" in result
    assert "dyn" in result


def test_compose_system_prompt_dynamic_block_appended() -> None:
    from alan.teams._base import compose_system_prompt

    result = compose_system_prompt("static", terse=False, dynamic_block="DYNAMIC")
    assert result.index("static") < result.index("DYNAMIC")


def test_compose_system_prompt_empty_dynamic_excluded() -> None:
    from alan.teams._base import compose_system_prompt

    result = compose_system_prompt("static", terse=False, dynamic_block="")
    assert "DYNAMIC" not in result
    assert result == "static"


def test_compose_system_prompt_terse_ordering() -> None:
    from alan.teams._base import TERSENESS_BLOCK, compose_system_prompt

    result = compose_system_prompt("STATIC", terse=True, dynamic_block="DYNAMIC")
    si = result.index("STATIC")
    ti = result.index(TERSENESS_BLOCK)
    di = result.index("DYNAMIC")
    assert si < ti < di


def test_compose_system_prompt_strips_whitespace() -> None:
    from alan.teams._base import compose_system_prompt

    result = compose_system_prompt("  static  ", terse=False, dynamic_block="  dyn  ")
    assert result.startswith("static")
    assert result.endswith("dyn")


def test_terseness_block_contains_all_machine_parsed_sentinels() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    for sentinel in (
        "EXTRACT_DISPATCH_YAML",
        "INLINE_FINDINGS_YAML",
        "decisions.yaml",
        "decomposition.yaml",
        "VERDICT",
        "CONFIDENCE",
    ):
        assert sentinel in TERSENESS_BLOCK, f"{sentinel!r} missing from TERSENESS_BLOCK"


def test_terseness_block_omits_dead_extract_task_yaml_sentinel() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    assert "EXTRACT_TASK_YAML" not in TERSENESS_BLOCK


def test_terseness_block_preserves_extract_dispatch_yaml_parse_roundtrip() -> None:
    from alan.routes._chat_router import parse_dispatch_proposal_yaml
    from alan.teams._base import compose_system_prompt

    composed = compose_system_prompt("CHAT STUB", terse=True)
    assert "EXTRACT_DISPATCH_YAML" in composed

    valid_yaml = (
        "status: ready\n"
        "proposal:\n"
        "  team: research\n"
        "  rationale: test rationale\n"
        "  counter_case:\n"
        "    against: locks in scope before the shop has bandwidth to review it\n"
        "    cheapest_80: ship the smallest read-only slice first\n"
        "    cost_estimate: insufficient data\n"
    )
    result = parse_dispatch_proposal_yaml(valid_yaml)
    assert result.status == "ready"


def test_terseness_block_covers_triage_yaml_block_names() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    for block_name in ("findings", "patterns", "advisory"):
        assert block_name in TERSENESS_BLOCK, (
            f"triage block {block_name!r} missing from TERSENESS_BLOCK; "
            "a future refactor routing TriageTeam through compose_system_prompt "
            "would silently break parsing"
        )


def test_is_revisit_returns_false_when_no_count(workspace: Workspace) -> None:
    from alan.teams._base import is_revisit

    assert is_revisit(workspace, "implementation_plan", "chain-001") is False


def test_is_revisit_returns_true_when_count_positive(workspace: Workspace) -> None:
    from alan.teams._base import is_revisit
    from alan.workspace import increment_revisit_count

    chain_id = "chain-abc"
    updated_ws = increment_revisit_count(workspace, "implementation_plan", chain_id)
    assert is_revisit(updated_ws, "implementation_plan", chain_id) is True


def test_is_revisit_returns_false_for_different_team(workspace: Workspace) -> None:
    from alan.teams._base import is_revisit
    from alan.workspace import increment_revisit_count

    chain_id = "chain-abc"
    updated_ws = increment_revisit_count(workspace, "research", chain_id)
    assert is_revisit(updated_ws, "implementation_plan", chain_id) is False


def test_single_agent_team_revisit_uses_decisions_only_cross_team(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.handover import save_handover
    from alan.workspace import increment_revisit_count

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, output="next", captured=captured)

    chain_id = f"handover_{workspace.task_id}_chain"

    seed = make_handover(
        workspace.task_id,
        "seed",
        full_synthesis="full synthesis that must NOT appear on revisit",
        decision_statement="use json format",
    )
    save_handover(workspace, seed)

    updated_ws = increment_revisit_count(workspace, "implementation_plan", chain_id)

    prior = make_handover(
        workspace.task_id,
        "prior",
        from_invocation_id="inv_002",
        iteration=2,
        full_synthesis="updated thinking",
    )

    team = SingleAgentTeam(
        name="implementation_plan",
        system_prompt="you are planner",
        to_team_suggestions=("implementation",),
    )
    seed_and_run(team, updated_ws, prior, lambda _e: None)

    user_text = captured[0][1]
    assert "Prior context for implementation_plan" in user_text
    assert "use json format" in user_text
    assert "full synthesis that must NOT appear on revisit" not in user_text


def test_single_agent_team_first_pass_includes_full_summary(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.handover import save_handover

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, output="next", captured=captured)

    seed = make_handover(
        workspace.task_id,
        "seed",
        full_synthesis="first pass synthesis should appear",
    )
    save_handover(workspace, seed)

    prior = make_handover(
        workspace.task_id,
        "prior",
        from_invocation_id="inv_002",
        iteration=2,
        full_synthesis="current thinking",
    )

    team = SingleAgentTeam(
        name="implementation_plan",
        system_prompt="you are planner",
        to_team_suggestions=("implementation",),
    )
    seed_and_run(team, workspace, prior, lambda _e: None)

    user_text = captured[0][1]
    assert "Prior context for implementation_plan" in user_text
    assert "Summary:" in user_text
    assert "first pass synthesis should appear" in user_text


def _write_turns_audit(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    audit_path = tmp_path / "logs" / "node_turns_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_max_turns_for_run_default_off_returns_role_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=500), role="implementer")
    assert decision.applied == 500
    assert decision.derived == 500
    assert decision.source == "static"


def test_max_turns_for_run_off_falls_through_to_flat_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    decision = base._max_turns_for_run(WorkerOverrides(), role="research")
    assert decision.applied == MAX_TURNS


def test_max_turns_for_run_off_never_consults_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("history must not be consulted when adaptive turns are off")

    monkeypatch.setattr(base, "_cached_role_turns", boom)
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=500), role="implementer")
    assert decision.applied == 500


def test_max_turns_for_run_adaptive_derives_below_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    _write_turns_audit(tmp_path, [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 40}
        for i in range(5)
    ])
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=500), role="implementer")
    assert decision.source == "adaptive"
    assert decision.derived == decision.applied
    assert decision.applied == 52
    assert decision.applied < 500


def test_max_turns_for_run_adaptive_never_exceeds_role_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    _write_turns_audit(tmp_path, [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 1000}
        for i in range(5)
    ])
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=150), role="implementer")
    assert decision.applied == 150


def test_max_turns_for_run_adaptive_never_below_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    _write_turns_audit(tmp_path, [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "verification",
         "role": "verifier", "turns": 1}
        for i in range(5)
    ])
    decision = base._max_turns_for_run(WorkerOverrides(), role="verifier")
    assert decision.applied == turn_policy.FLOOR_TURNS


def test_max_turns_for_run_adaptive_below_min_history_rows_returns_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    _write_turns_audit(tmp_path, [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 10}
        for i in range(turn_policy.MIN_HISTORY_ROWS - 1)
    ])
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=500), role="implementer")
    assert decision.applied == 500


def test_max_turns_for_run_excludes_hit_ceiling_rows_from_p95(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    good_rows = [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 40}
        for i in range(5)
    ]
    ceiling_rows = [
        {"task_id": "t", "inv_id": f"inv_c{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 999, "hit_ceiling": True}
        for i in range(20)
    ]
    _write_turns_audit(tmp_path, good_rows + ceiling_rows)
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=2000), role="implementer")
    assert decision.applied == 52


def test_emit_node_turns_audit_hit_ceiling_omits_turns_includes_role(
    workspace: Workspace,
) -> None:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    usage = workflow_mod.NodeUsage(usage_raw={"num_turns": 500})
    graph.complete_node("implementer", usage=usage)
    base.emit_node_turns_audit(
        workspace.task_id, inv_id, "implementation", graph, "implementer", hit_ceiling=True
    )
    audit_path = base.node_turns_audit_dir() / "node_turns_audit.jsonl"
    entry = json.loads(audit_path.read_text().strip().splitlines()[-1])
    assert entry["hit_ceiling"] is True
    assert entry["role"] == "implementer"
    assert "turns" not in entry


def test_single_agent_team_run_accumulates_node_turns_history(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_streaming(monkeypatch, output="ok")
    team = SingleAgentTeam(name="research", system_prompt="p", to_team_suggestions=())
    seed_and_run(team, workspace, None, lambda _e: None)
    audit_path = base.node_turns_audit_dir() / "node_turns_audit.jsonl"
    assert audit_path.exists()
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(e["role"] == "research" and e["team"] == "research" for e in entries)


def test_prepare_node_context_persists_static_turns_source_when_off(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    _patch_streaming(monkeypatch)
    team = SingleAgentTeam(
        name="architecture", system_prompt="p", to_team_suggestions=()
    )
    seed_and_run(team, workspace, None, lambda _e: None)
    cfg = load_node_config(workspace, "inv_001", "architecture")
    assert cfg is not None
    assert cfg["turns_source"] == "static"
    assert cfg["derived_max_turns"] == MAX_TURNS


def test_prepare_node_context_returns_augmented_prompt_with_turn_budget(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    prepared = base._prepare_node_context(
        workspace, inv_id, "node_x", role="implementer",
        system_prompt="BASE PROMPT", user_text="u", overrides=None, model="sonnet",
    )
    assert prepared.effective_max_turns == MAX_TURNS
    assert prepared.augmented_prompt == f"BASE PROMPT\n\nTurn budget for this run: {MAX_TURNS}"
    cfg = load_node_config(workspace, inv_id, "node_x")
    assert cfg is not None
    assert cfg["system_prompt"] == prepared.augmented_prompt


def test_prepare_node_context_turn_budget_reflects_override(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    prepared = base._prepare_node_context(
        workspace, inv_id, "node_y", role="implementer",
        system_prompt="BASE", user_text="u",
        overrides=WorkerOverrides(max_turns=77), model="sonnet",
    )
    assert prepared.effective_max_turns == 77
    assert prepared.augmented_prompt == "BASE\n\nTurn budget for this run: 77"


def test_run_or_resume_node_sends_augmented_prompt_to_agent(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="implementation"
    )
    base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet", depends_on=[],
        system_prompt="BASE PROMPT", user_text="u",
    )
    assert captured[0][0] == f"BASE PROMPT\n\nTurn budget for this run: {MAX_TURNS}"


def test_resume_workflow_node_sends_augmented_prompt_to_agent(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sid-prior")
    graph.mark_interrupted("implementer", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    base.resume_workflow_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", system_prompt="BASE PROMPT", user_text="u",
    )
    assert captured[0][0] == f"BASE PROMPT\n\nTurn budget for this run: {MAX_TURNS}"


def test_run_or_resume_node_adaptive_mode_persists_derived_ceiling(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    _write_turns_audit(tmp_path, [
        {"task_id": "t", "inv_id": f"inv_{i:03d}", "team": "implementation",
         "role": "implementer", "turns": 40}
        for i in range(5)
    ])
    seen: list[int] = []
    _patch_streaming(monkeypatch, max_turns_seen=seen)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="implementation"
    )
    base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet", depends_on=[],
        system_prompt="p", user_text="u",
        overrides=WorkerOverrides(max_turns=500),
    )
    assert seen == [52]
    cfg = load_node_config(workspace, inv_id, "implementer")
    assert cfg is not None
    assert cfg["turns_source"] == "adaptive"
    assert cfg["derived_max_turns"] == 52
    assert cfg["max_turns"] == 52


def test_max_turns_for_run_adaptive_cold_start_no_audit_file_returns_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    decision = base._max_turns_for_run(WorkerOverrides(max_turns=500), role="implementer")
    assert decision.applied == 500
    assert decision.source == "adaptive"


def test_parse_turns_row_bad_json_returns_none() -> None:
    assert base._parse_turns_row("{not valid json") is None


def test_parse_turns_row_non_dict_returns_none() -> None:
    assert base._parse_turns_row(json.dumps([1, 2, 3])) is None


def test_parse_turns_row_missing_role_or_turns_returns_none() -> None:
    assert base._parse_turns_row(json.dumps({"team": "implementation"})) is None
    assert base._parse_turns_row(json.dumps({"role": "implementer", "turns": "nope"})) is None
    assert base._parse_turns_row(json.dumps({"role": 5, "turns": 10})) is None


def test_read_role_turns_returns_empty_dict_on_os_error(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    assert base._read_role_turns(directory) == {}


def test_cached_role_turns_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "node_turns_audit.jsonl"
    assert base._cached_role_turns(missing) == {}


def test_cached_role_turns_reuses_cache_on_unchanged_file(tmp_path: Path) -> None:
    audit_path = tmp_path / "node_turns_audit.jsonl"
    audit_path.write_text(
        json.dumps({"role": "implementer", "turns": 10}) + "\n", encoding="utf-8"
    )
    first = base._cached_role_turns(audit_path)
    second = base._cached_role_turns(audit_path)
    assert first == second
    assert second is base._ROLE_TURNS_CACHE[audit_path][2]


def test_untrusted_content_notice_reexported_from_claude_cli_single_source() -> None:
    from alan import claude_cli

    assert base.UNTRUSTED_CONTENT_NOTICE is claude_cli.UNTRUSTED_CONTENT_NOTICE


def test_untrusted_content_notice_in_base_module_all() -> None:
    assert "UNTRUSTED_CONTENT_NOTICE" in base.__all__


def test_require_grounded_cwd_noop_when_overrides_none() -> None:
    base._require_grounded_cwd(
        None, task_id="t", inv_id="inv_001",
        team="research", role="research", node_id="research",
    )


def test_require_grounded_cwd_noop_when_cwd_none() -> None:
    base._require_grounded_cwd(
        WorkerOverrides(cwd=None), task_id="t", inv_id="inv_001",
        team="research", role="research", node_id="research",
    )


def test_require_grounded_cwd_noop_when_valid_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    base._require_grounded_cwd(
        WorkerOverrides(cwd=checkout), task_id="t", inv_id="inv_001",
        team="implementation", role="implementer", node_id="implementer",
    )


def test_require_grounded_cwd_raises_and_audits_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ungrounded = tmp_path / "not-a-repo"
    ungrounded.mkdir()
    with pytest.raises(base.UngroundedRunError):
        base._require_grounded_cwd(
            WorkerOverrides(cwd=ungrounded), task_id="task-x", inv_id="inv_002",
            team="implementation_fix", role="implementer", node_id="implementer",
        )
    audit_path = tmp_path / "logs" / "grounding_audit.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "ungrounded_cwd_blocked"
    assert entry["task_id"] == "task-x"
    assert entry["inv_id"] == "inv_002"
    assert entry["team"] == "implementation_fix"
    assert entry["role"] == "implementer"
    assert entry["node_id"] == "implementer"
    assert entry["cwd"] == str(ungrounded)
    assert entry["reason"]


def test_require_grounded_cwd_raises_when_checkout_vanished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    vanished = tmp_path / "vanished-checkout"
    (vanished / ".git").mkdir(parents=True)
    shutil.rmtree(vanished)
    with pytest.raises(base.UngroundedRunError):
        base._require_grounded_cwd(
            WorkerOverrides(cwd=vanished), task_id="task-y", inv_id="inv_003",
            team="verification", role="judge", node_id="judge",
        )


def test_grounding_audit_path_honours_logs_root_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "custom-logs"))
    assert base.grounding_audit_path() == tmp_path / "custom-logs" / "grounding_audit.jsonl"


def test_grounding_audit_path_defaults_under_node_turns_audit_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert base.grounding_audit_path() == base.node_turns_audit_dir() / "grounding_audit.jsonl"


def test_run_or_resume_node_blocks_before_spawn_on_ungrounded_cwd(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker for an ungrounded cwd")

    monkeypatch.setattr(base, "run_agent_streaming", boom)
    ungrounded = tmp_path / "not-a-repo"
    ungrounded.mkdir()
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    with pytest.raises(base.UngroundedRunError):
        base.run_or_resume_node(
            workspace, graph, inv_id, lambda _e: None,
            node_id="implementer", role="implementer", model="sonnet", depends_on=[],
            system_prompt="p", user_text="u",
            overrides=WorkerOverrides(cwd=ungrounded),
        )
    doc = load_workflow(workflow_path(workspace, inv_id))
    assert doc.tasks[0].status == "pending"


def test_resume_workflow_node_blocks_before_resume_on_ungrounded_cwd(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker for an ungrounded cwd")

    monkeypatch.setattr(base, "run_agent_streaming", boom)
    ungrounded = tmp_path / "not-a-repo"
    ungrounded.mkdir()
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sid-x")
    graph.mark_interrupted("implementer", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    graph = workflow_mod.load_graph(workspace, inv_id)
    with pytest.raises(base.UngroundedRunError):
        base.resume_workflow_node(
            workspace, graph, inv_id, lambda _e: None,
            node_id="implementer", system_prompt="p", user_text="u",
            overrides=WorkerOverrides(cwd=ungrounded),
        )
    doc = load_workflow(workflow_path(workspace, inv_id))
    assert doc.tasks[0].status == "interrupted"


def test_workflow_lifecycle_finalizes_failed_when_guard_raises(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ungrounded = tmp_path / "not-a-repo"
    ungrounded.mkdir()
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    with pytest.raises(base.UngroundedRunError):
        with base.workflow_lifecycle(
            workspace, team_name="implementation", inv_id=inv_id, emit=lambda _e: None
        ) as graph:
            base.run_or_resume_node(
                workspace, graph, inv_id, lambda _e: None,
                node_id="implementer", role="implementer", model="sonnet", depends_on=[],
                system_prompt="p", user_text="u",
                overrides=WorkerOverrides(cwd=ungrounded),
            )
    doc = load_workflow(workflow_path(workspace, inv_id))
    assert doc.status == "failed"

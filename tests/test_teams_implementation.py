from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from alan import process_runner
from alan.claude_cli import AgentStreamingResult, ProcessConfig, TurnCeilingReached
from alan.compression import DEFAULT_MAX_CHARS
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import ArtifactRef, Decision, Handover, OpenQuestion, TestScenario
from alan.secrets_store import ASKPASS_TOKEN_ENV
from alan.slot_pool import SlotPool, set_active_pool
from alan.teams import TEAMS, _forge_errors, _pr_body, _pr_branch
from alan.teams import _base as base_mod
from alan.teams._base import BASE_ALLOWED_TOOLS, TeamEvent
from alan.teams.implementation import (
    _SYSTEM_PROMPT,
    ALLOWED_TOOLS,
    TEAM,
    ImplementationTeam,
    _backstop_required,
    _drift_gate_check,
)
from alan.workflow import load_workflow, workflow_path
from alan.workspace import Task, TaskDoDClause, TaskStatus, Workspace, create
from tests._helpers import seed_and_run

_FAKE_TRANSPORT_TOKEN = "test-transport-token"  # noqa: S105  # nosec B105


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", _FAKE_TRANSPORT_TOKEN)


@pytest.fixture(autouse=True)
def _ambient_worktree_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_WORKTREE_REPO_ROOT", raising=False)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)  # noqa: S603
    (repo / "README.md").write_text("seed\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "initial"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("impl-task", "do the thing", root=tmp_path / "ws")


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *,
    result_text: str = "## Summary\nDone.",
    write_files: dict[str, str] | None = None,
    commit_subject: str = "feat(impl): add work",
    capture: list[dict[str, object]] | None = None,
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,  # noqa: ARG001
        max_turns: int = 10,
        proc_config: ProcessConfig | None = None,
        allowed_tools: list[str] | None = None,
        permission_mode: str = "auto",
        resume_session_id: str | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        _cwd = proc_config.cwd if proc_config is not None else None
        if capture is not None:
            capture.append({
                "system_prompt": system_prompt,
                "user_text": user_text,
                "cwd": _cwd,
                "allowed_tools": allowed_tools,
                "permission_mode": permission_mode,
                "max_turns": max_turns,
                "resume_session_id": resume_session_id,
                "model": model,
            })
        if _cwd is not None and write_files is not None:
            for rel_path, content in write_files.items():
                target = _cwd / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            subprocess.run(  # noqa: S603,S607
                ["git", "add", "-A"],
                cwd=str(_cwd),
                check=True,
                capture_output=True,
            )
            subprocess.run(  # noqa: S603,S607
                ["git", "commit", "-q", "-m", commit_subject],
                cwd=str(_cwd),
                check=True,
                capture_output=True,
            )
        return AgentStreamingResult(result_text=result_text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    _ = repo


def _prior_plan_handover(workspace: Workspace) -> Handover:
    return Handover(
        handover_id="handover_plan_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation_plan",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="d1",
                statement="use json storage",
                rationale="lighter than sqlite",
                confidence=0.85,
                reversibility="medium",
            )
        ],
        artifacts_produced=[
            ArtifactRef(path="plan.md", description="the plan"),
        ],
        forbidden_actions=["do not edit /etc/anything"],
        full_synthesis="## Plan\n- step 1\n- step 2",
    )


def test_team_registration() -> None:
    assert TEAMS["implementation"] is TEAM
    assert isinstance(TEAM, ImplementationTeam)
    assert TEAM.name == "implementation"
    assert TEAM.to_team_suggestions == ("verification",)


def test_plan_workflow_returns_single_implementer_node(workspace: Workspace) -> None:
    specs = TEAM.plan_workflow(workspace, None)
    assert len(specs) == 1
    assert specs[0].id == "implementer"
    assert specs[0].role == "implementer"
    assert specs[0].model == "sonnet"
    assert specs[0].effort == "xhigh"


def test_system_prompt_documents_worktree_env() -> None:
    assert "uv run --no-active" in _SYSTEM_PROMPT
    assert "npm --prefix alan/web" in _SYSTEM_PROMPT
    assert "NO .venv" in _SYSTEM_PROMPT
    assert "uv sync --locked --extra dev" in _SYSTEM_PROMPT
    assert "--no-emit-project" in _SYSTEM_PROMPT
    assert "--disable-pip -r <(uv export" in _SYSTEM_PROMPT
    assert "cwd PERSISTS" in _SYSTEM_PROMPT
    assert "drift_gate" in _SYSTEM_PROMPT


def test_system_prompt_treats_a_ratchet_flagged_own_spec_as_the_workers_defect() -> None:
    assert "A red spec there is your own defect to fix before stopping, never a flake" in (
        _SYSTEM_PROMPT
    )
    assert "CI's flake ratchet names any spec, not only one this diff adds or edits" in (
        _SYSTEM_PROMPT
    )
    assert "`GATE_FAILING_TEST:` lines" in _SYSTEM_PROMPT
    assert "never widen a" in _SYSTEM_PROMPT.lower()
    assert "flake-quarantine issue" in _SYSTEM_PROMPT


def test_system_prompt_success_requires_full_coverage_gate() -> None:
    assert "fail_under=100" in _SYSTEM_PROMPT
    assert "NO path args" in _SYSTEM_PROMPT
    assert "--no-cov" in _SYSTEM_PROMPT


def test_system_prompt_references_design_system() -> None:
    assert "DESIGN_SYSTEM.md" in _SYSTEM_PROMPT


def test_implementation_prompt_has_screen_capture_bullet() -> None:
    assert "capture:screens" in _SYSTEM_PROMPT
    assert "screen-captures" in _SYSTEM_PROMPT


def test_system_prompt_has_ux_lens() -> None:
    from alan.teams._base import UX_LENS

    assert UX_LENS in _SYSTEM_PROMPT


def test_system_prompt_excludes_terseness_block() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    assert TERSENESS_BLOCK not in _SYSTEM_PROMPT


def test_run_creates_worktree_invokes_streaming_then_cleans_up(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    captured: list[dict[str, object]] = []
    _patch_streaming(
        monkeypatch,
        repo,
        write_files={"feature.py": "print('hi')\n"},
        commit_subject="feat(impl): add feature.py",
        capture=captured,
    )
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, None, events.append)

    assert len(captured) == 1
    worktree_path = captured[0]["cwd"]
    assert isinstance(worktree_path, Path)
    assert not worktree_path.exists()

    assert handover.from_team == "implementation"
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert handover.forbidden_actions
    assert handover.decisions
    assert handover.artifacts_produced

    node_kinds = [e.kind for e in events if e.kind in {"node_started", "node_completed"}]
    assert node_kinds == ["node_started", "node_completed"]


def test_run_forwards_allowed_tools_to_worker(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert captured[0]["allowed_tools"] == list(ALLOWED_TOOLS)
    tools = captured[0]["allowed_tools"]
    assert isinstance(tools, list)
    assert "Edit" in tools
    assert "Bash(git:*)" in tools
    assert "WebSearch" not in tools
    for base_tool in BASE_ALLOWED_TOOLS:
        assert base_tool in tools


def test_run_uses_pinned_non_default_engine_instructions(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, Posture, RepoIdentity, RoleConfig
    from alan.workspace import EngineSnapshot

    cfg = EngineConfig(
        repo=RepoIdentity(owner="rustco", repo="cargo-service"),
        gate_commands=[["cargo", "test"], ["cargo", "clippy"]],
        warmup_commands=[["cargo", "fetch"]],
        roles={"implementer": RoleConfig(model="opus", effort="high")},
        posture=Posture.autonomous,
    )
    ws = workspace_mod.stamp_engine_snapshot(
        workspace, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)

    seed_and_run(TEAM, ws, None, lambda _e: None)

    prompt = captured[0]["system_prompt"]
    assert isinstance(prompt, str)
    assert prompt != _SYSTEM_PROMPT
    assert "cargo test" in prompt
    assert "fail_under=100" not in prompt
    assert captured[0]["model"] == "opus"


def test_run_forwards_accept_edits_permission_mode_to_worker(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert captured[0]["permission_mode"] == "acceptEdits"


def test_run_user_text_includes_prior_plan_and_forbidden_actions(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    prior = _prior_plan_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "Prior plan" in user_text
    assert "implementation_plan" in user_text
    assert "use json storage" in user_text
    assert "plan.md" in user_text
    assert "do not edit /etc/anything" in user_text
    assert "do not push" in user_text


def test_plan_implementation_steps_extracts_bounded_section() -> None:
    from alan.teams.implementation import _plan_implementation_steps

    text = (
        "## Context\nsome context\n\n"
        "## Implementation steps\n1. do x\n2. do y\n\n"
        "## Risks and mitigations\nrisk text\n"
    )
    assert (
        _plan_implementation_steps(text)
        == "## Implementation steps\n1. do x\n2. do y"
    )


def test_plan_implementation_steps_runs_to_end_when_last_section() -> None:
    from alan.teams.implementation import _plan_implementation_steps

    text = "## Context\nsome context\n\n## Implementation steps\n1. do x\n2. do y\n"
    assert (
        _plan_implementation_steps(text)
        == "## Implementation steps\n1. do x\n2. do y"
    )


def test_plan_implementation_steps_falls_back_to_full_text_when_header_missing() -> None:
    from alan.teams.implementation import _plan_implementation_steps

    text = "no steps header in this plan"
    assert _plan_implementation_steps(text) == text


def test_prior_plan_block_falls_back_byte_identically_without_plan_synthesis(
    workspace: Workspace,
) -> None:
    from alan.teams.implementation import _prior_plan_block

    long_text = "y" * (DEFAULT_MAX_CHARS + 500)
    prior = _prior_plan_handover(workspace).model_copy(
        update={"full_synthesis": long_text, "plan_synthesis": None}
    )
    assert prior.plan_synthesis is None
    expected = (
        f"\n\n## Prior plan (from {prior.from_team}, iteration {prior.iteration})"
        f"\n{long_text[:DEFAULT_MAX_CHARS]}"
    )
    assert _prior_plan_block(prior) == expected


def test_base_cross_team_context_render_still_slices_at_default_max_chars(
    workspace: Workspace,
) -> None:
    from alan.teams._base import build_prior_context_user_text

    tail_marker = "UNIQUE_TAIL_MARKER_BEYOND_THE_SLICE"
    long_text = ("z" * DEFAULT_MAX_CHARS) + tail_marker
    prior = _prior_plan_handover(workspace).model_copy(
        update={"full_synthesis": long_text, "iteration": 1}
    )
    rendered = build_prior_context_user_text("some_team", workspace, prior)
    assert long_text[:DEFAULT_MAX_CHARS] in rendered
    assert tail_marker not in rendered


def test_plan_steps_delivered_through_decomposer_keep_chain(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.decomposer import Decomposition, _build_keep_handover

    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)

    long_step = "x" * (DEFAULT_MAX_CHARS + 500)
    plan_full_synthesis = (
        "## Context\nsome context\n\n"
        f"## Implementation steps\n1. {long_step}\n\n"
        "## Risks and mitigations\nnone\n"
    )
    assert len(plan_full_synthesis) > DEFAULT_MAX_CHARS

    plan_prior = _prior_plan_handover(workspace).model_copy(
        update={"full_synthesis": plan_full_synthesis}
    )
    keep_handover = _build_keep_handover(
        workspace,
        plan_prior,
        "inv_decomp_001",
        "keep — single subsystem, <= 5 files",
        Decomposition(split=False, rationale="single subsystem, <= 5 files", subtasks=[]),
    )
    assert keep_handover.plan_synthesis == plan_full_synthesis

    seed_and_run(TEAM, workspace, keep_handover, lambda _e: None)
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert long_step in user_text
    assert "## Risks and mitigations" not in user_text


def test_max_turns_upscaled(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert captured[0]["max_turns"] == 500


def test_max_turns_upscaled_with_prior(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    prior = _prior_plan_handover(workspace).model_copy(
        update={"files_to_edit": [f"f{i}.py" for i in range(17)]}
    )
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert captured[0]["max_turns"] == 500


def test_files_to_edit_appears_in_user_text(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    prior = _prior_plan_handover(workspace).model_copy(
        update={"files_to_edit": ["alan/router.py", "tests/test_router.py"]}
    )
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "Files to edit" in user_text
    assert "- alan/router.py" in user_text
    assert "- tests/test_router.py" in user_text


def test_implementation_handover_propagates_test_scenarios_from_prior(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    _patch_streaming(monkeypatch, repo)
    scenarios = [
        TestScenario(
            id="propagate_s1",
            description="planner scenario must reach verification",
            given="planner emits test_scenarios",
            when="implementation team forwards to verification",
            then="verification.prior.test_scenarios is non-empty",
        )
    ]
    prior = _prior_plan_handover(workspace).model_copy(
        update={"test_scenarios": scenarios}
    )
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [s.id for s in handover.test_scenarios] == ["propagate_s1"]


def test_implementation_handover_defaults_to_empty_scenarios_without_prior(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    _patch_streaming(monkeypatch, repo)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.test_scenarios == []


def test_run_includes_baseline_forbidden_when_prior_is_none(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "Prior plan" not in user_text


def test_run_marks_workflow_failed_on_worker_exception(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("worker crashed")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)
    with pytest.raises(RuntimeError, match="worker crashed"):
        seed_and_run(TEAM, workspace, None, lambda _e: None)
    doc = load_workflow(workflow_path(workspace, "inv_001"))
    assert doc.status == "failed"


def test_run_resume_invokes_streaming_with_prior_session_id(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workflow as workflow_mod

    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    specs = TEAM.plan_workflow(workspace, None)
    inv_id = workflow_mod.seed_pending_workflow(workspace, team=TEAM.name, tasks=specs)
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sid-prior-impl")
    graph.mark_interrupted("implementer", error="interrupted_by_restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    TEAM.run(workspace, None, lambda _e: None, inv_id=inv_id, resume_inv_id=inv_id)
    assert len(captured) == 1
    assert captured[0]["resume_session_id"] == "sid-prior-impl"
    assert captured[0]["permission_mode"] == "acceptEdits"


def test_run_persists_slot_index_with_active_pool(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workflow as workflow_mod

    pool = SlotPool(root=repo.parent / "slots", size=2, repo_root=str(repo))
    set_active_pool(pool)
    monkeypatch.chdir(repo)
    _patch_streaming(monkeypatch, repo)
    try:
        specs = TEAM.plan_workflow(workspace, None)
        inv_id = workflow_mod.seed_pending_workflow(
            workspace, team=TEAM.name, tasks=specs
        )
        TEAM.run(workspace, None, lambda _e: None, inv_id=inv_id)
    finally:
        set_active_pool(None)
    doc = load_workflow(workflow_path(workspace, inv_id))
    assert doc.slot_index in {0, 1}


def test_run_resume_reuses_recorded_slot_with_active_pool(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workflow as workflow_mod

    pool = SlotPool(root=repo.parent / "slots", size=2, repo_root=str(repo))
    set_active_pool(pool)
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    try:
        specs = TEAM.plan_workflow(workspace, None)
        inv_id = workflow_mod.seed_pending_workflow(
            workspace, team=TEAM.name, tasks=specs
        )
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.start_node("implementer", role="implementer", model="sonnet")
        graph.set_session_id("implementer", "sid-prior-impl")
        graph.set_slot_index(1)
        graph.mark_interrupted("implementer", error="interrupted_by_restart")
        workflow_mod.save_workflow(workspace, graph.doc)
        TEAM.run(workspace, None, lambda _e: None, inv_id=inv_id, resume_inv_id=inv_id)
    finally:
        set_active_pool(None)
    assert len(captured) == 1
    assert captured[0]["resume_session_id"] == "sid-prior-impl"
    assert Path(str(captured[0]["cwd"])).name == "slot_1"


def test_run_no_commits_yields_empty_decisions(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    _patch_streaming(monkeypatch, repo, write_files=None)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.decisions == []
    assert handover.artifacts_produced == []
    assert handover.forbidden_actions


def test_run_preserves_long_synthesis(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    long_text = "x" * 5000
    _patch_streaming(monkeypatch, repo, result_text=long_text)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert long_text in handover.full_synthesis
    assert len(handover.full_synthesis) >= 5000


def test_run_revisit_increments_iteration_and_carries_revisit_of(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    _patch_streaming(monkeypatch, repo)
    prior = Handover(
        handover_id="handover_prior_42",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation_plan",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation"],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="x",
    )
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.iteration == 4
    assert handover.revisit_of == "handover_prior_42"


def test_open_question_prior_does_not_show_decisions_block_when_empty(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    prior = Handover(
        handover_id="handover_noq_1",
        handover_type="clarification_request",
        task_id=workspace.task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        open_questions=[
            OpenQuestion(
                id="q1",
                question="storage?",
                resolver_role="decomposer",
                blocking=True,
            )
        ],
        full_synthesis="ok",
    )
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "Decisions inherited" not in user_text
    assert "Prior artifacts" not in user_text
    assert "Forbidden actions" in user_text


def test_commit_sha_filter_drops_non_sha_lines(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import implementation as impl_mod

    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        impl_mod,
        "captured_commits",
        lambda _handle, limit=20: [  # noqa: ARG005
            "abc1234 feat: real",
            "garbage line without sha",
            "",
        ],
    )
    _patch_streaming(monkeypatch, repo)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    paths = [a.path for a in handover.artifacts_produced]
    assert paths == ["abc1234"]
    assert len(handover.decisions) == 2


def test_decisions_rationale_pulls_from_synthesis_head(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch,
        repo,
        result_text="## Summary\nFirst line of summary.\n\n## Decisions\n- d1",
        write_files={"a.py": "x\n"},
    )
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.decisions
    rationale = handover.decisions[0].rationale
    assert "First line" in rationale or "Summary" in rationale


def test_decisions_rationale_falls_back_when_synthesis_blank(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch, repo, result_text="   \n\n", write_files={"a.py": "x\n"}
    )
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.decisions
    assert handover.decisions[0].rationale == "see implementation synthesis"


def test_dry_run_skips_push_and_pr(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch,
        repo,
        write_files={"feature.py": "print('hi')\n"},
        commit_subject="feat: thing",
    )

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run should not be called in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    audit_path = tmp_path / "logs" / "impl_audit.jsonl"
    assert audit_path.exists()
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert any(e["action"] == "dry_run" for e in entries)


def test_live_push_and_pr_extracts_pr_number(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch,
        repo,
        write_files={"a.py": "x\n"},
        commit_subject="feat: a",
    )

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:2] == ["gh", "pr"]:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout="https://github.com/owner/repo/pull/123\n",
                stderr="",
            )
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number == 123
    push_cmds = [c for c in calls if c[0] == "git" and "push" in c]
    pr_cmds = [c for c in calls if c[:2] == ["gh", "pr"]]
    assert len(push_cmds) == 1
    assert len(pr_cmds) == 1
    assert "--head" in pr_cmds[0]


def test_push_failure_yields_no_pr_number(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch,
        repo,
        write_files={"a.py": "x\n"},
    )

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(
                cmd, 128, stdout="", stderr="fatal: no remote"
            )
        raise AssertionError(f"gh pr create should not run after push failure: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []
    assert "no PR is available" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "push_failed" for e in entries)
    assert handover.credential_failure is None


def test_push_failure_with_expired_token_sets_credential_failure(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="Bad credentials"
            )
        raise AssertionError(f"gh pr create should not run after push failure: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    assert handover.credential_failure == "expired_token"
    assert "stable fallback name" in handover.full_synthesis


def test_pr_create_failure_yields_no_pr_number(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:2] == ["gh", "pr"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="gh auth failure"
            )
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []
    assert "no PR is available" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_create_failed" for e in entries)


def test_pr_number_unparseable_returns_none(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:2] == ["gh", "pr"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="something else, no pull URL\n", stderr=""
            )
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    assert "no PR is available" in handover.full_synthesis


def test_push_skipped_when_no_commits(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "skip" for e in entries)


def test_push_timeout_logs_and_returns_none(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    assert "no PR is available" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "push_failed" for e in entries)


def test_pr_create_timeout_audits_and_raises_for_retry(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:2] == ["gh", "pr"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, None, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_create_failed" for e in entries)


def test_run_subprocess_wrapper_real_invocation(tmp_path: Path) -> None:
    result = process_runner.run_subprocess(
        ["echo", "hello"], cwd=str(tmp_path), timeout=10.0
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_dry_run_enabled_helper(monkeypatch):
    from alan.teams.implementation import dry_run_enabled

    for val in ("1", "true", "yes", "on", "TRUE", "On"):
        monkeypatch.setenv("ALAN_IMPL_DRY_RUN", val)
        assert dry_run_enabled() is True
    for val in ("0", "false", "no", ""):
        monkeypatch.setenv("ALAN_IMPL_DRY_RUN", val)
        assert dry_run_enabled() is False
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    assert dry_run_enabled() is False


def test_audit_log_file_mode_is_owner_only(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch, repo, write_files={"a.py": "x\n"}
    )
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    audit_path = tmp_path / "logs" / "impl_audit.jsonl"
    assert audit_path.exists()
    mode = audit_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0


def test_audit_dir_mkdir_failure_swallowed(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    blocker = tmp_path / "logs-blocker"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a dir")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(blocker))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None


def test_audit_fdopen_failure_swallowed(
    workspace, repo, monkeypatch, tmp_path, caplog
):

    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "impl_audit.jsonl").mkdir()
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run should not be invoked in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    caplog.set_level("WARNING")
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert any(
        "impl audit write failed" in r.message for r in caplog.records
    )


def test_audit_fdopen_partial_failure(workspace, repo, monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    real_fdopen = os.fdopen

    def boom_fdopen(fd, *args, **kwargs):
        if args and args[0] != "a":
            return real_fdopen(fd, *args, **kwargs)
        raise OSError("disk gone")

    monkeypatch.setattr(os, "fdopen", boom_fdopen)

    def stub_run(*_a, **_k):
        raise AssertionError("subprocess.run should not run in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", stub_run)
    caplog.set_level("WARNING")
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert any("impl audit write failed" in r.message for r in caplog.records)


def test_audit_write_after_fdopen_failure_logged(workspace, repo, monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    class FailingHandle:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def write(self, _data):
            raise OSError("disk full mid-write")

    real_fdopen = os.fdopen

    def fake_fdopen(fd, *args, **kwargs):
        if args and args[0] != "a":
            return real_fdopen(fd, *args, **kwargs)
        os.close(fd)
        return FailingHandle()

    monkeypatch.setattr(os, "fdopen", fake_fdopen)

    def stub_run(*_a, **_k):
        raise AssertionError("subprocess.run should not run in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", stub_run)
    caplog.set_level("WARNING")
    seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert any("impl audit write failed" in r.message for r in caplog.records)


def test_turn_ceiling_forwards_committed_work_with_push(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(
        system_prompt: str,
        user_text: str,
        *,
        proc_config: ProcessConfig | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        cwd = proc_config.cwd if proc_config is not None else None
        if cwd is not None:
            (cwd / "wip.py").write_text("x = 1\n")
            subprocess.run(  # noqa: S603,S607
                ["git", "add", "-A"], cwd=str(cwd), check=True, capture_output=True
            )
            subprocess.run(  # noqa: S603,S607
                ["git", "commit", "-q", "-m", "wip: partial work"],
                cwd=str(cwd), check=True, capture_output=True,
            )
        raise TurnCeilingReached("ceiling hit; num_turns=500")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    push_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        push_calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "create"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/o/r/pull/77\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)

    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)

    assert any(c[0] == "git" and "push" in c for c in push_calls)
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert handover.pr_number == 77
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "ceiling_salvage"
        and e["commit_count"] > 0
        and e["has_commits"] is True
        for e in entries
    )


def test_turn_ceiling_no_push_when_no_commits(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(*_a: object, **_k: object) -> AgentStreamingResult:
        raise TurnCeilingReached("ceiling hit; num_turns=1")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("push must not be called when no commits")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)

    with pytest.raises(TurnCeilingReached):
        seed_and_run(TEAM, workspace, None, lambda _e: None)

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "ceiling_salvage" and e["commit_count"] == 0 for e in entries)


def test_turn_ceiling_forwards_without_push_in_dry_run(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(
        system_prompt: str,
        user_text: str,
        *,
        proc_config: ProcessConfig | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        cwd = proc_config.cwd if proc_config is not None else None
        if cwd is not None:
            (cwd / "wip.py").write_text("x = 1\n")
            subprocess.run(  # noqa: S603,S607
                ["git", "add", "-A"], cwd=str(cwd), check=True, capture_output=True
            )
            subprocess.run(  # noqa: S603,S607
                ["git", "commit", "-q", "-m", "wip: partial work"],
                cwd=str(cwd), check=True, capture_output=True,
            )
        raise TurnCeilingReached("ceiling hit; num_turns=500")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("push must not be called in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)

    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "ceiling_salvage" and e["has_commits"] is True for e in entries
    )


def test_turn_ceiling_still_fails_when_tree_genuinely_clean(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(*_a: object, **_k: object) -> AgentStreamingResult:
        raise TurnCeilingReached("ceiling hit; num_turns=1")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("push must not be called when there is nothing to salvage")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)

    with pytest.raises(TurnCeilingReached):
        seed_and_run(TEAM, workspace, None, lambda _e: None)

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "ceiling_salvage"
        and e["commit_count"] == 0
        and e["has_commits"] is False
        for e in entries
    )
    assert not any(e["action"] == "wip_salvage_commit" for e in entries)


def test_run_worker_emits_turns_audit_and_reraises_on_unexpected_error(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(*_a: object, **_k: object) -> AgentStreamingResult:
        raise RuntimeError("worker blew up")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    with pytest.raises(RuntimeError, match="worker blew up"):
        seed_and_run(TEAM, workspace, None, lambda _e: None)

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "node_turns_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e.get("role") == "implementer" and "turns" not in e and "hit_ceiling" not in e
        for e in entries
    )


def test_run_salvages_uncommitted_diff_left_by_worker(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_streaming(
        system_prompt: str,
        user_text: str,
        *,
        proc_config: ProcessConfig | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        cwd = proc_config.cwd if proc_config is not None else None
        if cwd is not None:
            (cwd / "feature.py").write_text("print('done but never committed')\n")
        return AgentStreamingResult(result_text="## Summary\nDone.")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake_streaming)

    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert any(
        "chore: salvage uncommitted implementation output" in d.statement
        for d in handover.decisions
    )
    assert "alan-salvage identity" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "wip_salvage_commit" and e["ok"] is True for e in entries
    )


def test_run_no_salvage_needed_when_tree_clean(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)

    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)

    assert handover.handover_type == "escalation"
    assert "produced no commits" in handover.full_synthesis
    assert "alan-salvage identity" not in handover.full_synthesis
    assert not (tmp_path / "logs" / "impl_audit.jsonl").exists() or not any(
        json.loads(line)["action"] == "wip_salvage_commit"
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    )


def _prior_verification_handover(
    workspace: Workspace, pr_number: int | None = 99
) -> Handover:
    return Handover(
        handover_id="handover_verif_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="v1",
                statement="critical: fix the bug",
                rationale="diff review found NPE",
                confidence=0.9,
                reversibility="high",
            )
        ],
        full_synthesis="VERDICT: REQUEST_CHANGES\nfix the NPE",
        pr_number=pr_number,
    )


def _add_branch_at_main(repo: Path, branch: str) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", branch],
        cwd=str(repo), check=True, capture_output=True,
    )


def test_iteration_resolves_existing_branch_via_second_engine_forge_target(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
    from alan.workspace import EngineSnapshot

    ws = create(
        "impl-task-2", "do the other thing",
        root=tmp_path / "ws", engine_key="acme-corp/widget-app",
    )
    snapshot = EngineSnapshot(
        posture=Posture.autonomous,
        transition_graph={},
        config=EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
                authoritative_checks=frozenset({"Widget CI"}),
            ),
        ),
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)

    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "acme-widget-token")
    _add_branch_at_main(repo, "teams/impl-task-2-existing")
    _patch_streaming(
        monkeypatch, repo, write_files={"fix.py": "patched\n"}, commit_subject="fix: a",
    )

    calls: list[list[str]] = []
    push_envs: list[dict[str, str] | None] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="teams/impl-task-2-existing\n", stderr=""
            )
        if cmd[0] == "git" and "push" in cmd:
            push_envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(ws, pr_number=99)
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)

    assert handover.pr_number == 99
    view_calls = [c for c in calls if c[:3] == ["gh", "pr", "view"]]
    assert view_calls
    assert "--repo" in view_calls[0]
    assert view_calls[0][view_calls[0].index("--repo") + 1] == "acme-corp/widget-app"

    assert push_envs, "push_branch must have been called"
    pushed_env = push_envs[0]
    assert pushed_env is not None
    assert pushed_env[ASKPASS_TOKEN_ENV] == "acme-widget-token"


def test_resolve_existing_branch_returns_none_when_pr_number_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a, **_k):
        raise AssertionError("gh should not be called when pr_number is None")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert _pr_branch.resolve_existing_branch(
        None, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None


def test_resolve_existing_branch_returns_trimmed_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def fake_run(cmd, **kwargs):
        del kwargs
        assert cmd[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(cmd, 0, stdout="teams/foo-branch\n", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    branch = _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert branch == "teams/foo-branch"
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_view_ok" and e["branch"] == "teams/foo-branch" for e in entries)


def test_resolve_existing_branch_returns_none_on_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def empty(cmd, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", empty)
    assert _pr_branch.resolve_existing_branch(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_view_empty" for e in entries)


def test_iteration_on_existing_pr_pushes_same_branch_skips_pr_create(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _add_branch_at_main(repo, "teams/impl-task-existing")
    _patch_streaming(
        monkeypatch,
        repo,
        write_files={"fix.py": "patched\n"},
        commit_subject="fix: address review",
    )

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="teams/impl-task-existing\n", stderr=""
            )
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(workspace, pr_number=99)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 99
    assert any(c[0] == "git" and "push" in c for c in calls)
    assert all(c[:3] != ["gh", "pr", "create"] for c in calls)


def test_iteration_falls_back_to_fresh_pr_when_gh_view_fails(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch, repo, write_files={"a.py": "x\n"}, commit_subject="feat: a",
    )

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="gh: Not Found (HTTP 404)"
            )
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:3] == ["gh", "pr", "create"]:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout="https://github.com/owner/repo/pull/200\n",
                stderr="",
            )
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(workspace, pr_number=99)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 200
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_view_not_found" for e in entries)
    assert any(e["action"] == "pr_create_ok" for e in entries)


def test_pr_body_survives_well_over_old_budget_verbatim(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    sentinel = "SENTINEL-CAPABILITY-MAP-MARKER"
    result_text = "## Summary\n" + ("a" * 3000) + sentinel + ("b" * 200)
    _patch_streaming(
        monkeypatch, repo, write_files={"a.py": "x\n"}, commit_subject="feat: a",
        result_text=result_text,
    )

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "create"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/owner/repo/pull/321\n", stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number == 321

    create_cmd = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
    body = create_cmd[create_cmd.index("--body") + 1]
    assert sentinel in body
    assert len(body) > 2500
    assert "truncated" not in body
    assert body == _pr_body.build_pr_body(result_text)


def test_iteration_with_no_commits_keeps_existing_pr_number(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _add_branch_at_main(repo, "teams/impl-task-existing")
    _patch_streaming(monkeypatch, repo)

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="teams/impl-task-existing\n", stderr=""
            )
        raise AssertionError(f"git push / gh pr create should not run: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(workspace, pr_number=99)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 99


def test_iteration_dry_run_reports_existing_pr_in_audit(
    workspace, repo, monkeypatch, tmp_path
):

    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _add_branch_at_main(repo, "teams/impl-task-existing")
    _patch_streaming(
        monkeypatch, repo, write_files={"fix.py": "p\n"}, commit_subject="fix: a",
    )

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="teams/impl-task-existing\n", stderr=""
            )
        raise AssertionError(f"no subprocess in dry-run: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(workspace, pr_number=99)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 99
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "dry_run" and e["existing_pr"] == 99 for e in entries
    )


def test_iteration_push_failure_escalates_to_human(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _add_branch_at_main(repo, "teams/impl-task-existing")
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="teams/impl-task-existing\n", stderr=""
            )
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rejected")
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _prior_verification_handover(workspace, pr_number=99)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []
    assert handover.pr_number == 99
    assert "push to PR #99 failed" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "push_failed" for e in entries)


def test_drift_gate_check_returns_false_when_git_fails(tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "alan.teams.implementation._base_ref.resolve", lambda *a, **kw: None
        )
        assert _drift_gate_check(tmp_path) is False


def test_drift_gate_check_returns_false_when_no_load_bearing_change(tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "alan.teams.implementation._base_ref.resolve",
            lambda *a, **kw: "origin/main",
        )
        mp.setattr(
            "alan.teams.implementation._drift_gate_changed_paths",
            lambda *a, **kw: ["alan/foo.py", "README.md"],
        )
        assert _drift_gate_check(tmp_path) is False


def test_drift_gate_check_returns_true_when_load_bearing_changed_without_doc(tmp_path):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "alan.teams.implementation._base_ref.resolve",
            lambda *a, **kw: "origin/main",
        )
        mp.setattr(
            "alan.teams.implementation._drift_gate_changed_paths",
            lambda *a, **kw: ["alan/server.py"],
        )
        assert _drift_gate_check(tmp_path) is True


def test_backstop_required_false_when_no_commits(tmp_path):
    from alan.worktree import WorktreeHandle
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="test-branch")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("alan.teams.implementation._drift_gate_check", lambda _p: True)
        assert _backstop_required(handle, []) is False


def test_backstop_required_false_when_gate_passes(tmp_path):
    from alan.worktree import WorktreeHandle
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="test-branch")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("alan.teams.implementation._drift_gate_check", lambda _p: False)
        assert _backstop_required(handle, ["commit-sha"]) is False


def test_backstop_required_true_when_commits_and_gate_violated(tmp_path):
    from alan.worktree import WorktreeHandle
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="test-branch")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("alan.teams.implementation._drift_gate_check", lambda _p: True)
        assert _backstop_required(handle, ["commit-sha"]) is True


def test_run_drift_backstop_resume_returns_none_falls_through(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"alan/server.py": "x\n"})
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_required", lambda _h, _c: True
    )
    prior = _prior_plan_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type != "escalation"
    log_path = tmp_path / "logs" / "impl_audit.jsonl"
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert not any(e["action"] == "drift_backstop_done" for e in entries)


def test_run_drift_backstop_resume_succeeds_gate_clears(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"alan/server.py": "x\n"})
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_required", lambda _h, _c: True
    )
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_resume_attempt",
        lambda _g, _h, _c=None: "## Resumed",
    )
    monkeypatch.setattr(
        "alan.teams.implementation._drift_gate_check", lambda _p: False
    )
    prior = _prior_plan_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type != "escalation"
    log_path = tmp_path / "logs" / "impl_audit.jsonl"
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert any(e["action"] == "drift_backstop_done" for e in entries)


def test_run_drift_backstop_gate_still_violated_falls_through(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo, write_files={"alan/server.py": "x\n"})
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_required", lambda _h, _c: True
    )
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_resume_attempt",
        lambda _g, _h, _c=None: "## Resumed",
    )
    monkeypatch.setattr(
        "alan.teams.implementation._drift_gate_check", lambda _p: True
    )
    prior = _prior_plan_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type != "escalation"
    log_path = tmp_path / "logs" / "impl_audit.jsonl"
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert not any(e["action"] == "drift_backstop_done" for e in entries)


def test_run_drift_backstop_noop_when_gate_passes(
    workspace, repo, monkeypatch, tmp_path
):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(
        monkeypatch, repo, write_files={"alan/server.py": "x\n"},
        commit_subject="feat: server change with doc",
    )
    monkeypatch.setattr(
        "alan.teams.implementation._backstop_required", lambda _h, _c: False
    )
    prior = _prior_plan_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type != "escalation"


def test_backstop_resume_attempt_returns_none_when_node_not_found(
    tmp_path: Path,
) -> None:
    from alan.teams.implementation import _backstop_resume_attempt
    from alan.workflow import WorkflowGraph
    from alan.worktree import WorktreeHandle
    graph = WorkflowGraph(task_id="t1", inv_id="i1", team="implementation")
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="b")
    assert _backstop_resume_attempt(graph, handle) is None


def test_backstop_resume_attempt_returns_none_when_no_session_id(
    tmp_path: Path,
) -> None:
    from alan.teams.implementation import _backstop_resume_attempt
    from alan.workflow import WorkflowGraph
    from alan.worktree import WorktreeHandle
    graph = WorkflowGraph(task_id="t1", inv_id="i1", team="implementation")
    graph.start_node("implementer", role="implementer", model="sonnet")
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="b")
    assert _backstop_resume_attempt(graph, handle) is None


def test_backstop_resume_attempt_returns_result_text_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.claude_cli import AgentStreamingResult
    from alan.teams import _base as impl_base
    from alan.teams.implementation import _backstop_resume_attempt
    from alan.workflow import WorkflowGraph
    from alan.worktree import WorktreeHandle
    graph = WorkflowGraph(task_id="t1", inv_id="i1", team="implementation")
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sess-abc")
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="b")
    captured: list[str | None] = []

    def fake_stream(
        _sys: str,
        _usr: str,
        *,
        resume_session_id: str | None = None,
        **_kw: object,
    ) -> AgentStreamingResult:
        captured.append(resume_session_id)
        return AgentStreamingResult(result_text="resumed text")

    monkeypatch.setattr(impl_base, "run_agent_streaming", fake_stream)
    result = _backstop_resume_attempt(graph, handle)
    assert result == "resumed text"
    assert captured == ["sess-abc"]


def test_backstop_resume_attempt_carries_fleet_ceilings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.claude_cli import AgentStreamingResult, ProcessConfig
    from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS
    from alan.teams import _base as impl_base
    from alan.teams.implementation import _backstop_resume_attempt
    from alan.workflow import WorkflowGraph
    from alan.worktree import WorktreeHandle
    graph = WorkflowGraph(task_id="t1", inv_id="i1", team="implementation")
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sess-abc")
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="b")
    captured: dict[str, object] = {}

    def fake_stream(
        _sys: str,
        _usr: str,
        *,
        proc_config: ProcessConfig | None = None,
        fleet: bool = False,
        **_kw: object,
    ) -> AgentStreamingResult:
        captured["proc_config"] = proc_config
        captured["fleet"] = fleet
        return AgentStreamingResult(result_text="resumed text")

    monkeypatch.setattr(impl_base, "run_agent_streaming", fake_stream)
    _backstop_resume_attempt(graph, handle)
    assert captured["fleet"] is True
    proc_config = cast(ProcessConfig, captured["proc_config"])
    assert proc_config.timeout_seconds == FLEET_IDLE_TIMEOUT_SECONDS


def test_backstop_resume_attempt_returns_none_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _base as impl_base
    from alan.teams.implementation import _backstop_resume_attempt
    from alan.workflow import WorkflowGraph
    from alan.worktree import WorktreeHandle
    graph = WorkflowGraph(task_id="t1", inv_id="i1", team="implementation")
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sess-abc")
    handle = WorktreeHandle(path=tmp_path, base_ref="origin/main", branch="b")

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(impl_base, "run_agent_streaming", boom)
    assert _backstop_resume_attempt(graph, handle) is None


def _append_subtask(
    workspace: Workspace,
    *,
    instructions: str | None = "implement the frontend notice in web/foo.ts",
    dod: list[str] | None = None,
    title: str | None = "Frontend notice",
    tid: str = "sub-1",
) -> None:
    workspace.meta.tasks.append(
        Task(
            id=tid,
            status=TaskStatus.RUNNING,
            title=title,
            instructions=instructions,
            dod_clauses=[TaskDoDClause(text=t) for t in (dod or [])],
        )
    )


def test_run_user_text_includes_your_task_before_prior_plan(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    _append_subtask(workspace, dod=["notice renders an error"])
    prior = _prior_plan_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None, task_id="sub-1")
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "## Your task: Frontend notice" in user_text
    assert "implement the frontend notice in web/foo.ts" in user_text
    assert "notice renders an error" in user_text
    assert user_text.index("## Your task") < user_text.index("## Prior plan")


def test_run_user_text_relabels_escalation_prior_as_unreliable(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    captured: list[dict[str, object]] = []
    _patch_streaming(monkeypatch, repo, capture=captured)
    _append_subtask(workspace)
    prior = _prior_plan_handover(workspace).model_copy(
        update={"handover_type": "escalation"}
    )
    seed_and_run(TEAM, workspace, prior, lambda _e: None, task_id="sub-1")
    user_text = captured[0]["user_text"]
    assert isinstance(user_text, str)
    assert "Previous FAILED attempt" in user_text
    assert "UNRELIABLE" in user_text
    assert "## Prior plan" not in user_text


def test_system_prompt_warns_sibling_code_is_not_proof() -> None:
    assert "SIBLING CODE IS NOT PROOF" in _SYSTEM_PROMPT

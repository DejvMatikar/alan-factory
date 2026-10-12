from __future__ import annotations

import contextlib
import json
import subprocess as subprocess_mod
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alan import base_ref as base_ref_mod
from alan import process_runner
from alan import worktree as worktree_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Decision, Handover, ScenarioOutcome, TestScenario
from alan.teams import _base as base_mod
from alan.teams import _pr_branch, _pr_post, _pr_ready
from alan.teams import _verification_merged_guard as _merged_guard
from alan.teams import verification as verif_mod
from alan.teams.verification import (
    ROLES,
    SCENARIOS_WAIVED_MARKER,
    TEAM,
    _effective_scenarios,
    _effective_task_id,
    _find_current_task,
    _scenario_mode,
    _verification_task_id_var,
)
from alan.workspace import ScenarioBinding, Task, TaskStatus, Workspace, create
from tests._helpers import seed_and_run
from tests.forge_stubs import gh_unreachable


@contextlib.contextmanager
def _with_task_id(task_id: str | None) -> Iterator[None]:
    token = _verification_task_id_var.set(task_id)
    try:
        yield
    finally:
        _verification_task_id_var.reset(token)


_DEFAULT_JUDGE_OUTPUT = (
    "## Verdict\n\n"
    "VERDICT: APPROVE\n"
    "DIFF_FINDINGS_CRITICAL: 0\n\n"
    "## Rationale\nLooks good."
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("verify-task", "do verification", root=tmp_path / "ws")


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_path = tmp_path / "grounded"
    (fake_path / ".git").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: "fake-branch")
    monkeypatch.setattr(base_ref_mod, "resolve", lambda *_a, **_kw: "HEAD")
    monkeypatch.setattr(_merged_guard, "pr_state_name", lambda *_a, **_kw: None)

    @contextlib.contextmanager
    def _fake_worktree(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_worktree)


def _impl_handover(workspace: Workspace, pr_number: int | None = 42) -> Handover:
    return Handover(
        handover_id="handover_impl_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation",
        from_invocation_id="inv_001",
        to_team_suggestions=["verification"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="impl-01",
                statement="abc1234 feat: a",
                rationale="see synthesis",
                confidence=0.8,
                reversibility="medium",
            )
        ],
        full_synthesis="implementation done",
        pr_number=pr_number,
    )


def _make_impl_handover_with_scenarios(workspace: Workspace) -> Handover:
    prior = _impl_handover(workspace)
    return prior.model_copy(
        update={
            "test_scenarios": [
                TestScenario(
                    id="s_pass",
                    description="passes",
                    given="cmd exists",
                    when="uv run pytest",
                    then="ok",
                ),
                TestScenario(
                    id="s_skipped",
                    description="skipped",
                    given="no command",
                    when="",
                    then="",
                ),
            ]
        }
    )


def _patch_streaming_capturing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    judge_output: str = _DEFAULT_JUDGE_OUTPUT,
    captured: list[tuple[str, str]] | None = None,
) -> None:
    counter = {"n": 0}

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if captured is not None:
            captured.append((system_prompt, user_text))
        text = judge_output if counter["n"] == len(ROLES) else f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def _capture_for_role(captured: list[tuple[str, str]], role: str) -> tuple[str, str]:
    return next(entry for entry in captured if entry[0].startswith(f"You are the {role}"))


def test_scenario_runner_grounds_pass_and_fail_in_prefix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _make_impl_handover_with_scenarios(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    _dr_sp, dr_user = _capture_for_role(captured, "diff_reviewer")
    _sr_sp, sr_user = _capture_for_role(captured, "scenario_runner")
    _jg_sp, judge_user = _capture_for_role(captured, "judge")

    assert "## Scenario execution results" in sr_user
    assert "## Scenario execution results" not in dr_user
    assert "## Scenario execution results" not in judge_user


def test_only_scenario_runner_role_gets_prefix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _make_impl_handover_with_scenarios(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    for role in ("diff_reviewer", "judge"):
        _sp, user = _capture_for_role(captured, role)
        assert "## Scenario execution results" not in user


def test_no_scenarios_no_prefix_injected(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    for _sp, user in captured:
        assert "## Scenario execution results" not in user
    assert handover.scenario_results == []


def _workspace_with_task(
    ws: Workspace, *, scenarios: list[TestScenario], scenarios_waived: bool
) -> Workspace:
    new_task = Task(
        id=ws.task_id,
        status=TaskStatus.RUNNING,
        scenarios=scenarios,
        scenarios_waived=scenarios_waived,
    )
    updated = [new_task if t.id == ws.task_id else t for t in ws.meta.tasks]
    if not any(t.id == ws.task_id for t in ws.meta.tasks):
        updated = [new_task]
    return ws.model_copy(update={"meta": ws.meta.model_copy(update={"tasks": updated})})


def test_find_current_task_returns_none_for_empty_tasks(
    workspace: Workspace,
) -> None:
    assert _find_current_task(workspace) is None


def test_find_current_task_returns_task_for_workspace_id(
    workspace: Workspace,
) -> None:
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=False)
    task = _find_current_task(ws)
    assert task is not None
    assert task.id == workspace.task_id


def test_find_current_task_skips_non_matching_tasks(
    workspace: Workspace,
) -> None:
    other = Task(id="other-task", status=TaskStatus.PENDING)
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=False)
    ws = ws.model_copy(
        update={"meta": ws.meta.model_copy(update={"tasks": [other, *ws.meta.tasks]})}
    )
    task = _find_current_task(ws)
    assert task is not None
    assert task.id == workspace.task_id


def test_scenario_mode_missing_when_no_task_no_prior(workspace: Workspace) -> None:
    assert _scenario_mode(workspace, None) == "MISSING"


def test_scenario_mode_missing_when_no_scenarios_and_no_prior(
    workspace: Workspace,
) -> None:
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=False)
    assert _scenario_mode(ws, None) == "MISSING"


def test_scenario_mode_waived_when_task_scenarios_waived(workspace: Workspace) -> None:
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=True)
    assert _scenario_mode(ws, None) == "WAIVED"


def test_scenario_mode_waived_when_prior_scenarios_waived(workspace: Workspace) -> None:
    prior = _impl_handover(workspace).model_copy(update={"scenarios_waived": True})
    assert _scenario_mode(workspace, prior) == "WAIVED"


def test_scenario_mode_waived_when_prior_dependency_bot_authorized(
    workspace: Workspace,
) -> None:
    prior = _impl_handover(workspace).model_copy(
        update={"dependency_bot_authorized": True}
    )
    assert _scenario_mode(workspace, prior) == "WAIVED"


def test_scenario_mode_run_when_task_has_scenarios(workspace: Workspace) -> None:
    s = TestScenario(id="s1", description="d", given="g", when="uv run pytest", then="ok")
    ws = _workspace_with_task(workspace, scenarios=[s], scenarios_waived=False)
    assert _scenario_mode(ws, None) == "RUN"


def test_scenario_mode_run_when_prior_has_scenarios(workspace: Workspace) -> None:
    prior = _make_impl_handover_with_scenarios(workspace)
    assert _scenario_mode(workspace, prior) == "RUN"


def test_effective_scenarios_returns_task_scenarios_first(workspace: Workspace) -> None:
    task_s = TestScenario(id="task-s", description="d", given="g", when="uv run pytest", then="ok")
    ws = _workspace_with_task(workspace, scenarios=[task_s], scenarios_waived=False)
    prior = _make_impl_handover_with_scenarios(workspace)
    result = _effective_scenarios(ws, prior)
    assert [s.id for s in result] == ["task-s"]


def test_effective_scenarios_falls_back_to_prior(workspace: Workspace) -> None:
    prior = _make_impl_handover_with_scenarios(workspace)
    result = _effective_scenarios(workspace, prior)
    assert {s.id for s in result} == {"s_pass", "s_skipped"}


def test_effective_scenarios_empty_without_task_or_prior(workspace: Workspace) -> None:
    assert _effective_scenarios(workspace, None) == []


def test_scenario_waived_marker_appears_in_user_text(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=True)
    prior = _impl_handover(workspace).model_copy(
        update={
            "scenario_results": [ScenarioOutcome(scenario_id="stale", status="PASS")]
        }
    )
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
    assert any(SCENARIOS_WAIVED_MARKER in user for _sp, user in captured)
    assert handover.scenario_results == []


def _workspace_with_root_and_subtask(
    ws: Workspace,
    *,
    subtask_id: str,
    root_scenarios: list[TestScenario],
    subtask_scenarios: list[TestScenario],
    root_scenarios_waived: bool = False,
    subtask_scenarios_waived: bool = False,
) -> Workspace:
    root_task = Task(
        id=ws.task_id, status=TaskStatus.RUNNING, scenarios=root_scenarios,
        scenarios_waived=root_scenarios_waived,
    )
    sub_task = Task(
        id=subtask_id, status=TaskStatus.RUNNING, scenarios=subtask_scenarios,
        scenarios_waived=subtask_scenarios_waived,
    )
    return ws.model_copy(
        update={"meta": ws.meta.model_copy(update={"tasks": [root_task, sub_task]})}
    )


def test_effective_task_id_falls_back_to_workspace_task_id_when_var_unset(
    workspace: Workspace,
) -> None:
    assert _effective_task_id(workspace) == workspace.task_id


def test_effective_task_id_prefers_context_var(workspace: Workspace) -> None:
    with _with_task_id("subtask-1"):
        assert _effective_task_id(workspace) == "subtask-1"


def test_effective_task_id_does_not_treat_empty_string_as_unset(
    workspace: Workspace,
) -> None:
    with _with_task_id(""):
        assert _effective_task_id(workspace) == ""


def test_find_current_task_uses_context_var_task_id_for_subtask(
    workspace: Workspace,
) -> None:
    ws = _workspace_with_root_and_subtask(
        workspace, subtask_id="subtask-1", root_scenarios=[], subtask_scenarios=[]
    )
    with _with_task_id("subtask-1"):
        found = _find_current_task(ws)
    assert found is not None
    assert found.id == "subtask-1"


def test_find_current_task_returns_none_when_effective_id_not_in_tasks(
    workspace: Workspace,
) -> None:
    ws = _workspace_with_task(workspace, scenarios=[], scenarios_waived=False)
    with _with_task_id("subtask-missing"):
        assert _find_current_task(ws) is None


def test_scenario_mode_missing_for_unregistered_subtask_even_when_root_has_scenarios(
    workspace: Workspace,
) -> None:
    root_scenario = TestScenario(
        id="root-s", description="d", given="g", when="uv run pytest", then="ok"
    )
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[root_scenario],
        subtask_scenarios=[],
    )
    with _with_task_id("subtask-missing"):
        assert _scenario_mode(ws, None) == "MISSING"


def test_scenarios_waived_reads_subtask_flag_not_root(workspace: Workspace) -> None:
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[],
        subtask_scenarios=[],
        root_scenarios_waived=False,
        subtask_scenarios_waived=True,
    )
    with _with_task_id("subtask-1"):
        task = _find_current_task(ws)
    assert verif_mod._scenarios_waived(task, None) is True  # noqa: SLF001


def test_scenario_mode_uses_subtask_scenarios_not_root(workspace: Workspace) -> None:
    sub_scenario = TestScenario(
        id="sub-only", description="sub", given="g", when="uv run pytest", then="ok"
    )
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[],
        subtask_scenarios=[sub_scenario],
    )
    with _with_task_id("subtask-1"):
        assert _scenario_mode(ws, None) == "RUN"


def test_effective_scenarios_returns_subtask_scenarios(workspace: Workspace) -> None:
    root_scenario = TestScenario(
        id="root-only", description="root", given="g", when="uv run pytest", then="ok"
    )
    sub_scenario = TestScenario(
        id="sub-only", description="sub", given="g", when="uv run pytest", then="ok"
    )
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[root_scenario],
        subtask_scenarios=[sub_scenario],
    )
    with _with_task_id("subtask-1"):
        result = _effective_scenarios(ws, None)
    assert [s.id for s in result] == ["sub-only"]


def test_run_scopes_scenario_lookup_to_subtask_not_workspace_root(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    root_scenario = TestScenario(
        id="root-only", description="root", given="g", when="uv run pytest", then="ok"
    )
    sub_scenario = TestScenario(
        id="sub-only", description="sub", given="g", when="uv run pytest", then="ok"
    )
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[root_scenario],
        subtask_scenarios=[sub_scenario],
    )
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _impl_handover(ws)
    seed_and_run(TEAM, ws, prior, lambda _e: None, task_id="subtask-1")

    _dr_sp, dr_user = captured[0]
    assert "sub-only" in dr_user
    assert "root-only" not in dr_user


def test_run_falls_back_to_workspace_task_id_when_no_task_id_param(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    root_scenario = TestScenario(
        id="root-only", description="root", given="g", when="uv run pytest", then="ok"
    )
    ws = _workspace_with_root_and_subtask(
        workspace,
        subtask_id="subtask-1",
        root_scenarios=[root_scenario],
        subtask_scenarios=[],
    )
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _impl_handover(ws)
    seed_and_run(TEAM, ws, prior, lambda _e: None, task_id=None)

    _dr_sp, dr_user = captured[0]
    assert "root-only" in dr_user


def test_fix_bound_scenario_that_does_not_resolve_surfaces_as_fail(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    scenario = TestScenario(
        id="s1", description="d", given="g", when="uv run pytest tests/test_old.py", then="ok"
    )
    ws = _workspace_with_task(workspace, scenarios=[scenario], scenarios_waived=False)
    ws = ws.model_copy(
        update={
            "meta": ws.meta.model_copy(
                update={
                    "scenario_bindings": {
                        "s1": ScenarioBinding(
                            when="", written_by="fix_rebind", reason="fix_rebind"
                        )
                    }
                }
            )
        }
    )
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _impl_handover(ws)
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)
    by_id = {r.scenario_id: r.status for r in handover.scenario_results}
    assert by_id["s1"] == "FAIL"


def test_run_already_succeeded_scenario_runner_yields_empty_scenario_results(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(verif_mod, "_scenario_runner_already_succeeded", lambda _ws, _inv: True)
    captured: list[tuple[str, str]] = []
    _patch_streaming_capturing(monkeypatch, captured=captured)
    prior = _make_impl_handover_with_scenarios(workspace).model_copy(
        update={
            "scenario_results": [ScenarioOutcome(scenario_id="s_pass", status="PASS")]
        }
    )
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.scenario_results == []

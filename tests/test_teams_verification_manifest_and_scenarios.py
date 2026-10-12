from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, TestScenario
from alan.teams import _base as base_mod
from alan.teams import _pr_branch
from alan.teams import _scenario_runner as scenario_runner_mod
from alan.teams import _verification_scenarios as _scen_mod
from alan.teams import verification as verif_mod
from alan.teams._scenario_runner import ScenarioResult
from alan.teams.verification import (
    ROLES,
    TEAM,
    VerificationTeam,
)
from alan.workspace import ScenarioBinding, Task, TaskStatus, Workspace, update_scenario_binding
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _scenario(suffix: str) -> TestScenario:
    return TestScenario(
        id=f"s_{suffix}",
        description=f"desc-{suffix}",
        given=f"given-{suffix}",
        when=f"when-{suffix}",
        then=f"then-{suffix}",
    )


def test_format_scenarios_block_renders_all_fields() -> None:
    block = _scen_mod._format_scenarios_block(
        [_scenario("a"), _scenario("b")], frozenset()
    )
    assert block.startswith("## TEST SCENARIOS")
    assert "1. s_a — desc-a" in block
    assert "- Given: given-a" in block
    assert "- When: when-a" in block
    assert "- Then: then-a" in block
    assert "2. s_b — desc-b" in block
    assert "waived" not in block


def test_format_scenarios_block_marks_waived_ids_non_blocking() -> None:
    block = _scen_mod._format_scenarios_block(
        [_scenario("a"), _scenario("b")], frozenset({"s_b"})
    )
    assert "1. s_a — desc-a\n" in block
    assert "2. s_b — desc-b — waived (operator override, non-blocking)" in block


def test_verification_user_text_injects_scenarios_when_present(
    workspace: Workspace,
) -> None:
    prior = _impl_handover(workspace)
    prior = prior.model_copy(update={"test_scenarios": [_scenario("a")]})
    base = TEAM._build_base_user_text(workspace, prior)
    assert "## TEST SCENARIOS" in base
    assert "s_a" in base


def test_verification_user_text_omits_scenarios_block_when_empty(
    workspace: Workspace,
) -> None:
    prior = _impl_handover(workspace)
    base = TEAM._build_base_user_text(workspace, prior)
    assert "## TEST SCENARIOS" not in base


def test_verification_user_text_omits_scenarios_block_when_no_prior(
    workspace: Workspace,
) -> None:
    base = TEAM._build_base_user_text(workspace, None)
    assert "## TEST SCENARIOS" not in base


def test_current_task_manifest_empty_when_no_matching_task(workspace: Workspace) -> None:
    assert verif_mod._current_task_manifest(workspace) == []


def test_current_task_manifest_returns_task_files(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["alan/x.py"])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)
    assert verif_mod._current_task_manifest(workspace) == ["alan/x.py"]


def test_out_of_manifest_files_empty_without_prior(workspace: Workspace) -> None:
    assert verif_mod._out_of_manifest_files(workspace, None) == []


def test_out_of_manifest_files_empty_without_pr_number(workspace: Workspace) -> None:
    prior = _impl_handover(workspace, pr_number=None)
    assert verif_mod._out_of_manifest_files(workspace, prior) == []


def test_out_of_manifest_files_empty_when_manifest_empty(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=[])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("fetch_diff_files must not be called for an empty manifest")

    monkeypatch.setattr(_pr_branch, "fetch_diff_files", boom)
    prior = _impl_handover(workspace)
    assert verif_mod._out_of_manifest_files(workspace, prior) == []


def test_out_of_manifest_files_empty_when_diff_fetch_fails(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["alan/x.py"])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda _pr, **_kw: None)
    prior = _impl_handover(workspace)
    assert verif_mod._out_of_manifest_files(workspace, prior) == []


def test_out_of_manifest_files_empty_when_fully_covered(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["alan/"])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)
    monkeypatch.setattr(
        _pr_branch, "fetch_diff_files", lambda _pr, **_kw: frozenset({"alan/x.py"})
    )
    prior = _impl_handover(workspace)
    assert verif_mod._out_of_manifest_files(workspace, prior) == []


def test_out_of_manifest_files_returns_files_outside_manifest(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["alan/x.py"])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda _pr, **_kw: frozenset({"alan/x.py", "alan/unrelated.py"}),
    )
    prior = _impl_handover(workspace)
    assert verif_mod._out_of_manifest_files(workspace, prior) == ["alan/unrelated.py"]


def test_current_task_manifest_uses_effective_task_id_from_context_var(
    workspace: Workspace,
) -> None:
    root_task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["root.py"])
    sub_task = Task(id="subtask-1", status=TaskStatus.RUNNING, files=["sub.py"])
    ws = workspace.model_copy(
        update={"meta": workspace.meta.model_copy(update={"tasks": [root_task, sub_task]})}
    )
    token = verif_mod._verification_task_id_var.set("subtask-1")  # noqa: SLF001
    try:
        manifest = verif_mod._current_task_manifest(ws)
    finally:
        verif_mod._verification_task_id_var.reset(token)  # noqa: SLF001
    assert manifest == ["sub.py"]


def test_build_base_user_text_appends_manifest_deviation_block(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verif_mod, "_out_of_manifest_files", lambda _ws, _p: ["alan/unrelated.py"]
    )
    prior = _impl_handover(workspace)
    base = TEAM._build_base_user_text(workspace, prior)
    assert "## Manifest deviation (advisory)" in base
    assert "alan/unrelated.py" in base


def test_build_base_user_text_omits_manifest_block_when_no_deviation(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verif_mod, "_out_of_manifest_files", lambda _ws, _p: [])
    prior = _impl_handover(workspace)
    base = TEAM._build_base_user_text(workspace, prior)
    assert "## Manifest deviation" not in base


def test_empty_manifest_produces_no_finding(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("fetch_diff_files must not be called for an empty manifest")

    monkeypatch.setattr(_pr_branch, "fetch_diff_files", boom)
    prior = _impl_handover(workspace)
    base = TEAM._build_base_user_text(workspace, prior)
    assert "## Manifest deviation" not in base


def test_out_of_manifest_yields_verification_finding_without_flipping_verdict(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    task = Task(id=workspace.task_id, status=TaskStatus.RUNNING, files=["alan/x.py"])
    monkeypatch.setattr(verif_mod, "_find_current_task", lambda _ws: task)
    monkeypatch.setattr(
        _pr_branch,
        "fetch_diff_files",
        lambda _pr, **_kw: frozenset({"alan/x.py", "alan/unrelated.py"}),
    )
    captured: list[str] = []
    counter = {"n": 0}

    def fake(
        system_prompt: str,  # noqa: ARG001
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
        captured.append(user_text)
        text = _DEFAULT_JUDGE_OUTPUT if counter["n"] == len(ROLES) else f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    judge_text = captured[-1]
    assert "## Manifest deviation (advisory)" in judge_text
    assert "alan/unrelated.py" in judge_text
    assert handover.to_team_suggestions == ["pr_merge"]


def test_scenario_runner_prompt_references_test_scenarios_block() -> None:
    from alan.teams.verification import _SCENARIO_RUNNER_PROMPT

    assert "## TEST SCENARIOS" in _SCENARIO_RUNNER_PROMPT
    assert "SCENARIOS_MISSING" in _SCENARIO_RUNNER_PROMPT


def test_scenario_runner_prompt_no_longer_claims_test_ref_absence() -> None:
    from alan.teams.verification import _SCENARIO_RUNNER_PROMPT

    assert "no bound test_ref" not in _SCENARIO_RUNNER_PROMPT
    assert (
        "command prefix is not in the scenario runner's allowlist"
        in _SCENARIO_RUNNER_PROMPT
    )


def _scenario_result(scenario_id: str, status: str, diagnostic: str = "") -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        status=status,  # type: ignore[arg-type]
        diagnostic=diagnostic,
        stdout_tail="",
        stderr_tail="",
        returncode=0,
        elapsed_s=0.01,
    )


def test_build_verdict_handover_threads_scenario_results_populated(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    results = [_scenario_result("s1", "PASS"), _scenario_result("s2", "FAIL", "exit code 1")]
    handover = TEAM._build_verdict_handover(  # noqa: SLF001
        workspace, None, _DEFAULT_JUDGE_OUTPUT, "inv_001", lambda _e: None,
        DEFAULT_FORGE_TARGET, results,
    )
    by_id = {r.scenario_id: r for r in handover.scenario_results}
    assert by_id["s1"].status == "PASS"
    assert by_id["s2"].status == "FAIL"
    assert by_id["s2"].diagnostic == "exit code 1"


def test_build_verdict_handover_threads_scenario_results_empty(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    handover = TEAM._build_verdict_handover(  # noqa: SLF001
        workspace, None, _DEFAULT_JUDGE_OUTPUT, "inv_001", lambda _e: None,
        DEFAULT_FORGE_TARGET, [],
    )
    assert handover.scenario_results == []


def test_run_scenario_results_land_under_matching_scenario_ids(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    canned = [
        _scenario_result("s_pass", "PASS"),
        _scenario_result("s_fail", "FAIL", "exit code 1"),
        _scenario_result("s_skip", "SKIPPED", "aggregate budget exhausted"),
        _scenario_result("s_doc", "DOCUMENTED", "prose when field: documentation-only scenario"),
    ]
    monkeypatch.setattr(
        VerificationTeam,
        "_compute_scenario_results",
        lambda self, _ws, _prior, _inv_id: canned,
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    by_id = {r.scenario_id: r for r in handover.scenario_results}
    assert by_id["s_pass"].status == "PASS"
    assert by_id["s_fail"].status == "FAIL"
    assert by_id["s_fail"].diagnostic == "exit code 1"
    assert by_id["s_skip"].status == "SKIPPED"
    assert by_id["s_doc"].status == "DOCUMENTED"


def test_run_scenario_results_survive_scenario_id_divergence_from_prior(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    canned = [_scenario_result("new-id", "PASS")]
    monkeypatch.setattr(
        VerificationTeam,
        "_compute_scenario_results",
        lambda self, _ws, _prior, _inv_id: canned,
    )
    prior = _impl_handover(workspace).model_copy(
        update={
            "test_scenarios": [
                TestScenario(
                    id="old-id", description="d", given="g", when="uv run pytest", then="ok"
                )
            ]
        }
    )
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [r.scenario_id for r in handover.scenario_results] == ["new-id"]
    assert handover.test_scenarios[0].id == "old-id"


def _scenario_with_id(sid: str) -> TestScenario:
    return TestScenario(
        id=sid, description="d", given="g", when="uv run pytest tests/test_x.py", then="t",
    )


def _fix_prior_with_accepted_rebind(workspace: Workspace, *, waived: bool = False) -> Handover:
    return Handover(
        handover_id="h_fix_1",
        handover_type="revisit_amend",
        task_id=workspace.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_fix_001",
        iteration=2,
        revisit_of="h_verif_1",
        timestamp_utc=datetime.now(UTC),
        full_synthesis="fixed",
        pr_number=42,
        test_scenarios=[_scenario_with_id("s1")],
        scenarios_waived=waived,
    )


def _save_prior_invocation_graph(workspace: Workspace, inv_id: str) -> None:
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="verification"
    )
    graph.start_node("scenario_runner", role="scenario_runner", model="sonnet")
    graph.complete_node("scenario_runner")
    workflow_mod.save_workflow(workspace, graph.doc)


class TestStrictReevaluationOnResumedWorkspace:
    def test_runs_and_is_strict_despite_prior_verification_invocation(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = update_scenario_binding(
            workspace, "s1",
            ScenarioBinding(
                when="uv run pytest tests/test_rebound.py",
                written_by="fix_rebind", reason="fix_rebind",
            ),
        )
        _save_prior_invocation_graph(ws, "inv_001")
        prior = _fix_prior_with_accepted_rebind(ws)
        captured: dict[str, object] = {}

        def fake_run_scenarios(
            scenarios: list[TestScenario], *, cwd: str, cfg: object, strict_ids: frozenset[str]
        ) -> list[ScenarioResult]:
            del cwd, cfg
            captured["strict_ids"] = strict_ids
            captured["cmd"] = scenarios[0].when
            return [_scenario_result("s1", "PASS")]

        monkeypatch.setattr(scenario_runner_mod, "run_scenarios", fake_run_scenarios)
        monkeypatch.setattr(verif_mod, "_changed_e2e_results", lambda *_a, **_kw: [])

        results = TEAM._compute_scenario_results(ws, prior, "inv_002")  # noqa: SLF001

        assert captured["strict_ids"] == frozenset({"s1"})
        assert captured["cmd"] == "uv run pytest tests/test_rebound.py"
        assert [r.scenario_id for r in results] == ["s1"]

    def test_already_succeeded_precondition_is_scoped_to_this_invocation(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = update_scenario_binding(
            workspace, "s1",
            ScenarioBinding(
                when="uv run pytest tests/test_rebound.py",
                written_by="fix_rebind", reason="fix_rebind",
            ),
        )
        _save_prior_invocation_graph(ws, "inv_001")
        prior = _fix_prior_with_accepted_rebind(ws)
        called = {"n": 0}

        def fake_run_scenarios(*_a: object, **_kw: object) -> list[ScenarioResult]:
            called["n"] += 1
            return [_scenario_result("s1", "PASS")]

        monkeypatch.setattr(scenario_runner_mod, "run_scenarios", fake_run_scenarios)
        monkeypatch.setattr(verif_mod, "_changed_e2e_results", lambda *_a, **_kw: [])

        TEAM._compute_scenario_results(ws, prior, "inv_002")  # noqa: SLF001

        assert called["n"] == 1

    def test_waived_precondition_still_short_circuits_on_a_resumed_workspace(
        self, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = update_scenario_binding(
            workspace, "s1",
            ScenarioBinding(
                when="uv run pytest tests/test_rebound.py",
                written_by="fix_rebind", reason="fix_rebind",
            ),
        )
        _save_prior_invocation_graph(ws, "inv_001")
        prior = _fix_prior_with_accepted_rebind(ws, waived=True)
        called = {"n": 0}

        def fake_run_scenarios(*_a: object, **_kw: object) -> list[ScenarioResult]:
            called["n"] += 1
            return []

        monkeypatch.setattr(scenario_runner_mod, "run_scenarios", fake_run_scenarios)
        monkeypatch.setattr(verif_mod, "_changed_e2e_results", lambda *_a, **_kw: [])

        results = TEAM._compute_scenario_results(ws, prior, "inv_002")  # noqa: SLF001

        assert results == []
        assert called["n"] == 0

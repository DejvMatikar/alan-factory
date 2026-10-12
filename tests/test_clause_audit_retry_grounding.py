from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import EngineConfig, Posture, RepoIdentity
from alan.teams import _base as base_mod
from alan.teams import _clause_audit as ca_mod
from alan.teams import _pr_branch, _pr_forge
from alan.teams import verification as verif_mod
from alan.teams._verification_artifacts import reviewer_count_var
from alan.workspace import EngineSnapshot, Requirement, Workspace, stamp_engine_snapshot
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.forge_stubs import gh_unreachable

_APPROVE = "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


@pytest.fixture(autouse=True)
def _reset_clause_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CLAUSE_AUDIT", raising=False)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _workspace(tmp_path: Path, *, requirements: list[Requirement] | None) -> Workspace:
    ws = support.make_workspace(tmp_path)
    meta = ws.meta.model_copy(update={"requirements": requirements})
    return ws.model_copy(update={"meta": meta})


def _stub_gh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())


def _capturing_fake(
    *,
    diff_reviewer_output: str,
    scenario_runner_output: str,
    first_auditor_output: str,
    retry_auditor_output: str,
    judge_output: str,
    captured: dict[str, tuple[Path | None, list[str] | None, str]],
) -> object:
    def fake(system_prompt: str, user_text: str, **kwargs: object) -> AgentStreamingResult:
        cwd = getattr(kwargs.get("proc_config"), "cwd", None)
        tools = kwargs.get("allowed_tools")
        tools_list = list(tools) if isinstance(tools, list) else None
        if system_prompt.startswith("You are the diff_reviewer"):
            captured["diff_reviewer"] = (cwd, tools_list, system_prompt)
            text = diff_reviewer_output
        elif system_prompt.startswith("You are the scenario_runner"):
            captured["scenario_runner"] = (cwd, tools_list, system_prompt)
            text = scenario_runner_output
        elif system_prompt.startswith("You are the clause_auditor"):
            if ca_mod._CLAUSE_AUDITOR_RETRY_NUDGE in user_text:  # noqa: SLF001
                captured["clause_auditor_retry"] = (cwd, tools_list, system_prompt)
                text = retry_auditor_output
            else:
                captured["clause_auditor"] = (cwd, tools_list, system_prompt)
                text = first_auditor_output
        elif system_prompt.startswith("You are the judge"):
            captured["judge"] = (cwd, tools_list, system_prompt)
            text = judge_output
        else:
            text = "DIFF_FINDINGS_CRITICAL: 0"
        stream_path = kwargs.get("stream_path")
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    return fake


def test_retry_on_default_engine_runs_in_first_auditors_checkout_with_its_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    req = Requirement(text="deliver the fenced file")
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_gh_failure(monkeypatch)
    captured: dict[str, tuple[Path | None, list[str] | None, str]] = {}
    fake = _capturing_fake(
        diff_reviewer_output="DIFF_FINDINGS_CRITICAL: 0",
        scenario_runner_output="no scenarios",
        first_auditor_output="not parseable at all",
        retry_auditor_output=f"CLAUSE {req.id}: PASS — ok\nCLAUSE_FAILURES: 0\n",
        judge_output=_APPROVE,
        captured=captured,
    )
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = support.impl_handover(ws)
    seed_and_run(verif_mod.TEAM, ws, prior, lambda _e: None)

    assert "clause_auditor" in captured
    assert "clause_auditor_retry" in captured
    expected_cwd = tmp_path / "grounded"
    assert captured["clause_auditor"][0] == expected_cwd
    assert captured["clause_auditor_retry"][0] == expected_cwd
    assert captured["clause_auditor_retry"][1] == captured["clause_auditor"][1]
    assert "uv run --no-active pytest" in captured["clause_auditor_retry"][2]
    assert "TEST OWNERSHIP (RETRY)" in captured["clause_auditor_retry"][2]
    assert "run each exactly once" not in captured["clause_auditor_retry"][2]
    assert "run each exactly once" in captured["clause_auditor"][2]
    assert not (audit_root / "grounding_audit.jsonl").exists()


def test_retry_on_secondary_engine_uses_that_engines_grant_not_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    req = Requirement(text="deliver the fenced file")
    ws = _workspace(tmp_path, requirements=[req])
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", "Bash(dotnet:*)"],
        posture=Posture.autonomous,
    )
    assert cfg.base_tools != list(base_mod.BASE_ALLOWED_TOOLS)
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    _stub_gh_failure(monkeypatch)

    checkout = tmp_path / "grounded-secondary"
    (checkout / ".git").mkdir(parents=True)
    captured: dict[str, tuple[Path | None, list[str] | None, str]] = {}
    fake = _capturing_fake(
        diff_reviewer_output="DIFF_FINDINGS_CRITICAL: 0",
        scenario_runner_output="no scenarios",
        first_auditor_output="not parseable at all",
        retry_auditor_output=f"CLAUSE {req.id}: PASS — ok\nCLAUSE_FAILURES: 0\n",
        judge_output=_APPROVE,
        captured=captured,
    )
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)

    specs = verif_mod.TEAM.plan_workflow(ws, None)
    inv_id = workflow_mod.seed_pending_workflow(ws, team=verif_mod.TEAM.name, tasks=specs)
    grounded_token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    count_token = reviewer_count_var.set(None)
    ca_token = ca_mod._clause_audit_var.set(  # noqa: SLF001
        ca_mod.ClauseAuditContext(
            requirements=(req,), diff_files=frozenset(), pr_state=None,
            pr_number=42, task_id=ws.task_id, mode="enforce",
            forge_target=_pr_forge.forge_target_for_ws(ws),
        )
    )
    try:
        verif_mod.TEAM._collect_outputs(  # noqa: SLF001
            ws, inv_id, "base text", lambda _e: None, None,
        )
    finally:
        ca_mod._clause_audit_var.reset(ca_token)  # noqa: SLF001
        reviewer_count_var.reset(count_token)
        verif_mod._grounded_cwd_var.reset(grounded_token)  # noqa: SLF001

    assert captured["clause_auditor"][0] == checkout
    assert captured["clause_auditor_retry"][0] == checkout
    assert (captured["clause_auditor"][1] or [])[:2] == ["Read", "Bash(dotnet:*)"]
    assert captured["clause_auditor_retry"][1] == captured["clause_auditor"][1]
    assert "uv run --no-active pytest" not in captured["clause_auditor_retry"][2]
    assert "npm --prefix alan/web" not in captured["clause_auditor_retry"][2]
    assert "declares no local quality gates" in captured["clause_auditor_retry"][2]
    assert "TEST OWNERSHIP (RETRY)" in captured["clause_auditor_retry"][2]


def test_retry_entry_points_take_overrides_as_required_keyword() -> None:
    entry_points = (
        ca_mod.absorb, ca_mod._parse_with_retry, ca_mod._run_clause_auditor_retry,  # noqa: SLF001
    )
    for fn in entry_points:
        param = inspect.signature(fn).parameters["overrides"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


def test_parseable_first_auditor_output_never_spawns_retry_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,  # noqa: ARG001
) -> None:
    req = Requirement(text="deliver the fenced file")
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_gh_failure(monkeypatch)
    spawned: list[str] = []

    def fake(system_prompt: str, user_text: str, **kwargs: object) -> AgentStreamingResult:  # noqa: ARG001
        text = "DIFF_FINDINGS_CRITICAL: 0"
        if system_prompt.startswith("You are the clause_auditor"):
            spawned.append("clause_auditor")
            text = f"CLAUSE {req.id}: PASS — ok\nCLAUSE_FAILURES: 0\n"
        elif system_prompt.startswith("You are the judge"):
            text = _APPROVE
        elif system_prompt.startswith("You are the scenario_runner"):
            text = "no scenarios"
        stream_path = kwargs.get("stream_path")
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = support.impl_handover(ws)
    handover = seed_and_run(verif_mod.TEAM, ws, prior, lambda _e: None)
    assert spawned == ["clause_auditor"]
    doc = workflow_mod.load_workflow(
        workflow_mod.workflow_path(ws, handover.from_invocation_id)
    )
    assert all(t.id != ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID for t in doc.tasks)  # noqa: SLF001

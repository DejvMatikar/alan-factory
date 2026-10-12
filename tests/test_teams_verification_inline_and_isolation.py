from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from alan import judge_eval, process_runner
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import RepoIdentity
from alan.handover import Handover, TestScenario
from alan.teams import _base as base_mod
from alan.teams import _pr_ready, _verifier_thread_resolution
from alan.teams import verification as verif_mod
from alan.teams.role_output_fence import role_fence
from alan.teams.verification import (
    ROLES,
    TEAM,
)
from alan.workspace import Workspace, create
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT
_INLINE_JUDGE_OUTPUT = support._INLINE_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover
_blocked_readiness = support.blocked_readiness


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
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _posted(forge: ForgeScript) -> list[dict[str, object]]:
    return [cast("dict[str, object]", args[1]) for args, _ in forge.calls("post_review")]


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


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


def test_scenario_runner_already_succeeded_returns_true_after_run(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    import subprocess as subprocess_mod

    from alan.teams.verification import _scenario_runner_already_succeeded

    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout, env=None: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    _patch_streaming(monkeypatch)
    prior = _make_impl_handover_with_scenarios(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert _scenario_runner_already_succeeded(workspace, "inv_001") is True


def test_scenario_runner_already_succeeded_returns_false_when_no_workflow(
    workspace: Workspace,
) -> None:
    from alan.teams.verification import _scenario_runner_already_succeeded

    assert _scenario_runner_already_succeeded(workspace, "inv_001") is False


def test_resume_skips_scenarios_when_node_cached(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    import subprocess as subprocess_mod


    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout, env=None: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    _patch_streaming(monkeypatch)
    prior = _make_impl_handover_with_scenarios(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    results = TEAM._compute_scenario_results(workspace, prior, "inv_001")
    assert results == []


def test_scenario_results_context_var_reset_after_run(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    import subprocess as subprocess_mod

    from alan.teams.verification import _scenario_results_var

    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout, env=None: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 0, stdout="ok", stderr=""
        ),
    )
    _patch_streaming(monkeypatch)
    prior = _make_impl_handover_with_scenarios(workspace)
    assert _scenario_results_var.get() is None
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert _scenario_results_var.get() is None


def test_parse_inline_findings_resolves_through_reexport() -> None:
    assert verif_mod.parse_inline_findings(_DEFAULT_JUDGE_OUTPUT) == []


def test_run_live_human_body_omits_yaml(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)
    payloads: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if "--input" in cmd:
            idx = cmd.index("--input")
            payloads.append(json.loads(Path(cmd[idx + 1]).read_text()))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    body = payloads[0]["body"]
    assert isinstance(body, str)
    assert "INLINE_FINDINGS_YAML" not in body
    assert "```" not in body


_CONFIDENCE_FOOTER = (
    "---\n"
    "AGENT_STATUS: complete\n"
    "CONFIDENCE: 0.9\n"
    "COMPLETENESS: 1.0\n"
    "GAPS: none\n"
    "---\n"
)


def test_run_live_human_body_omits_confidence_footer(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge_output = _DEFAULT_JUDGE_OUTPUT + "\n" + _CONFIDENCE_FOOTER
    _patch_streaming(monkeypatch, judge_output=judge_output)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    body = _posted(forge)[0]["body"]
    assert isinstance(body, str)
    assert "AGENT_STATUS" not in body
    assert "CONFIDENCE" not in body
    assert "VERDICT: APPROVE" in body


def test_run_live_inline_posts_review_with_comments(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)

    captured: list[list[str]] = []
    payloads: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        if "--input" in cmd:
            idx = cmd.index("--input")
            payloads.append(json.loads(Path(cmd[idx + 1]).read_text()))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert any(
        cmd[:4] == ["gh", "api", "-X", "POST"]
        and "/repos/DejvMatikar/alan/pulls/42/reviews" in cmd
        for cmd in captured
    )
    assert not any(cmd[:3] == ["gh", "pr", "review"] for cmd in captured)
    assert payloads[0]["event"] == "REQUEST_CHANGES"
    comments = payloads[0]["comments"]
    assert isinstance(comments, list)
    assert len(comments) == 2
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_ok_inline" and e["inline_count"] == 2 for e in entries
    )


def test_external_gate_inline_block_honors_the_actionable_request_changes_event(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n"
        "## INLINE_FINDINGS_YAML\n```yaml\n"
        "- path: alan/foo.py\n  line: 10\n  body: |\n    stray finding\n"
        "```\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _blocked_readiness()
    )
    captured: list[list[str]] = []
    payloads: list[dict[str, object]] = []

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        if "--input" in cmd:
            idx = cmd.index("--input")
            payloads.append(json.loads(Path(cmd[idx + 1]).read_text()))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert not any(cmd[:3] == ["gh", "pr", "review"] for cmd in captured)
    assert payloads and payloads[0]["event"] == "REQUEST_CHANGES"
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_ok_inline" and e["event"] == "REQUEST_CHANGES" for e in entries
    )


def test_run_live_inline_no_findings_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    posted = _posted(forge)
    assert len(posted) == 1
    assert posted[0]["event"] == "APPROVE"
    assert posted[0]["comments"] == []
    assert "VERDICT: APPROVE" in str(posted[0]["body"])
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_ok" for e in entries)


def test_run_live_inline_post_failure_audited(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_failed_inline" and e["inline_count"] == 2
        for e in entries
    )


def test_run_live_approve_with_stale_yaml_uses_legacy_path(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    approve_with_stale_yaml = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n"
        "## INLINE_FINDINGS_YAML\n```yaml\n"
        "- path: alan/foo.py\n  line: 10\n  body: stale finding\n"
        "```\n"
    )
    _patch_streaming(monkeypatch, judge_output=approve_with_stale_yaml)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    posted = _posted(forge)
    assert len(posted) == 1
    assert posted[0]["event"] == "APPROVE"
    assert posted[0]["comments"] == []


@pytest.mark.parametrize(("token", "requested"), [("ghp_xxx", True), (None, False)])
def test_verifier_identity_is_requested_and_audited_from_credential_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
    forge: ForgeScript, token: str | None, requested: bool,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    if token is None:
        monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", token)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _verifier_thread_resolution, "resolve_after_approval", lambda *_a, **_kw: None
    )
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [kwargs["as_verifier"] for _, kwargs in forge.calls("post_review")] == [requested]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    review_ok = [e for e in entries if e["action"] == "review_ok"]
    assert [e["gh_user_override"] for e in review_ok] == [requested]


def test_verification_role_terse_judge_false() -> None:
    assert TEAM._role_terse("judge") is False  # noqa: SLF001


def test_verification_role_terse_diff_reviewer_false() -> None:
    assert TEAM._role_terse("diff_reviewer") is False  # noqa: SLF001


def test_verification_role_terse_scenario_runner_true() -> None:
    assert TEAM._role_terse("scenario_runner") is True  # noqa: SLF001


def test_diff_reviewer_prompt_excludes_terseness(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    from alan.teams._base import TERSENESS_BLOCK

    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured_prompts: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        stream_path: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        captured_prompts.append(system_prompt)
        n = len(captured_prompts)
        text = _DEFAULT_JUDGE_OUTPUT if n == len(ROLES) else "DIFF_FINDINGS_CRITICAL: 0"
        from pathlib import Path as _Path
        if isinstance(stream_path, _Path):
            import json as _json
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                _json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(TEAM, workspace, _impl_handover(workspace), lambda _e: None)
    def _role_for(prompt: str) -> str:
        return next(r.name for r in ROLES if prompt.startswith(f"You are the {r.name}"))

    role_prompts = {_role_for(prompt): prompt for prompt in captured_prompts}
    assert TERSENESS_BLOCK not in role_prompts["diff_reviewer"]
    assert TERSENESS_BLOCK not in role_prompts["judge"]
    assert TERSENESS_BLOCK in role_prompts["scenario_runner"]


def test_judge_user_text_includes_both_reviewer_and_runner_output_under_concurrency(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured_user_texts: dict[str, str] = {}

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",  # noqa: ARG001
        stream_path: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        role = next(r.name for r in ROLES if system_prompt.startswith(f"You are the {r.name}"))
        captured_user_texts[role] = user_text
        text = _DEFAULT_JUDGE_OUTPUT if role == "judge" else f"{role}-output"
        from pathlib import Path as _Path
        if isinstance(stream_path, _Path):
            import json as _json
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                _json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(TEAM, workspace, _impl_handover(workspace), lambda _e: None)

    judge_text = captured_user_texts["judge"]
    nonce = re.search(r"nonce=([0-9a-f]{16})", judge_text)
    assert nonce is not None
    assert judge_eval.JUDGE_COVERAGE in judge_text
    assert "### diff_reviewer\n\n" + role_fence(
        "diff_reviewer", "diff_reviewer-output", nonce.group(1)
    ) in judge_text
    assert "### scenario_runner\n\n" + role_fence(
        "scenario_runner", "scenario_runner-output", nonce.group(1)
    ) in judge_text


def test_diff_reviewer_and_scenario_runner_both_see_grounded_cwd_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    audit_root: Path,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured_cwds: dict[str, Path | None] = {}

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        stream_path: object = None,
        proc_config: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        role = next(r.name for r in ROLES if system_prompt.startswith(f"You are the {r.name}"))
        captured_cwds[role] = getattr(proc_config, "cwd", None)
        text = _DEFAULT_JUDGE_OUTPUT if role == "judge" else "DIFF_FINDINGS_CRITICAL: 0"
        from pathlib import Path as _Path
        if isinstance(stream_path, _Path):
            import json as _json
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                _json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = _impl_handover(workspace, pr_number=42)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    grounded = tmp_path / "grounded"
    assert captured_cwds["diff_reviewer"] == grounded
    assert captured_cwds["scenario_runner"] == grounded


def test_diff_reviewer_prompt_does_not_mention_git_diff_main() -> None:
    from alan.teams.verification import _DIFF_REVIEWER_PROMPT  # noqa: PLC0415
    assert "git diff main" not in _DIFF_REVIEWER_PROMPT


def test_verification_team_role_overrides_returns_worker_overrides(
    workspace: Workspace,
) -> None:
    from alan.teams._base import WorkerOverrides  # noqa: PLC0415
    overrides = TEAM._role_overrides("diff_reviewer", workspace)  # noqa: SLF001
    assert isinstance(overrides, WorkerOverrides)
    assert overrides.cwd == workspace.path


def test_verification_team_role_overrides_uses_grounded_cwd_when_set(
    workspace: Workspace, tmp_path: Path
) -> None:
    from alan.teams._base import WorkerOverrides  # noqa: PLC0415
    from alan.teams.verification import _grounded_cwd_var  # noqa: PLC0415
    grounded = tmp_path / "grounded"
    token = _grounded_cwd_var.set(grounded)
    try:
        overrides = TEAM._role_overrides("diff_reviewer", workspace)  # noqa: SLF001
        assert isinstance(overrides, WorkerOverrides)
        assert overrides.cwd == grounded
    finally:
        _grounded_cwd_var.reset(token)


def test_verification_team_role_overrides_ignores_role_name(
    workspace: Workspace,
) -> None:
    from alan.teams._base import WorkerOverrides  # noqa: PLC0415
    for role_name in ("diff_reviewer", "scenario_runner", "judge"):
        overrides = TEAM._role_overrides(role_name, workspace)  # noqa: SLF001
        assert isinstance(overrides, WorkerOverrides)
        assert overrides.cwd == workspace.path


def test_verification_team_role_overrides_sets_default_engine_allowed_tools(
    workspace: Workspace,
) -> None:
    from alan.teams._base import BASE_ALLOWED_TOOLS  # noqa: PLC0415

    overrides = TEAM._role_overrides("diff_reviewer", workspace)  # noqa: SLF001
    assert overrides is not None
    assert overrides.allowed_tools is not None
    assert overrides.allowed_tools[: len(BASE_ALLOWED_TOOLS)] == list(BASE_ALLOWED_TOOLS)


def test_verification_team_role_overrides_sets_nonpython_engine_allowed_tools(
    tmp_path: Path,
) -> None:
    from alan import workspace as workspace_mod  # noqa: PLC0415
    from alan.engine_config import EngineConfig, Posture  # noqa: PLC0415
    from alan.workspace import EngineSnapshot  # noqa: PLC0415

    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", "Bash(dotnet:*)"],
        posture=Posture.autonomous,
    )
    ws = create("verif-nonpython", "d", root=tmp_path / "ws")
    ws = workspace_mod.stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    overrides = TEAM._role_overrides("diff_reviewer", ws)  # noqa: SLF001
    assert overrides is not None
    assert overrides.allowed_tools is not None
    assert overrides.allowed_tools[:2] == ["Read", "Bash(dotnet:*)"]


def test_verification_team_role_overrides_reflects_live_registry_update(
    tmp_path: Path,
) -> None:
    from alan import engine_config as engine_config_mod  # noqa: PLC0415
    from alan.engine_config import EngineConfig, Posture  # noqa: PLC0415

    repo = RepoIdentity(owner="acme", repo="live-widget")
    initial = EngineConfig(repo=repo, base_tools=["Read"], posture=Posture.autonomous)
    engine_config_mod.register(initial, validate_env=False)
    try:
        ws = create("verif-live", "d", root=tmp_path / "ws", engine_key=repo.slug)
        before = TEAM._role_overrides("diff_reviewer", ws)  # noqa: SLF001
        assert before is not None
        assert before.allowed_tools is not None
        assert before.allowed_tools[:1] == ["Read"]

        updated = initial.model_copy(update={"base_tools": ["Read", "Edit"]})
        engine_config_mod.update_and_persist(updated, validate_env=False)

        after = TEAM._role_overrides("diff_reviewer", ws)  # noqa: SLF001
        assert after is not None
        assert after.allowed_tools is not None
        assert after.allowed_tools[:2] == ["Read", "Edit"]
    finally:
        engine_config_mod.unregister_and_persist(repo)

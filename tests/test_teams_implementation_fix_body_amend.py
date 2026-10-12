from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import process_runner
from alan.claude_cli import AgentStreamingResult, ProcessConfig
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.handover import Handover
from alan.teams import _base as base_mod
from alan.teams import (
    _fix_non_actionable,
    _pr_body,
    _pr_push,
    _pr_sonar,
    _pr_threads,
)
from alan.teams import implementation_fix as fix_mod
from alan.teams._pr_push import PushOutcome, PushResult
from alan.teams.implementation_fix import TEAM
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, gh_unreachable, pr_read


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


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
        ["git", "commit", "-q", "-m", "initial"], cwd=str(repo), check=True, capture_output=True,
    )


def _add_branch_at_main(repo: Path, branch: str) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", branch], cwd=str(repo), check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("body-amend-task", "iterate on the open PR", root=tmp_path / "ws")


@pytest.fixture
def closing_workspace(tmp_path: Path) -> Workspace:
    return create(
        "body-amend-closing-task",
        "iterate on the open PR\n\nDelivers-Issues: 42",
        root=tmp_path / "ws-closing",
    )


def _verification_handover(
    workspace: Workspace, *, pr_number: int | None = 42,
    summary: str = "## Verdict\nVERDICT: REQUEST_CHANGES\n",
) -> Handover:
    return Handover(
        handover_id="handover_verif_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        pr_number=pr_number,
    )


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result_text: str,
    write_files: dict[str, str] | None = None,
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        proc_config: ProcessConfig | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model
        cwd = proc_config.cwd if proc_config is not None else None
        if cwd is not None and write_files is not None:
            for rel_path, content in write_files.items():
                target = cwd / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            subprocess.run(  # noqa: S603,S607
                ["git", "add", "-A"], cwd=str(cwd), check=True, capture_output=True
            )
            subprocess.run(  # noqa: S603,S607
                ["git", "commit", "-q", "-m", "fix: address review"],
                cwd=str(cwd), check=True, capture_output=True,
            )
        return AgentStreamingResult(result_text=result_text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def _patch_branch_resolution(monkeypatch: pytest.MonkeyPatch, *, branch: str | None) -> None:
    monkeypatch.setattr(fix_mod, "_resolve_existing_branch", lambda *_a, **_k: branch)


def _patch_push_fix_branch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_value: PushOutcome = PushOutcome.PUSHED,
) -> None:
    def fake(*_a: object, **_k: object) -> PushResult:
        return PushResult(outcome=return_value)

    monkeypatch.setattr(fix_mod, "push_fix_branch_with_reconcile", fake)


_PR_BODY_SYNTHESIS = (
    "## Summary\nOnly the PR body needed revising; no code change was required.\n\n"
    "```pr_body\n"
    "## Why\nThe original body omitted the migration rollback plan.\n"
    "```\n"
)


def test_render_system_prompt_includes_pr_body_block_instructions(tmp_path: Path) -> None:
    from alan.engine_config import _DEFAULT as engine_default

    prompt = fix_mod.render_system_prompt(engine_default, tmp_path)
    assert _pr_body.PR_BODY_BLOCK_INSTRUCTIONS in prompt


def test_maybe_amend_body_returns_none_without_authored_block(workspace: Workspace) -> None:
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, 42, "## Summary\nno block", None, ["abc fix"],
        PushResult(outcome=PushOutcome.PUSHED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is None


def test_maybe_amend_body_returns_none_without_prior_pr(workspace: Workspace) -> None:
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, None, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", [],
        PushResult(outcome=PushOutcome.PUSHED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is None


def test_maybe_amend_body_returns_none_when_commits_failed_to_push(
    workspace: Workspace,
) -> None:
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", ["abc fix"],
        PushResult(outcome=PushOutcome.PUSH_FAILED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is None


def test_maybe_amend_body_proceeds_with_no_commits_despite_push_failed_outcome(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_body, "amend_pr_body",
        lambda *_a, **_k: _pr_body.BodyAmendResult(ok=True, previous_body="old", new_body="new"),
    )
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", [],
        PushResult(outcome=PushOutcome.PUSH_FAILED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is not None
    assert result.ok is True


def test_maybe_amend_body_proceeds_with_reconciled_push(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_amend(
        pr_number: int, body: str, **_k: object,
    ) -> _pr_body.BodyAmendResult:
        captured.append(body)
        return _pr_body.BodyAmendResult(ok=True, previous_body=None, new_body=body)

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", ["abc fix"],
        PushResult(outcome=PushOutcome.RECONCILED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is not None
    assert captured == [
        "## Why\nThe original body omitted the migration rollback plan."
    ]


def test_maybe_amend_body_appends_fixes_line_for_declared_issue(
    closing_workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_amend(
        pr_number: int, body: str, **_k: object,
    ) -> _pr_body.BodyAmendResult:
        captured.append(body)
        return _pr_body.BodyAmendResult(ok=True, previous_body=None, new_body=body)

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        closing_workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", ["abc fix"],
        PushResult(outcome=PushOutcome.RECONCILED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is not None
    assert captured == [
        "## Why\nThe original body omitted the migration rollback plan.\n\nFixes #42"
    ]


def test_every_amend_of_a_declared_workspace_carries_fixes_line_on_its_own_repo(
    closing_workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        credential_env_name="ACME_WIDGET_GH_TOKEN",
    )
    captured: list[tuple[str, ForgeTarget]] = []

    def fake_amend(
        pr_number: int, body: str, *, forge_target: ForgeTarget, **_k: object,
    ) -> _pr_body.BodyAmendResult:
        captured.append((body, forge_target))
        return _pr_body.BodyAmendResult(ok=True, previous_body=None, new_body=body)

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)

    result_1 = fix_mod._maybe_amend_body(  # noqa: SLF001
        closing_workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", ["abc fix"],
        PushResult(outcome=PushOutcome.RECONCILED), forge_target=target,
    )
    result_2 = fix_mod._maybe_amend_body(  # noqa: SLF001
        closing_workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it again.", ["def fix"],
        PushResult(outcome=PushOutcome.PUSHED), forge_target=target,
    )

    assert result_1 is not None and result_1.ok is True
    assert result_2 is not None and result_2.ok is True
    assert len(captured) == 2
    for body, used_target in captured:
        assert body.endswith("Fixes #42")
        assert used_target is target


def test_maybe_amend_body_dry_run_skips_amend_call(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")

    def boom(*_a: object, **_k: object) -> _pr_body.BodyAmendResult:
        raise AssertionError("amend_pr_body must not be called in dry-run")

    monkeypatch.setattr(_pr_body, "amend_pr_body", boom)
    result = fix_mod._maybe_amend_body(  # noqa: SLF001
        workspace, 42, _PR_BODY_SYNTHESIS, "## Why\nExplains it.", [],
        PushResult(outcome=PushOutcome.PUSH_FAILED), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result is not None
    assert result.ok is True
    assert result.previous_body is None


def test_check_non_actionable_returns_none_when_authored_body_present(
    workspace: Workspace,
) -> None:
    synthesis = "## Summary\nno code change\n"
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", synthesis, [], "CI flake unrelated to this PR",
        authored_body="## Why\nExplains it.", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is None


def test_check_non_actionable_still_escalates_without_authored_body(
    workspace: Workspace,
) -> None:
    synthesis = "## Summary\nno code change\n"
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", synthesis, [], "CI flake unrelated to this PR",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == []


def test_maybe_fetch_current_body_prior_none_returns_none() -> None:
    assert fix_mod._maybe_fetch_current_body(None, forge_target=DEFAULT_FORGE_TARGET) is None  # noqa: SLF001


def test_maybe_fetch_current_body_no_pr_returns_none(workspace: Workspace) -> None:
    prior = _verification_handover(workspace, pr_number=None)
    assert fix_mod._maybe_fetch_current_body(prior, forge_target=DEFAULT_FORGE_TARGET) is None  # noqa: SLF001


def test_maybe_fetch_current_body_delegates_to_fetch_pr_body(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda _pr, **_k: "## Existing")
    prior = _verification_handover(workspace, pr_number=42)
    assert fix_mod._maybe_fetch_current_body(  # noqa: SLF001
        prior, forge_target=DEFAULT_FORGE_TARGET
    ) == "## Existing"


def test_current_pr_body_block_fences_body() -> None:
    block = fix_mod._current_pr_body_block("## Existing\nold content")  # noqa: SLF001
    assert "## Current live PR body" in block
    assert "## Existing\nold content" in block
    assert "a human may have edited it" in block
    assert base_mod.UNTRUSTED_CONTENT_NOTICE in block


def test_build_user_text_renders_current_body_block(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_threads, "list_unresolved_threads", lambda _pr, **_k: ())
    monkeypatch.setattr(_pr_sonar, "list_sonarcloud_annotations", lambda _pr, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda _pr, **_k: "## Existing body text")
    prior = _verification_handover(workspace)
    text = fix_mod._build_user_text(workspace, prior)  # noqa: SLF001
    assert "## Current live PR body" in text
    assert "## Existing body text" in text


def test_build_user_text_omits_current_body_block_when_fetch_fails(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_threads, "list_unresolved_threads", lambda _pr, **_k: ())
    monkeypatch.setattr(_pr_sonar, "list_sonarcloud_annotations", lambda _pr, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda _pr, **_k: None)
    prior = _verification_handover(workspace)
    text = fix_mod._build_user_text(workspace, prior)  # noqa: SLF001
    assert "## Current live PR body" not in text


def test_no_commits_body_only_round_forwards_and_amends(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch)
    amend_calls: list[tuple[int, str]] = []

    def fake_amend(pr_number: int, body: str, **_k: object) -> _pr_body.BodyAmendResult:
        amend_calls.append((pr_number, body))
        return _pr_body.BodyAmendResult(ok=True, previous_body="old", new_body=body)

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)
    _patch_streaming(monkeypatch, result_text=_PR_BODY_SYNTHESIS)

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert handover.pr_number == 42
    assert amend_calls == [(42, "## Why\nThe original body omitted the migration rollback plan.")]
    assert handover.decisions
    assert handover.decisions[0].id == "fix-body"


def test_failed_body_amend_escalates_instead_of_forwarding_as_success(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch)

    def fake_amend(pr_number: int, body: str, **_k: object) -> _pr_body.BodyAmendResult:
        return _pr_body.BodyAmendResult(
            ok=False, previous_body="old", new_body=body, error="404 not found",
        )

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)
    _patch_streaming(monkeypatch, result_text=_PR_BODY_SYNTHESIS)

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "escalation"
    assert "produced no commits" in handover.full_synthesis


def test_body_block_takes_precedence_over_non_actionable_feedback(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch)
    monkeypatch.setattr(
        _pr_body, "amend_pr_body",
        lambda pr, body, **_k: _pr_body.BodyAmendResult(ok=True, previous_body=None, new_body=body),
    )
    synthesis = (
        "## Summary\nNothing actionable in code, but the body needed a rewrite.\n\n"
        "```yaml non_actionable_feedback.yaml\n"
        "reason: CI was flaky and unrelated\n"
        "```\n\n"
        "```pr_body\n"
        "## Why\nRewritten body.\n"
        "```\n"
    )
    _patch_streaming(monkeypatch, result_text=synthesis)

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert not handover.open_questions


def test_commits_present_round_amends_body_after_successful_push(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=PushOutcome.PUSHED)
    amend_calls: list[int] = []

    def fake_amend(pr_number: int, body: str, **_k: object) -> _pr_body.BodyAmendResult:
        amend_calls.append(pr_number)
        return _pr_body.BodyAmendResult(ok=True, previous_body=None, new_body=body)

    monkeypatch.setattr(_pr_body, "amend_pr_body", fake_amend)
    synthesis = (
        "## Summary\nFixed the bug and revised the body.\n\n"
        "```pr_body\n## Why\nFixes the race condition.\n```\n"
    )
    _patch_streaming(monkeypatch, result_text=synthesis, write_files={"feature.py": "fixed\n"})

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "forward"
    assert amend_calls == [42]


def test_diverged_push_does_not_amend_body(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=PushOutcome.DIVERGED_ESCALATE)

    def boom(*_a: object, **_k: object) -> _pr_body.BodyAmendResult:
        raise AssertionError("amend_pr_body must not be called on diverged push")

    monkeypatch.setattr(_pr_body, "amend_pr_body", boom)
    monkeypatch.setattr(_pr_push, "try_resolve_remote_sha", lambda *_a, **_k: "deadbeef")
    synthesis = (
        "## Summary\nFixed the bug and revised the body.\n\n"
        "```pr_body\n## Why\nFixes the race condition.\n```\n"
    )
    _patch_streaming(monkeypatch, result_text=synthesis, write_files={"feature.py": "fixed\n"})

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "escalation"


def test_no_authored_body_no_scenarios_still_escalates_on_no_commits(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch)
    _patch_streaming(monkeypatch, result_text="## Summary\nNothing to report.")

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "escalation"
    assert "produced no commits" in handover.full_synthesis


def test_dry_run_body_only_round_routes_forward_without_gh_call(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")

    def boom(*_a: object, **_k: object) -> _pr_body.BodyAmendResult:
        raise AssertionError("dry-run must not call the real amend_pr_body")

    monkeypatch.setattr(_pr_body, "amend_pr_body", boom)
    _patch_streaming(monkeypatch, result_text=_PR_BODY_SYNTHESIS)

    from tests._helpers import seed_and_run

    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]


def test_amend_pr_body_audit_row_written_via_full_round(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    prior = _verification_handover(workspace)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(repo))
    _add_branch_at_main(repo, "teams/body-amend-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/body-amend-task-existing")
    _patch_push_fix_branch(monkeypatch)

    def fake_run_subprocess(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(cmd, 0, stdout='{"body": "old"}')
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    always(_active_fake_forge, "read_pr", pr_read({"body": "old"}))
    always(_active_fake_forge, "update_pr_body", api_result())
    _patch_streaming(monkeypatch, result_text=_PR_BODY_SYNTHESIS)

    from tests._helpers import seed_and_run

    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    amend_row = next(e for e in entries if e["action"] == "pr_body_amend_ok")
    assert amend_row["pr_number"] == 42
    assert amend_row["previous_body"] == "old"
    forward_row = next(e for e in entries if e["action"] == "pr_body_amend_forward")
    assert forward_row["amend_ok"] is True

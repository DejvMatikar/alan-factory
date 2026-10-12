from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.claude_cli import AgentStreamingResult, ProcessConfig
from alan.handover import Handover
from alan.teams import _base as base_mod
from alan.teams import _forge_errors, _pr_closing, _pr_forge, _pr_handover
from alan.teams import implementation as implementation_mod
from alan.teams.implementation import TEAM
from alan.workspace import Workspace, create
from alan.worktree import WorktreeHandle
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge

_RATE_LIMIT_STDERR = "GraphQL: API rate limit already exceeded for user ID 24228929.\n"
_FAKE_TRANSPORT_TOKEN = "test-transport-token"  # noqa: S105  # nosec B105


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", _FAKE_TRANSPORT_TOKEN)


@pytest.fixture(autouse=True)
def _ambient_worktree_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_WORKTREE_REPO_ROOT", raising=False)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("impl-task", "do the thing", root=tmp_path / "ws")


def _prior_with_pr(workspace: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id="handover_verif_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="VERDICT: REQUEST_CHANGES\nfix it",
        pr_number=pr_number,
    )


def test_run_propagates_rate_limit_before_entering_worktree(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def rate_limited(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["gh"], 1, stdout="", stderr=_RATE_LIMIT_STDERR)

    monkeypatch.setattr(process_runner, "run_subprocess", rate_limited)

    def boom(*_a: object, **_k: object) -> object:
        pytest.fail("must not enter worktree_iteration when resolution is rate limited")

    monkeypatch.setattr(_pr_handover, "worktree_iteration", boom)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        seed_and_run(TEAM, workspace, _prior_with_pr(workspace, 42), lambda _e: None)


def test_run_propagates_transient_network_failure(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def network_error(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["gh"], 1, stdout="", stderr="dial tcp: i/o timeout"
        )

    monkeypatch.setattr(process_runner, "run_subprocess", network_error)

    def boom(*_a: object, **_k: object) -> object:
        pytest.fail("must not enter worktree_iteration on a network failure")

    monkeypatch.setattr(_pr_handover, "worktree_iteration", boom)
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, _prior_with_pr(workspace, 42), lambda _e: None)


def test_open_pr_rate_limit_raises_forge_rate_limited(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def rate_limited(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["gh"], 1, stdout="", stderr=_RATE_LIMIT_STDERR)

    monkeypatch.setattr(process_runner, "run_subprocess", rate_limited)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        implementation_mod._open_pr(
            WorktreeHandle(path=tmp_path, base_ref="HEAD", branch="teams/x"),
            workspace, "synth",
        )


def test_open_pr_already_exists_returns_none_with_audit(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def exists(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["gh"], 1, stdout="",
            stderr='a pull request for branch "teams/x" already exists',
        )

    monkeypatch.setattr(process_runner, "run_subprocess", exists)
    handle = WorktreeHandle(path=tmp_path, base_ref="HEAD", branch="teams/x")
    assert implementation_mod._open_pr(handle, workspace, "synth") is None
    audit = (tmp_path / "logs" / "impl_audit.jsonl").read_text()
    assert "pr_create_failed" in audit


def test_open_pr_runs_one_ambient_gh_pr_create_through_the_forge_port(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def credential_must_not_resolve(*_a: object, **_k: object) -> object:
        pytest.fail("the ambient PR creation must not resolve a credential")

    monkeypatch.setattr(secrets_store, "transport_credential", credential_must_not_resolve)
    monkeypatch.setattr(secrets_store, "verifier_gh_env", credential_must_not_resolve)
    recorded: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.append((list(cmd), dict(kwargs)))
        stdout = "https://github.com/o/r/pull/9\n" if cmd[0] == "gh" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handle = WorktreeHandle(path=tmp_path, base_ref="HEAD", branch="teams/x")
    assert implementation_mod._open_pr(handle, workspace, "synth") == 9
    gh_calls = [call for call in recorded if call[0][0] == "gh"]
    expected_body = _pr_closing.compose_pr_body(
        workspace, "synth", team=implementation_mod._TEAM_NAME, pr_number=None,
    )
    assert len(gh_calls) == 1
    argv, kwargs = gh_calls[0]
    assert argv == [
        "gh", "pr", "create",
        "--title", workspace.meta.title or workspace.task_id,
        "--body", expected_body,
        "--base", "main",
        "--head", "teams/x",
    ]
    assert kwargs == {"cwd": str(tmp_path), "timeout": _pr_forge.GH_TIMEOUT_S}
    port_calls = [c for c in _active_fake_forge.calls if c[0] == "create_pr_from_checkout"]
    assert len(port_calls) == 1
    assert port_calls[0][2]["forge_target"] == _pr_forge.forge_target_for_ws(workspace)


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


def _patch_streaming(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
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
        del system_prompt, user_text, model, on_event, max_turns
        del allowed_tools, permission_mode, resume_session_id
        cwd = proc_config.cwd if proc_config is not None else None
        if cwd is not None:
            (cwd / "a.py").write_text("x\n")
            subprocess.run(  # noqa: S603,S607
                ["git", "add", "-A"], cwd=str(cwd), check=True, capture_output=True
            )
            subprocess.run(  # noqa: S603,S607
                ["git", "commit", "-q", "-m", "feat(impl): add work"],
                cwd=str(cwd),
                check=True,
                capture_output=True,
            )
        return AgentStreamingResult(result_text="## Summary\nDone.")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    _ = repo


def _pushed_branches(calls: list[list[str]]) -> list[str]:
    return [c[-1] for c in calls if c[0] == "git" and "push" in c]


def test_pr_create_auth_failure_states_commits_are_safe(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)
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
                cmd, 1, stdout="", stderr="Bad credentials"
            )
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    assert handover.handover_type == "escalation"
    branch = _pushed_branches(calls)[0]
    assert branch in handover.full_synthesis
    assert "SAFE" in handover.full_synthesis
    assert "Do NOT re-implement" in handover.full_synthesis
    assert "committed work was lost" not in handover.full_synthesis


def test_pr_url_unparseable_states_commits_are_safe(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)
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
                cmd, 0, stdout="something else, no pull URL\n", stderr=""
            )
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.pr_number is None
    branch = _pushed_branches(calls)[0]
    assert branch in handover.full_synthesis
    assert "SAFE" in handover.full_synthesis
    assert "committed work was lost" not in handover.full_synthesis


def test_stale_prior_pr_with_unresolvable_branch_states_commits_safe_on_fresh_branch(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr="")
        if cmd[0] == "git" and "push" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="worker@example.com\n", stderr="")
        if cmd[:3] == ["gh", "pr", "create"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Bad credentials")
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    handover = seed_and_run(TEAM, workspace, _prior_with_pr(workspace, 42), lambda _e: None)
    assert handover.handover_type == "escalation"
    branch = _pushed_branches(calls)[0]
    assert branch in handover.full_synthesis
    assert "SAFE" in handover.full_synthesis
    assert "Do NOT re-implement" in handover.full_synthesis
    assert "push to PR #42 failed" not in handover.full_synthesis
    assert "were lost when the worktree was removed" not in handover.full_synthesis


def test_push_itself_failing_keeps_lost_wording(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _patch_streaming(monkeypatch, repo)

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
    assert "committed work was lost" in handover.full_synthesis
    assert "SAFE" not in handover.full_synthesis

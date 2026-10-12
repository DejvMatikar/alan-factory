from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from alan import process_runner
from alan import workspace as workspace_mod
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    Posture,
    RepoIdentity,
)
from alan.teams import (
    _copilot_resolution,
    _copilot_resolution_block,
    _pr_body,
    _pr_push,
    _pr_state_paths,
)
from alan.teams import _pr_sonar as pr_sonar_mod
from alan.teams import _pr_threads as pr_threads_mod
from alan.teams import implementation_fix as fix_mod
from alan.teams._copilot_resolution_block import CopilotResolutionRow
from alan.teams._pr_forge import governing_config_for_ws
from alan.teams.implementation_fix import TEAM
from alan.workspace import EngineSnapshot, Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, gh_unreachable
from tests.test_teams_implementation_fix import (
    _add_branch_at_main,
    _init_repo,
    _patch_branch_resolution,
    _patch_push_fix_branch,
    _patch_streaming,
    _verification_handover,
)

_WIDGET_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


@pytest.fixture(autouse=True)
def _ambient_worktree_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_WORKTREE_REPO_ROOT", raising=False)


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


def _widget_snapshot() -> EngineSnapshot:
    return EngineSnapshot(
        posture=Posture.propose,
        transition_graph={},
        config=EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
            ),
        ),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws = create(
        "fix-task-copilot", "iterate on the open PR",
        root=tmp_path / "ws", engine_key="acme-corp/widget-app",
    )
    return workspace_mod.stamp_engine_snapshot(ws, _widget_snapshot())


def _fake_gh_and_git(calls: list[list[str]]) -> Any:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    return fake


def test_generic_prompt_carries_no_alan_copilot_resolve_script(tmp_path: Path) -> None:
    ws = create(
        "fix-task-prompt", "iterate", root=tmp_path / "ws2",
        engine_key="acme-corp/widget-app",
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, _widget_snapshot())
    cfg = governing_config_for_ws(ws)
    prompt = fix_mod.render_system_prompt(cfg, tmp_path)
    assert "scripts/alan_copilot_resolve.py" not in prompt
    assert "copilot_resolution.yaml" in prompt


def test_maybe_resolve_called_on_pushed_with_second_engine_forge_target(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=_pr_push.PushOutcome.PUSHED)
    synthesis = (
        "## Summary\nFix landed.\n\n```yaml copilot_resolution.yaml\n"
        "- thread_id: PRTI_1\n  mode: fix\n```\n"
    )
    _patch_streaming(monkeypatch, repo, result_text=synthesis, write_files={"a.py": "x\n"})
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_maybe_resolve(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(_copilot_resolution, "maybe_resolve", fake_maybe_resolve)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(calls) == 1
    args, _kwargs = calls[0]
    assert args[3] == 42
    forge_target = cast(ForgeTarget, args[4])
    assert forge_target.repo.slug == "acme-corp/widget-app"


def test_maybe_resolve_called_on_reconciled(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=_pr_push.PushOutcome.RECONCILED)
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})
    calls: list[object] = []
    monkeypatch.setattr(_copilot_resolution, "maybe_resolve", lambda *a, **k: calls.append(1))
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(calls) == 1


def test_maybe_resolve_called_without_fix_rows_on_diverged_escalate(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=_pr_push.PushOutcome.DIVERGED_ESCALATE)
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        _copilot_resolution, "maybe_resolve", lambda *a, **k: calls.append(k)
    )
    monkeypatch.setattr(_pr_push, "try_resolve_remote_sha", lambda *_a, **_k: "")
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(calls) == 1
    assert calls[0]["allow_fix"] is False


def test_comment_ack_row_threads_repo_and_writes_ledger(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=_pr_push.PushOutcome.PUSHED)
    synthesis = (
        "## Summary\nAcked.\n\n```yaml copilot_resolution.yaml\n"
        "- database_id: 77\n  mode: comment_ack\n  body: not applicable here\n```\n"
    )
    _patch_streaming(monkeypatch, repo, result_text=synthesis, write_files={"a.py": "x\n"})
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    always(_active_fake_forge, "comment_on_pr", api_result("ok"))
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    comment_calls = [c for c in _active_fake_forge.calls if c[0] == "comment_on_pr"]
    assert comment_calls == [
        ("comment_on_pr", (42, "not applicable here"), {"forge_target": _WIDGET_FORGE}),
    ]
    assert _pr_state_paths.load_acked_comment_ids(42, _WIDGET_FORGE) == frozenset({77})
    assert _pr_state_paths.load_acked_comment_ids(42, DEFAULT_FORGE_TARGET) == frozenset()


def test_copilot_resolution_applies_on_commit_free_acknowledge_turn(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    push_capture: list[dict[str, object]] = []
    _patch_push_fix_branch(monkeypatch, capture=push_capture)
    synthesis = (
        "## Summary\nNo code change needed; nit.\n\n"
        "```yaml copilot_resolution.yaml\n"
        "- thread_id: PRTI_ack1\n  mode: acknowledge\n  body: out of scope\n```\n"
    )
    _patch_streaming(monkeypatch, repo, result_text=synthesis)
    calls: list[list[str]] = []
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git(calls))
    monkeypatch.setattr(
        pr_threads_mod, "list_unresolved_threads",
        lambda *_a, **_k: (
            pr_threads_mod.ReviewThread(
                thread_id="PRTI_ack1", author_login="copilot-pull-request-reviewer",
                body="finding", path="a.py", line=1, is_outdated=False,
            ),
        ),
    )
    always(_active_fake_forge, "reply_to_thread", api_result("ok"))
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert not push_capture
    reply_calls = [c for c in _active_fake_forge.calls if c[0] == "reply_to_thread"]
    resolve_calls = [c for c in calls if "resolveReviewThread" in " ".join(c)]
    assert [c[1] for c in reply_calls] == [("PRTI_ack1", "out of scope")]
    assert resolve_calls


def test_copilot_resolution_applies_alongside_non_actionable_early_return(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    push_capture: list[dict[str, object]] = []
    _patch_push_fix_branch(monkeypatch, capture=push_capture)
    synthesis = (
        "## Summary\nJudge finding is a false positive; Copilot comment acked.\n\n"
        "```yaml non_actionable_feedback.yaml\n"
        "reason: The judge finding is a false positive\n"
        "```\n\n"
        "```yaml copilot_resolution.yaml\n"
        "- database_id: 555\n  mode: comment_ack\n  body: not applicable\n```\n"
    )
    _patch_streaming(monkeypatch, repo, result_text=synthesis)
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    always(_active_fake_forge, "comment_on_pr", api_result("ok"))
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert not push_capture
    assert handover.handover_type == "forward"
    comment_calls = [c for c in _active_fake_forge.calls if c[0] == "comment_on_pr"]
    assert comment_calls
    assert _pr_state_paths.load_acked_comment_ids(42, _WIDGET_FORGE) == frozenset({555})


def test_run_fetches_threads_once_and_forwards_same_tuple_to_maybe_resolve(
    workspace: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.chdir(repo)
    _add_branch_at_main(repo, "teams/fix-task-existing")
    _patch_branch_resolution(monkeypatch, branch="teams/fix-task-existing")
    _patch_push_fix_branch(monkeypatch, return_value=_pr_push.PushOutcome.PUSHED)
    _patch_streaming(monkeypatch, repo, write_files={"a.py": "x\n"})
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    thread = pr_threads_mod.ReviewThread(
        thread_id="PRTI_1", author_login="copilot-pull-request-reviewer",
        body="finding", path="a.py", line=1, is_outdated=False,
    )
    fetch_calls: list[int] = []

    def fake_list_unresolved(*_a: object, **_k: object) -> tuple[pr_threads_mod.ReviewThread, ...]:
        fetch_calls.append(1)
        return (thread,)

    monkeypatch.setattr(pr_threads_mod, "list_unresolved_threads", fake_list_unresolved)
    monkeypatch.setattr(pr_sonar_mod, "list_sonarcloud_annotations", lambda *_a, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_k: None)
    resolve_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        _copilot_resolution, "maybe_resolve", lambda *_a, **k: resolve_calls.append(k)
    )
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(fetch_calls) == 1
    assert resolve_calls[0]["listed"] == (thread,)


def test_build_user_text_performs_no_fetch_when_threads_provided(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not fetch when threads is already provided")

    monkeypatch.setattr(pr_threads_mod, "list_unresolved_threads", boom)
    monkeypatch.setattr(pr_sonar_mod, "list_sonarcloud_annotations", lambda *_a, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_k: None)
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    thread = pr_threads_mod.ReviewThread(
        thread_id="PRTI_1", author_login="copilot-pull-request-reviewer",
        body="finding", path="a.py", line=1, is_outdated=False,
    )
    text = fix_mod._build_user_text(workspace, prior, None, None, (thread,))
    assert "PRTI_1" in text


def test_build_user_text_omits_threads_block_when_listing_empty(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.setattr(pr_sonar_mod, "list_sonarcloud_annotations", lambda *_a, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_k: None)
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    text = fix_mod._build_user_text(workspace, prior, None, None, ())
    assert "## Unresolved PR threads" not in text


def test_ack_cap_refusal_surfaces_in_next_iteration_prompt(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    always(_active_fake_forge, "comment_on_pr", api_result("ok"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_and_git([]))
    for i in range(_copilot_resolution.ACK_CAP):
        row = CopilotResolutionRow(database_id=i, mode="comment_ack", body="ack")
        _copilot_resolution.apply(
            (row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="sha-fixed", listed=(),
        )
    over_row = CopilotResolutionRow(database_id=999, mode="comment_ack", body="ack")
    _copilot_resolution.apply(
        (over_row,), pr_number=42, forge_target=_WIDGET_FORGE, head_sha="sha-fixed", listed=(),
    )

    prior = _verification_handover(workspace, pr_number=42, review_artifact=None)
    monkeypatch.setattr(pr_threads_mod, "list_unresolved_threads", lambda *_a, **_k: ())
    monkeypatch.setattr(pr_sonar_mod, "list_sonarcloud_annotations", lambda *_a, **_k: ())
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_k: None)
    text = fix_mod._build_user_text(workspace, prior)
    assert _copilot_resolution_block._REFUSAL_MESSAGES["ack_cap_exceeded"] in text  # noqa: SLF001

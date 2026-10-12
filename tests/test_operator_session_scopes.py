from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

import pytest

from alan import claude_cli_grants, operator_session, roots
from alan.control_token_store import CONTROL_TOKEN_FILENAME, CONTROL_TOKEN_META_FILENAME
from alan.operator_checkout import legacy_checkout_path
from alan.operator_prompt import Scope
from alan.operator_session import OperatorTargetNotFound, OperatorUnavailable
from alan.workspace import InterventionAction, Workspace, create, read
from tests.conftest import FakeGit, FakeTmux, git_reply, register_test_engine

_DENY_FLAG = "--disallowedTools"
_ADD_DIR_FLAG = "--add-dir"


@pytest.fixture(autouse=True)
def _available(operator_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del operator_state
    monkeypatch.setattr(operator_session, "availability", lambda: None)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def default_repo() -> str:
    register_test_engine("acme/default-repo", default=True)
    return "acme/default-repo"


def _spawned(fake_tmux: FakeTmux) -> tuple[list[str], str]:
    call = fake_tmux.calls_of("new-session")[-1]
    return shlex.split(call[-1]), call[call.index("-c") + 1]


def _add_dirs(command: list[str]) -> list[str]:
    if _ADD_DIR_FLAG not in command:
        return []
    return command[command.index(_ADD_DIR_FLAG) + 1 : command.index(_DENY_FLAG)]


def _stuck_workspace(ws_root: Path, engine_key: str | None = None) -> Workspace:
    return create("Stuck thing", "description", root=ws_root, engine_key=engine_key)


def test_a_global_start_needs_no_target_and_sits_idle_in_the_operator_root(
    fake_tmux: FakeTmux, operator_state: Path
) -> None:
    info = operator_session.start(Scope.FLEET, None)
    command, cwd = _spawned(fake_tmux)
    assert command[:2] == ["claude", _ADD_DIR_FLAG]
    assert cwd == str(operator_state)
    assert info.scope is Scope.FLEET
    assert info.target is None


def test_a_global_start_reaches_the_workspaces_logs_and_state_folders(
    fake_tmux: FakeTmux,
) -> None:
    operator_session.start(Scope.FLEET, None)
    command, _ = _spawned(fake_tmux)
    assert _add_dirs(command) == [
        str(roots.workspaces_root().absolute()),
        str(roots.logs_root().absolute()),
        str(roots.state_root().absolute()),
    ]


def test_a_global_start_holds_every_connected_repository_and_nothing_else(
    operator_state: Path,
) -> None:
    register_test_engine("acme/widget")
    register_test_engine("acme/gadget")
    operator_session.start(Scope.FLEET, None)
    checkouts = sorted(
        path.relative_to(operator_state).as_posix() for path in operator_state.glob("*/*")
    )
    assert checkouts == ["acme/gadget", "acme/widget", "install-owner/install-repo"]
    assert sorted(path.name for path in operator_state.iterdir()) == ["acme", "install-owner"]


def test_the_operator_root_is_neither_the_install_nor_inside_it_nor_its_parent(
    operator_state: Path, tmp_path: Path
) -> None:
    install = tmp_path / "operator-install"
    assert operator_state != install
    assert not operator_state.is_relative_to(install)
    assert operator_state != install.parent


def test_a_global_start_survives_another_repository_failing_to_clone_and_warns(
    fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    register_test_engine("acme/widget")
    fake_git.fail(
        "clone", "acme/widget", git_reply(returncode=128, stderr="fatal: https://u:p@h.test/x")
    )
    info = operator_session.start(Scope.FLEET, None)
    assert fake_tmux.live is True
    assert info.update_warning is not None
    assert info.update_warning.startswith("acme/widget: clone failed: ")
    assert "u:p" not in info.update_warning
    current = operator_session.current()
    assert current is not None
    assert current.update_warning == info.update_warning


def test_a_global_start_is_unavailable_when_the_installs_own_checkout_fails(
    fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    register_test_engine("acme/widget")
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: offline"))
    with pytest.raises(OperatorUnavailable) as excinfo:
        operator_session.start(Scope.FLEET, None)
    assert excinfo.value.unavailable.reason == "checkout_clone_failed"
    assert fake_tmux.calls_of("new-session") == []
    assert not operator_session.meta_path().exists()


def test_a_repo_start_sits_idle_inside_that_repositorys_checkout(
    fake_tmux: FakeTmux, operator_state: Path
) -> None:
    register_test_engine("acme/widget")
    info = operator_session.start(Scope.REPO, "acme/widget")
    command, cwd = _spawned(fake_tmux)
    assert cwd == str(operator_state / "acme" / "widget")
    assert command[:2] == ["claude", _DENY_FLAG]
    assert _ADD_DIR_FLAG not in command
    assert info.target == "acme/widget"


def test_a_repo_start_gets_no_fleet_state_folders(fake_tmux: FakeTmux) -> None:
    register_test_engine("acme/widget")
    operator_session.start(Scope.REPO, "acme/widget")
    command, _ = _spawned(fake_tmux)
    rules = claude_cli_grants.credential_deny_rules()
    outside_rules = [arg for arg in command if arg not in rules]
    assert not any(str(roots.state_root()) in arg for arg in outside_rules)
    assert not any(str(roots.workspaces_root()) in arg for arg in outside_rules)


@pytest.mark.parametrize("slug", ["stranger/unknown", "Acme/Widget"])
def test_a_repo_that_is_not_connected_is_refused_and_nothing_is_touched(
    fake_tmux: FakeTmux, fake_git: FakeGit, slug: str
) -> None:
    register_test_engine("acme/widget")
    with pytest.raises(OperatorTargetNotFound, match="not connected"):
        operator_session.start(Scope.REPO, slug)
    assert fake_tmux.calls == []
    assert fake_git.calls == []


def test_a_hidden_repo_is_refused_for_the_repo_scope(
    fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    register_test_engine("quiet/repo", hidden=True)
    with pytest.raises(OperatorTargetNotFound):
        operator_session.start(Scope.REPO, "quiet/repo")
    assert fake_tmux.calls == []
    assert fake_git.calls == []


def test_a_repo_checkout_is_created_on_first_use_and_fast_forwarded_later_without_a_reset(
    fake_git: FakeGit, operator_state: Path
) -> None:
    register_test_engine("acme/widget")
    operator_session.start(Scope.REPO, "acme/widget")
    checkout = operator_state / "acme" / "widget"
    assert (checkout / ".git").is_dir()
    assert fake_git.verbs().count("clone") == 1
    (checkout / "local-work.txt").write_text("operator was here")
    operator_session.start(Scope.REPO, "acme/widget", replace=True)
    assert fake_git.verbs().count("clone") == 1
    assert fake_git.verbs()[-2:] == ["fetch", "merge"]
    assert "reset" not in fake_git.verbs()
    assert (checkout / "local-work.txt").read_text() == "operator was here"


def test_a_repo_update_failure_is_a_warning_not_a_blocker(
    fake_tmux: FakeTmux, fake_git: FakeGit, operator_state: Path
) -> None:
    register_test_engine("acme/widget")
    (operator_state / "acme" / "widget" / ".git").mkdir(parents=True)
    fake_git.fail("fetch", "acme/widget", git_reply(stderr="fatal: unreachable"))
    info = operator_session.start(Scope.REPO, "acme/widget")
    assert fake_tmux.live is True
    assert info.update_warning is not None
    assert "fetch failed" in info.update_warning


def test_a_repo_whose_first_clone_fails_is_unavailable(
    fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    register_test_engine("acme/widget")
    fake_git.fail("clone", "acme/widget", git_reply(returncode=128, stderr="fatal: gone"))
    with pytest.raises(OperatorUnavailable) as excinfo:
        operator_session.start(Scope.REPO, "acme/widget")
    assert excinfo.value.unavailable.reason == "checkout_clone_failed"
    assert fake_tmux.calls_of("new-session") == []


def test_a_workspace_start_runs_in_the_default_repository_checkout_and_starts_analysing(
    fake_tmux: FakeTmux, ws_root: Path, operator_state: Path, default_repo: str
) -> None:
    workspace = _stuck_workspace(ws_root)
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    command, cwd = _spawned(fake_tmux)
    assert cwd == str(operator_state / "acme" / "default-repo")
    assert command[0] == "claude"
    assert f"Scope: workspace stuck-thing, in repository {default_repo}." in command[1]
    assert "Rules for this session:" in command[1]
    assert command[2] == _ADD_DIR_FLAG


def test_a_workspace_start_reaches_its_folder_the_logs_and_the_install_docs(
    fake_tmux: FakeTmux, ws_root: Path, operator_state: Path, default_repo: str
) -> None:
    del default_repo
    workspace = _stuck_workspace(ws_root)
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    command, _ = _spawned(fake_tmux)
    docs = operator_state / "install-owner" / "install-repo" / "docs"
    assert _add_dirs(command) == [
        str((ws_root / workspace.task_id).absolute()),
        str(roots.logs_root().absolute()),
        str(docs),
    ]
    assert str(ws_root / workspace.task_id) in command[1]
    assert f"{docs}/architecture/README.md" in command[1]


def test_a_workspace_pinned_to_an_engine_uses_that_engines_checkout(
    fake_tmux: FakeTmux, ws_root: Path, operator_state: Path
) -> None:
    register_test_engine("acme/widget")
    workspace = _stuck_workspace(ws_root, engine_key="acme/widget")
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    _, cwd = _spawned(fake_tmux)
    assert cwd == str(operator_state / "acme" / "widget")


def test_a_workspace_of_a_hidden_repository_is_still_allowed(
    fake_tmux: FakeTmux, ws_root: Path, operator_state: Path
) -> None:
    register_test_engine("quiet/repo", hidden=True)
    workspace = _stuck_workspace(ws_root, engine_key="quiet/repo")
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    _, cwd = _spawned(fake_tmux)
    assert cwd == str(operator_state / "quiet" / "repo")


def test_a_workspace_in_the_installs_own_repository_runs_in_the_install_checkout(
    fake_tmux: FakeTmux, ws_root: Path, operator_state: Path
) -> None:
    register_test_engine("install-owner/install-repo")
    workspace = _stuck_workspace(ws_root, engine_key="install-owner/install-repo")
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    command, cwd = _spawned(fake_tmux)
    assert cwd == str(operator_state / "install-owner" / "install-repo")
    assert len(_add_dirs(command)) == 2


@pytest.mark.parametrize("engine_key", ["stranger/unknown", None])
def test_a_workspace_whose_repository_is_not_connected_is_refused_with_nothing_touched(
    fake_tmux: FakeTmux, fake_git: FakeGit, ws_root: Path, engine_key: str | None
) -> None:
    workspace = _stuck_workspace(ws_root, engine_key=engine_key)
    with pytest.raises(OperatorTargetNotFound, match="not connected"):
        operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert fake_tmux.calls == []
    assert fake_git.calls == []
    assert read(workspace.task_id, root=ws_root).meta.interventions == []


def test_a_workspace_start_is_unavailable_when_the_install_checkout_fails(
    fake_tmux: FakeTmux, fake_git: FakeGit, ws_root: Path, default_repo: str
) -> None:
    del default_repo
    workspace = _stuck_workspace(ws_root)
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: offline"))
    with pytest.raises(OperatorUnavailable) as excinfo:
        operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert excinfo.value.unavailable.reason == "checkout_clone_failed"
    assert fake_tmux.calls_of("new-session") == []
    assert read(workspace.task_id, root=ws_root).meta.interventions == []


def test_a_workspace_start_still_records_the_intervention(
    ws_root: Path, default_repo: str
) -> None:
    del default_repo
    workspace = _stuck_workspace(ws_root)
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    actions = [i.action for i in read(workspace.task_id, root=ws_root).meta.interventions]
    assert actions == [InterventionAction.OPERATOR_TERMINAL]


@pytest.mark.parametrize("scope", [Scope.FLEET, Scope.REPO, Scope.WORKSPACE])
def test_every_scope_blocks_the_control_token_its_metadata_and_the_secrets_file(
    fake_tmux: FakeTmux, ws_root: Path, scope: Scope, default_repo: str
) -> None:
    del default_repo
    register_test_engine("acme/widget")
    task_id = _stuck_workspace(ws_root).task_id
    target = {Scope.FLEET: None, Scope.REPO: "acme/widget", Scope.WORKSPACE: task_id}[scope]
    operator_session.start(scope, target, workspaces_root=ws_root)
    command, _ = _spawned(fake_tmux)
    rules = claude_cli_grants.credential_deny_rules()
    assert command[-len(rules) - 1 :] == [_DENY_FLAG, *rules]
    state = roots.state_root().absolute()
    for protected in (
        state / CONTROL_TOKEN_FILENAME,
        state / CONTROL_TOKEN_META_FILENAME,
        state.parent / "secrets.env",
    ):
        assert f"Read(/{protected})" in rules


def test_the_legacy_install_checkout_is_moved_into_the_new_layout_by_a_global_start(
    fake_tmux: FakeTmux, operator_state: Path
) -> None:
    legacy = legacy_checkout_path()
    (legacy / ".git").mkdir(parents=True)
    (legacy / "local-work.txt").write_text("operator left this here")
    operator_session.start(Scope.FLEET, None)
    migrated = operator_state / "install-owner" / "install-repo"
    assert not legacy.exists()
    assert (migrated / "local-work.txt").read_text() == "operator left this here"
    assert fake_tmux.live is True


def test_the_legacy_checkout_is_moved_only_after_a_replaced_session_is_gone(
    fake_tmux: FakeTmux, operator_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)
    shutil.rmtree(operator_state / "install-owner" / "install-repo")
    (legacy_checkout_path() / ".git").mkdir(parents=True)
    live_during_rename: list[bool] = []
    real_rename = os.rename

    def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        live_during_rename.append(fake_tmux.live)
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", spy)
    operator_session.start(Scope.FLEET, None, replace=True)
    assert live_during_rename == [False]
    assert not legacy_checkout_path().exists()


def test_a_legacy_checkout_that_collides_with_the_new_layout_is_left_alone_with_a_warning(
    operator_state: Path,
) -> None:
    legacy = legacy_checkout_path()
    (legacy / ".git").mkdir(parents=True)
    (legacy / "local-work.txt").write_text("keep")
    (operator_state / "install-owner" / "install-repo" / ".git").mkdir(parents=True)
    info = operator_session.start(Scope.FLEET, None)
    assert (legacy / "local-work.txt").read_text() == "keep"
    assert info.update_warning is not None
    assert "left in place" in info.update_warning

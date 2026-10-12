from __future__ import annotations

from pathlib import Path

import pytest

from alan import claude_cli_grants, operator_launch, roots
from alan.control_token_store import CONTROL_TOKEN_FILENAME, CONTROL_TOKEN_META_FILENAME
from alan.engine_config import RepoIdentity
from alan.operator_checkout import CheckoutCloneFailed
from tests.conftest import FakeGit, git_reply, register_test_engine

_ORIGIN = "https://example.test/install-owner/install-repo.git"
_INSTALL = RepoIdentity(owner="install-owner", repo="install-repo")
_WIDGET = RepoIdentity(owner="acme", repo="widget")
_DENY_FLAG = "--disallowedTools"
_ADD_DIR_FLAG = "--add-dir"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "operator-install-operator-repos"


@pytest.fixture
def install_checkout(root: Path) -> Path:
    return root / "install-owner" / "install-repo"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    return tmp_path / "workspaces" / "fix-the-thing"


def _seed(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def _deny_rules(argv: tuple[str, ...]) -> list[str]:
    return list(argv[argv.index(_DENY_FLAG) + 1 :])


def _add_dirs(argv: tuple[str, ...]) -> list[str]:
    if _ADD_DIR_FLAG not in argv:
        return []
    start = argv.index(_ADD_DIR_FLAG) + 1
    return list(argv[start : argv.index(_DENY_FLAG)])


def _workspace_plan(identity: RepoIdentity, workspace_dir: Path) -> operator_launch.LaunchPlan:
    return operator_launch.workspace_plan(
        identity, task_id="fix-the-thing", workspace_dir=workspace_dir
    )


def _clone_urls(fake_git: FakeGit) -> list[str]:
    return [args[2] for args, _cwd, _timeout in fake_git.calls_of("clone")]


def test_the_executable_is_claude() -> None:
    assert operator_launch.CLAUDE_EXECUTABLE == "claude"


def test_the_global_plan_is_idle_in_the_operator_root_with_the_fleet_folders_added(
    fake_git: FakeGit, root: Path
) -> None:
    del fake_git
    plan = operator_launch.global_plan()
    assert plan.cwd == root
    assert plan.warning is None
    expected_dirs = [
        str(roots.workspaces_root().absolute()),
        str(roots.logs_root().absolute()),
        str(roots.state_root().absolute()),
    ]
    assert plan.argv == (
        "claude",
        _ADD_DIR_FLAG,
        *expected_dirs,
        _DENY_FLAG,
        *claude_cli_grants.credential_deny_rules(),
    )


def test_the_global_plan_holds_every_connected_repository_and_nothing_else(
    fake_git: FakeGit, root: Path
) -> None:
    register_test_engine("acme/widget")
    register_test_engine("acme/gadget")
    plan = operator_launch.global_plan()
    assert plan.warning is None
    found = sorted(
        str(path.relative_to(root))
        for path in root.rglob(".git")
        if path.parent.parent.parent == root
    )
    assert found == [
        "acme/gadget/.git",
        "acme/widget/.git",
        "install-owner/install-repo/.git",
    ]
    assert sorted(path.name for path in root.iterdir()) == ["acme", "install-owner"]
    assert len(fake_git.calls_of("clone")) == 3


def test_the_global_plan_warns_but_still_plans_when_another_repository_fails_to_clone(
    fake_git: FakeGit,
) -> None:
    register_test_engine("acme/widget")
    fake_git.fail("clone", "acme/widget", git_reply(returncode=128, stderr="fatal: offline"))
    plan = operator_launch.global_plan()
    assert plan.warning == "acme/widget: clone failed: fatal: offline"
    assert _ADD_DIR_FLAG in plan.argv


def test_the_global_plan_fails_when_the_installs_own_checkout_cannot_be_cloned(
    fake_git: FakeGit,
) -> None:
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: offline"))
    with pytest.raises(CheckoutCloneFailed, match="fatal: offline"):
        operator_launch.global_plan()


def test_the_global_plan_makes_relative_fleet_folders_absolute(
    fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_git
    monkeypatch.setenv("ALAN_LOGS_ROOT", "relative-logs")
    plan = operator_launch.global_plan()
    assert str(Path("relative-logs").absolute()) in _add_dirs(plan.argv)
    assert "relative-logs" not in _add_dirs(plan.argv)


def test_a_repo_plan_is_idle_inside_that_repositorys_checkout_with_no_extra_folders(
    fake_git: FakeGit, root: Path
) -> None:
    plan = operator_launch.repo_plan(_WIDGET)
    assert plan.cwd == root / "acme" / "widget"
    assert plan.warning is None
    assert plan.argv == (
        "claude",
        _DENY_FLAG,
        *claude_cli_grants.credential_deny_rules(),
    )
    assert _clone_urls(fake_git) == ["https://github.com/acme/widget.git"]


def test_a_repo_plan_for_the_installs_repository_clones_from_its_origin(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    plan = operator_launch.repo_plan(_INSTALL)
    assert plan.cwd == install_checkout
    assert _clone_urls(fake_git) == [_ORIGIN]


def test_a_repo_plan_falls_back_to_github_when_the_installs_origin_is_unreadable(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    fake_git.fail("config", "remote.origin.url", git_reply(stderr="no such key"))
    plan = operator_launch.repo_plan(_INSTALL)
    assert plan.cwd == install_checkout
    assert _clone_urls(fake_git) == ["https://github.com/install-owner/install-repo.git"]


def test_a_repo_plan_updates_an_existing_checkout_without_resetting_it(
    fake_git: FakeGit, root: Path
) -> None:
    _seed(root / "acme" / "widget")
    (root / "acme" / "widget" / "local-work.txt").write_text("keep me")
    plan = operator_launch.repo_plan(_WIDGET)
    assert plan.warning is None
    assert [verb for verb in fake_git.verbs() if verb != "config"] == ["fetch", "merge"]
    assert (root / "acme" / "widget" / "local-work.txt").read_text() == "keep me"


def test_a_repo_plan_update_failure_is_a_warning(fake_git: FakeGit, root: Path) -> None:
    _seed(root / "acme" / "widget")
    fake_git.fail("fetch", "acme/widget", git_reply(stderr="fatal: unreachable"))
    plan = operator_launch.repo_plan(_WIDGET)
    assert plan.cwd == root / "acme" / "widget"
    assert plan.warning is not None
    assert "fetch failed" in plan.warning
    assert not plan.warning.startswith("acme/widget:")


def test_a_repo_plan_fails_on_the_first_clone_of_the_started_repository(
    fake_git: FakeGit,
) -> None:
    fake_git.fail("clone", "acme/widget", git_reply(returncode=128, stderr="fatal: gone"))
    with pytest.raises(CheckoutCloneFailed, match="fatal: gone"):
        operator_launch.repo_plan(_WIDGET)


def test_a_workspace_plan_starts_in_the_workspace_repository_with_the_analysis_prompt(
    fake_git: FakeGit, root: Path, install_checkout: Path, workspace_dir: Path
) -> None:
    plan = _workspace_plan(_WIDGET, workspace_dir)
    assert plan.cwd == root / "acme" / "widget"
    assert plan.warning is None
    assert plan.argv[0] == "claude"
    prompt = plan.argv[1]
    assert not prompt.startswith("--")
    assert "Scope: workspace fix-the-thing, in repository acme/widget." in prompt
    assert f"Look at {workspace_dir} first" in prompt
    assert f"{install_checkout}/docs/architecture/README.md" in prompt
    assert _clone_urls(fake_git) == [_ORIGIN, "https://github.com/acme/widget.git"]


def test_a_workspace_plan_reaches_the_workspace_folder_the_logs_and_the_install_docs(
    fake_git: FakeGit, install_checkout: Path, workspace_dir: Path
) -> None:
    del fake_git
    plan = _workspace_plan(_WIDGET, workspace_dir)
    assert _add_dirs(plan.argv) == [
        str(workspace_dir),
        str(roots.logs_root().absolute()),
        str(install_checkout / "docs"),
    ]
    assert plan.argv[2] == _ADD_DIR_FLAG


def test_every_path_in_the_workspace_prompt_is_absolute_and_reachable(
    fake_git: FakeGit, workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_git
    monkeypatch.setenv("ALAN_LOGS_ROOT", "relative-logs")
    plan = operator_launch.workspace_plan(
        _WIDGET, task_id="fix-the-thing", workspace_dir=Path("relative-ws") / "fix-the-thing"
    )
    prompt = plan.argv[1]
    reachable = _add_dirs(plan.argv)
    assert str(Path("relative-logs").absolute()) in reachable
    assert str(Path("relative-ws").absolute() / "fix-the-thing") in reachable
    assert f"- logs: {Path('relative-logs').absolute()}" in prompt
    assert f"- workspace: {Path('relative-ws').absolute() / 'fix-the-thing'}" in prompt


def test_a_workspace_in_the_installs_own_repository_adds_no_docs_folder(
    fake_git: FakeGit, install_checkout: Path, workspace_dir: Path
) -> None:
    plan = _workspace_plan(_INSTALL, workspace_dir)
    assert plan.cwd == install_checkout
    assert _add_dirs(plan.argv) == [str(workspace_dir), str(roots.logs_root().absolute())]
    assert f"{install_checkout}/docs/architecture/README.md" in plan.argv[1]
    assert _clone_urls(fake_git) == [_ORIGIN]


def test_the_installs_repository_is_recognised_whatever_its_casing(
    fake_git: FakeGit, install_checkout: Path, workspace_dir: Path
) -> None:
    shouted = RepoIdentity(owner="INSTALL-OWNER", repo="INSTALL-REPO")
    plan = _workspace_plan(shouted, workspace_dir)
    assert plan.cwd == install_checkout
    assert len(fake_git.calls_of("clone")) == 1


def test_a_workspace_plan_fails_when_the_install_checkout_cannot_be_cloned(
    fake_git: FakeGit, workspace_dir: Path
) -> None:
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: offline"))
    with pytest.raises(CheckoutCloneFailed, match="fatal: offline"):
        _workspace_plan(_WIDGET, workspace_dir)
    assert _clone_urls(fake_git) == [_ORIGIN]


def test_a_workspace_plan_fails_when_the_install_has_no_origin(
    fake_git: FakeGit, workspace_dir: Path
) -> None:
    fake_git.fail("config", "remote.origin.url", git_reply(stderr="no such key"))
    with pytest.raises(CheckoutCloneFailed, match="no origin remote"):
        _workspace_plan(_WIDGET, workspace_dir)
    assert fake_git.calls_of("clone") == []


def test_a_workspace_plan_fails_on_the_first_clone_of_the_workspace_repository(
    fake_git: FakeGit, workspace_dir: Path
) -> None:
    fake_git.fail("clone", "acme/widget", git_reply(returncode=128, stderr="fatal: gone"))
    with pytest.raises(CheckoutCloneFailed, match="fatal: gone"):
        _workspace_plan(_WIDGET, workspace_dir)


def test_workspace_plan_update_failures_are_warnings_and_only_the_docs_checkout_is_prefixed(
    fake_git: FakeGit, root: Path, workspace_dir: Path
) -> None:
    _seed(root / "install-owner" / "install-repo")
    _seed(root / "acme" / "widget")
    fake_git.fail("fetch", "install-repo", git_reply(stderr="boom install"))
    fake_git.fail("fetch", "acme/widget", git_reply(stderr="boom widget"))
    plan = _workspace_plan(_WIDGET, workspace_dir)
    assert plan.warning is not None
    widget_part, install_part = plan.warning.split("; ")
    assert widget_part.startswith("checkout not updated (fetch failed)")
    assert "boom widget" in widget_part
    assert install_part.startswith("install-owner/install-repo: checkout not updated")
    assert "boom install" in install_part


def test_a_shared_checkout_update_failure_is_one_unprefixed_warning(
    fake_git: FakeGit, install_checkout: Path, workspace_dir: Path
) -> None:
    _seed(install_checkout)
    fake_git.fail("fetch", "install-repo", git_reply(stderr="boom install"))
    plan = _workspace_plan(_INSTALL, workspace_dir)
    assert plan.warning is not None
    assert plan.warning.startswith("checkout not updated (fetch failed)")
    assert ";" not in plan.warning


@pytest.mark.parametrize("scope", ["global", "repo", "workspace"])
def test_every_plan_ends_with_the_fleet_credential_deny_rules(
    fake_git: FakeGit, workspace_dir: Path, scope: str
) -> None:
    del fake_git
    plans = {
        "global": operator_launch.global_plan,
        "repo": lambda: operator_launch.repo_plan(_WIDGET),
        "workspace": lambda: operator_launch.workspace_plan(
            _WIDGET, task_id="fix-the-thing", workspace_dir=workspace_dir
        ),
    }
    plan = plans[scope]()
    rules = claude_cli_grants.credential_deny_rules()
    assert plan.argv[-len(rules) - 1 :] == (_DENY_FLAG, *rules)
    assert plan.argv.count(_DENY_FLAG) == 1


@pytest.mark.parametrize("scope", ["global", "repo", "workspace"])
def test_the_deny_rules_block_the_control_token_its_metadata_and_the_secrets_file(
    fake_git: FakeGit, workspace_dir: Path, scope: str
) -> None:
    del fake_git
    plans = {
        "global": operator_launch.global_plan,
        "repo": lambda: operator_launch.repo_plan(_WIDGET),
        "workspace": lambda: operator_launch.workspace_plan(
            _WIDGET, task_id="fix-the-thing", workspace_dir=workspace_dir
        ),
    }
    rules = _deny_rules(plans[scope]().argv)
    state = roots.state_root().absolute()
    for protected in (
        state / CONTROL_TOKEN_FILENAME,
        state / CONTROL_TOKEN_META_FILENAME,
        state.parent / "secrets.env",
    ):
        assert f"Read(/{protected})" in rules


def test_global_and_repo_plans_inject_no_prompt(fake_git: FakeGit) -> None:
    del fake_git
    for plan in (operator_launch.global_plan(), operator_launch.repo_plan(_WIDGET)):
        assert plan.argv[1].startswith("--")
        assert all(not arg.startswith("Scope:") for arg in plan.argv)
        assert all("Rules for this session" not in arg for arg in plan.argv)

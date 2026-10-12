from __future__ import annotations

import json
import shlex
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from alan import operator_checkout, operator_session, operator_tmux
from alan.operator_prompt import InvalidScopeTarget, Scope
from alan.operator_session import (
    OperatorSessionActive,
    OperatorStartFailed,
    OperatorTargetNotFound,
    OperatorUnavailable,
    SessionInfo,
)
from alan.operator_tmux import run_tmux as real_run_tmux
from alan.workspace import create
from tests.conftest import FakeGit, FakeTmux, git_reply, register_test_engine

_TOOLS = ("tmux", "claude", "git")
_ALLOWED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "USER", "SHELL")
_REPO = "some-owner/some-repo"


@pytest.fixture(autouse=True)
def _connected_repo() -> None:
    register_test_engine(_REPO, default=True)


@pytest.fixture
def tool_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "tool-bin"
    directory.mkdir()
    for name in _TOOLS:
        executable = directory / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.fixture
def ready(operator_state: Path, tool_dir: Path, fake_tmux: FakeTmux) -> FakeTmux:
    del operator_state, tool_dir
    return fake_tmux


def _meta() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(operator_session.meta_path().read_text())
    return loaded


def _live_while_git_runs(
    ready: FakeTmux, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> list[bool]:
    seen: list[bool] = []

    def spy(args: Sequence[str], *, cwd: Path, timeout: float) -> Any:
        seen.append(ready.live)
        return fake_git(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(operator_checkout, "run_git", spy)
    return seen


def test_status_with_no_session_and_everything_present_is_quiet(ready: FakeTmux) -> None:
    del ready
    status = operator_session.status()
    assert status.session is None
    assert status.unavailable is None


def test_availability_is_none_when_every_prerequisite_is_present(ready: FakeTmux) -> None:
    del ready
    assert operator_session.availability() is None


def test_detached_mode_off_is_named_as_the_cause(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    del ready
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    unavailable = operator_session.status().unavailable
    assert unavailable is not None
    assert unavailable.reason == "detached_mode_off"
    assert "restart" in unavailable.detail
    assert "ALAN_DETACHED_WORKERS" in unavailable.detail


@pytest.mark.parametrize("tool", _TOOLS)
def test_each_missing_prerequisite_is_named_as_the_cause(
    ready: FakeTmux, tool_dir: Path, tool: str
) -> None:
    del ready
    (tool_dir / tool).unlink()
    unavailable = operator_session.status().unavailable
    assert unavailable is not None
    assert unavailable.reason == f"{tool}_missing"
    assert tool in unavailable.detail


def test_a_prerequisite_that_is_not_executable_counts_as_missing(
    ready: FakeTmux, tool_dir: Path
) -> None:
    del ready
    (tool_dir / "tmux").chmod(0o644)
    unavailable = operator_session.availability()
    assert unavailable is not None
    assert unavailable.reason == "tmux_missing"


def test_prerequisites_are_searched_on_the_allow_listed_path_not_the_services(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    del ready
    monkeypatch.delenv("PATH")
    unavailable = operator_session.availability()
    assert unavailable is not None
    assert unavailable.reason == "tmux_missing"


def test_detached_mode_is_checked_before_the_tools(
    ready: FakeTmux, tool_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del ready
    (tool_dir / "tmux").unlink()
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    unavailable = operator_session.availability()
    assert unavailable is not None
    assert unavailable.reason == "detached_mode_off"


def test_a_live_session_is_reported_and_unavailability_is_not_consulted(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    status = operator_session.status()
    assert status.session is not None
    assert status.session.scope is Scope.FLEET
    assert status.unavailable is None
    assert ready.live is True


def test_a_global_start_spawns_an_idle_claude_in_the_operator_root(
    ready: FakeTmux, operator_state: Path
) -> None:
    info = operator_session.start(Scope.FLEET, None)
    new_calls = ready.calls_of("new-session")
    assert len(new_calls) == 1
    command = shlex.split(new_calls[0][-1])
    assert command[0] == "claude"
    assert command[1] == "--add-dir"
    assert new_calls[0][new_calls[0].index("-c") + 1] == str(operator_state)
    assert info == SessionInfo(Scope.FLEET, None, info.started_at, None, False)


def test_a_repo_start_spawns_an_idle_claude_in_that_repositorys_checkout(
    ready: FakeTmux, operator_state: Path
) -> None:
    info = operator_session.start(Scope.REPO, _REPO)
    new_calls = ready.calls_of("new-session")
    assert len(new_calls) == 1
    command = shlex.split(new_calls[0][-1])
    assert command[:2] == ["claude", "--disallowedTools"]
    checkout = operator_state / "some-owner" / "some-repo"
    assert new_calls[0][new_calls[0].index("-c") + 1] == str(checkout)
    assert info.scope is Scope.REPO
    assert info.target == _REPO
    assert info.exited is False
    assert info.update_warning is None


def test_start_records_the_scope_privately_for_a_later_page_load(ready: FakeTmux) -> None:
    del ready
    info = operator_session.start(Scope.REPO, _REPO)
    path = operator_session.meta_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert _meta() == {
        "scope": "repo",
        "target": _REPO,
        "started_at": info.started_at,
        "update_warning": None,
    }
    current = operator_session.current()
    assert current == SessionInfo(Scope.REPO, _REPO, info.started_at, None, False)


def test_every_scope_runs_in_the_same_isolated_environment(
    ready: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_root = tmp_path / "ws"
    task_id = create("stuck", "d", root=ws_root).task_id
    seen: list[dict[str, str]] = []

    def recording_run(cmd: list[str], **kwargs: Any) -> Any:
        seen.append(kwargs["env"])
        return ready(cmd[1:], cwd=kwargs["cwd"])

    monkeypatch.setattr(operator_tmux, "run_tmux", real_run_tmux)
    monkeypatch.setattr(subprocess, "run", recording_run)
    monkeypatch.setenv("SERVICE_ONLY_VARIABLE", "service-value")
    monkeypatch.setenv("ALAN_CONTROL_TOKEN", "control-secret")
    for scope, target in [(Scope.FLEET, None), (Scope.REPO, _REPO), (Scope.WORKSPACE, task_id)]:
        operator_session.start(scope, target, replace=True, workspaces_root=ws_root)
    assert len(ready.calls_of("new-session")) == 3
    assert seen
    assert all(env == operator_tmux.session_env() for env in seen)
    assert all(set(env) <= {*_ALLOWED_ENV, "TERM"} for env in seen)
    assert all("service-value" not in env.values() for env in seen)
    assert all("control-secret" not in env.values() for env in seen)


def test_start_over_a_live_session_without_replace_is_refused_and_touches_nothing(
    ready: FakeTmux, fake_git: FakeGit
) -> None:
    operator_session.start(Scope.FLEET, None)
    before = list(ready.calls)
    git_before = list(fake_git.calls)
    with pytest.raises(OperatorSessionActive):
        operator_session.start(Scope.REPO, _REPO)
    assert [c for c in ready.calls[len(before):] if c[4] in {"kill-session", "new-session"}] == []
    assert fake_git.calls == git_before
    assert operator_session.current() is not None


def test_start_refuses_when_unavailable_and_spawns_nothing(
    ready: FakeTmux, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    with pytest.raises(OperatorUnavailable) as excinfo:
        operator_session.start(Scope.FLEET, None)
    assert excinfo.value.unavailable.reason == "detached_mode_off"
    assert ready.calls_of("new-session") == []
    assert fake_git.calls == []


def test_an_unavailable_replace_leaves_the_live_session_running(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    with pytest.raises(OperatorUnavailable):
        operator_session.start(Scope.FLEET, None, replace=True)
    assert ready.live is True
    assert ready.calls_of("kill-session") == []


def test_a_failed_install_clone_is_reported_as_unavailable_with_its_detail(
    ready: FakeTmux, fake_git: FakeGit
) -> None:
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="network down"))
    with pytest.raises(OperatorUnavailable) as excinfo:
        operator_session.start(Scope.FLEET, None)
    assert excinfo.value.unavailable.reason == "checkout_clone_failed"
    assert excinfo.value.unavailable.detail == "clone failed: network down"
    assert ready.calls_of("new-session") == []


def test_an_update_warning_does_not_stop_the_session_and_is_kept_for_the_page(
    ready: FakeTmux, fake_git: FakeGit, operator_state: Path
) -> None:
    (operator_state / "install-owner" / "install-repo" / ".git").mkdir(parents=True)
    fake_git.fail("fetch", "install-repo", git_reply(stderr="offline"))
    info = operator_session.start(Scope.FLEET, None)
    assert info.update_warning is not None
    assert info.update_warning.startswith("install-owner/install-repo: checkout not updated")
    assert ready.live is True
    current = operator_session.current()
    assert current is not None
    assert current.update_warning == info.update_warning


def test_the_checkouts_are_refreshed_only_after_a_replaced_session_is_gone(
    ready: FakeTmux, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)
    live_during_git = _live_while_git_runs(ready, fake_git, monkeypatch)
    operator_session.start(Scope.FLEET, None, replace=True)
    assert live_during_git
    assert set(live_during_git) == {False}


def test_the_checkouts_are_refreshed_on_a_plain_start_when_nothing_is_live(
    ready: FakeTmux, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_during_git = _live_while_git_runs(ready, fake_git, monkeypatch)
    operator_session.start(Scope.FLEET, None)
    assert live_during_git
    assert set(live_during_git) == {False}


def test_a_tmux_that_refuses_the_session_is_a_start_failure_and_leaves_no_record(
    ready: FakeTmux,
) -> None:
    ready.new_session_rc = 1
    ready.new_session_stderr = "server exited unexpectedly https://user:pw@host.test/x"
    with pytest.raises(OperatorStartFailed) as excinfo:
        operator_session.start(Scope.FLEET, None)
    assert "pw" not in str(excinfo.value)
    assert "server exited unexpectedly" in str(excinfo.value)
    assert not operator_session.meta_path().exists()


@pytest.mark.parametrize(
    "error", [OSError("no tmux"), subprocess.TimeoutExpired(["tmux"], 15)]
)
def test_a_tmux_that_cannot_run_is_a_start_failure(
    ready: FakeTmux, error: BaseException
) -> None:
    ready.raises["new-session"] = error
    with pytest.raises(OperatorStartFailed, match="tmux did not start"):
        operator_session.start(Scope.FLEET, None)
    assert not operator_session.meta_path().exists()


def test_a_session_record_that_cannot_be_written_stops_the_session_again(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(operator_session, "write_text_atomic", refuse)
    with pytest.raises(OperatorStartFailed, match="disk full"):
        operator_session.start(Scope.FLEET, None)
    assert ready.live is False
    assert len(ready.calls_of("kill-session")) == 1


def test_a_session_that_cannot_be_recorded_or_stopped_is_reported_as_still_running(
    ready: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(operator_session, "write_text_atomic", refuse)
    ready.kill_leaves_session = True
    with pytest.raises(OperatorStartFailed, match="disk full; the new session could not be"):
        operator_session.start(Scope.FLEET, None)
    assert ready.live is True
    current = operator_session.current()
    assert current is not None
    assert current.scope is Scope.FLEET


@pytest.mark.parametrize(
    ("scope", "target"),
    [(Scope.FLEET, "x"), (Scope.REPO, None), (Scope.REPO, "no-slash"), (Scope.WORKSPACE, "../up")],
)
def test_an_invalid_target_is_refused_before_anything_happens(
    ready: FakeTmux, fake_git: FakeGit, scope: Scope, target: str | None
) -> None:
    with pytest.raises(InvalidScopeTarget):
        operator_session.start(scope, target)
    assert ready.calls == []
    assert fake_git.calls == []


def test_a_workspace_scope_naming_no_workspace_is_refused_before_anything_happens(
    ready: FakeTmux, fake_git: FakeGit, tmp_path: Path
) -> None:
    with pytest.raises(OperatorTargetNotFound, match="no-such-workspace"):
        operator_session.start(Scope.WORKSPACE, "no-such-workspace", workspaces_root=tmp_path)
    assert ready.calls == []
    assert fake_git.calls == []


def test_a_session_with_no_record_is_reported_as_fleet_scoped(ready: FakeTmux) -> None:
    ready.live = True
    current = operator_session.current()
    assert current == SessionInfo(Scope.FLEET, None, None, None, False)


def test_a_damaged_record_is_treated_like_a_missing_one(ready: FakeTmux) -> None:
    ready.live = True
    operator_tmux.prepare_state_dir()
    operator_session.meta_path().write_text("{not json")
    current = operator_session.current()
    assert current is not None
    assert current.scope is Scope.FLEET
    assert current.started_at is None


def test_a_record_without_a_live_tmux_session_is_no_session(ready: FakeTmux) -> None:
    operator_session.start(Scope.FLEET, None)
    ready.live = False
    assert operator_session.current() is None


def test_an_exited_pane_is_reported_but_the_session_stays_live(ready: FakeTmux) -> None:
    operator_session.start(Scope.FLEET, None)
    ready.pane_dead = True
    current = operator_session.current()
    assert current is not None
    assert current.exited is True


def test_the_unavailable_detail_table_covers_every_named_reason() -> None:
    reasons = {
        "detached_mode_off",
        "tmux_missing",
        "claude_missing",
        "git_missing",
        "checkout_clone_failed",
    }
    assert reasons == set(operator_session._UNAVAILABLE_DETAILS)  # noqa: SLF001

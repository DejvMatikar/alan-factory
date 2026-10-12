from __future__ import annotations

import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import operator_session, operator_tmux
from alan.operator_prompt import Scope
from alan.operator_session import OperatorEndFailed, OperatorUnavailable
from alan.workspace import Workspace, create
from tests.conftest import FakeGit, FakeTmux, git_reply, register_test_engine

_TRANSCRIPT_NAME = re.compile(r"^operator_transcript-\d{8}T\d{6}Z\.txt$")


@pytest.fixture(autouse=True)
def _connected_repo() -> None:
    register_test_engine("o/r", default=True)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(ws_root: Path) -> Workspace:
    return create("Stuck thing", "description", root=ws_root)


@pytest.fixture(autouse=True)
def _available(operator_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del operator_state
    monkeypatch.setattr(operator_session, "availability", lambda: None)


def _transcripts(root: Path) -> list[Path]:
    return sorted(root.rglob("operator_transcript-*.txt"))


def test_end_with_no_session_reports_nothing_to_end(fake_tmux: FakeTmux) -> None:
    assert operator_session.end() is False
    assert fake_tmux.calls_of("kill-session") == []


@pytest.mark.parametrize(("scope", "target"), [(Scope.FLEET, None), (Scope.REPO, "o/r")])
def test_ending_a_fleet_or_repo_session_archives_nothing(
    fake_tmux: FakeTmux, tmp_path: Path, scope: Scope, target: str | None
) -> None:
    operator_session.start(scope, target)
    assert operator_session.end(workspaces_root=tmp_path / "ws") is True
    assert fake_tmux.calls_of("capture-pane") == []
    assert len(fake_tmux.calls_of("kill-session")) == 1
    assert fake_tmux.live is False
    assert not operator_session.meta_path().exists()
    assert _transcripts(tmp_path) == []


def test_ending_a_workspace_session_archives_the_transcript_on_the_workspace(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    fake_tmux.capture = "operator typed things\nclaude answered\n"
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert operator_session.end(workspaces_root=ws_root) is True
    archived = _transcripts(ws_root)
    assert len(archived) == 1
    assert archived[0].parent == workspace.path
    assert _TRANSCRIPT_NAME.match(archived[0].name)
    assert archived[0].read_text() == "operator typed things\nclaude answered\n"
    assert stat.S_IMODE(archived[0].stat().st_mode) == 0o600
    assert not operator_session.meta_path().exists()


def test_two_sessions_ended_in_the_same_second_keep_both_transcripts(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FrozenClock:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    monkeypatch.setattr(operator_session, "datetime", _FrozenClock)
    for screen in ("first screen\n", "second screen\n", "third screen\n"):
        fake_tmux.capture = screen
        operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
        operator_session.end(workspaces_root=ws_root)
    archived = {path.name: path.read_text() for path in _transcripts(ws_root)}
    assert archived == {
        "operator_transcript-20260102T030405Z.txt": "first screen\n",
        "operator_transcript-20260102T030405Z-2.txt": "second screen\n",
        "operator_transcript-20260102T030405Z-3.txt": "third screen\n",
    }


def test_the_transcript_is_captured_before_the_session_is_killed(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    operator_session.end(workspaces_root=ws_root)
    subcommands = fake_tmux.subcommands()
    assert subcommands.index("capture-pane") < subcommands.index("kill-session")


def test_an_exited_claude_can_still_be_ended_with_its_final_screen_archived(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    fake_tmux.pane_dead = True
    fake_tmux.capture = "final screen\n"
    assert operator_session.end(workspaces_root=ws_root) is True
    assert _transcripts(ws_root)[0].read_text() == "final screen\n"


def test_a_transcript_that_cannot_be_captured_does_not_stop_the_end(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    fake_tmux.capture_rc = 1
    assert operator_session.end(workspaces_root=ws_root) is True
    assert _transcripts(ws_root) == []
    assert fake_tmux.live is False


def test_a_tmux_that_cannot_run_capture_does_not_stop_the_end(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    fake_tmux.raises["capture-pane"] = subprocess.TimeoutExpired(["tmux"], 15)
    assert operator_session.end(workspaces_root=ws_root) is True
    assert _transcripts(ws_root) == []


def test_a_workspace_deleted_mid_session_does_not_stop_the_end(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    (ws_root / workspace.task_id / "tasks" / workspace.task_id / "meta.json").unlink()
    assert operator_session.end(workspaces_root=ws_root) is True
    assert _transcripts(ws_root) == []
    assert fake_tmux.live is False


def test_a_transcript_that_cannot_be_written_does_not_stop_the_end(
    fake_tmux: FakeTmux,
    ws_root: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    monkeypatch.setattr(operator_session, "write_text_mkstemp", refuse)
    assert operator_session.end(workspaces_root=ws_root) is True
    assert fake_tmux.live is False


def test_a_session_that_survives_kill_session_is_an_end_failure_and_keeps_its_record(
    fake_tmux: FakeTmux,
) -> None:
    operator_session.start(Scope.FLEET, None)
    fake_tmux.kill_leaves_session = True
    with pytest.raises(OperatorEndFailed, match="could not be confirmed stopped"):
        operator_session.end()
    assert operator_session.meta_path().exists()
    assert operator_session.current() is not None


def test_a_kill_that_cannot_reach_tmux_leaves_the_session_and_its_record(
    fake_tmux: FakeTmux,
) -> None:
    operator_session.start(Scope.FLEET, None)
    fake_tmux.raises["kill-session"] = subprocess.TimeoutExpired(["tmux"], 15)
    with pytest.raises(OperatorEndFailed):
        operator_session.end()
    assert operator_session.meta_path().exists()
    assert fake_tmux.live is True


def test_an_unconfirmable_stop_keeps_the_record_when_tmux_stops_answering(
    fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)

    def hang_after_kill() -> bool:
        fake_tmux.raises["has-session"] = subprocess.TimeoutExpired(["tmux"], 15)
        return False

    monkeypatch.setattr(operator_tmux, "kill_session", hang_after_kill)
    with pytest.raises(OperatorEndFailed, match="could not be confirmed stopped"):
        operator_session.end()
    assert operator_session.meta_path().exists()


def test_a_kill_that_fails_because_the_session_already_exited_still_ends(
    fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_session.start(Scope.FLEET, None)

    def already_gone() -> bool:
        fake_tmux.live = False
        return False

    monkeypatch.setattr(operator_tmux, "kill_session", already_gone)
    assert operator_session.end() is True
    assert not operator_session.meta_path().exists()


def test_replacing_a_workspace_session_archives_the_old_transcript_first(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    fake_tmux.capture = "old session screen\n"
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    info = operator_session.start(Scope.FLEET, None, replace=True, workspaces_root=ws_root)
    assert info.scope is Scope.FLEET
    archived = _transcripts(ws_root)
    assert [path.read_text() for path in archived] == ["old session screen\n"]
    subcommands = fake_tmux.subcommands()
    first_new = subcommands.index("new-session")
    second_new = subcommands.index("new-session", first_new + 1)
    assert subcommands.index("capture-pane") < subcommands.index("kill-session") < second_new


def test_a_replace_that_fails_after_the_old_session_ended_leaves_no_session_but_a_transcript(
    fake_tmux: FakeTmux, fake_git: FakeGit, ws_root: Path, workspace: Workspace
) -> None:
    fake_tmux.capture = "old session screen\n"
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    fake_git.fail("config", "remote.origin.url", git_reply(stderr="offline"))
    with pytest.raises(OperatorUnavailable):
        operator_session.start(Scope.FLEET, None, replace=True, workspaces_root=ws_root)
    assert fake_tmux.live is False
    assert operator_session.current() is None
    assert not operator_session.meta_path().exists()
    assert [path.read_text() for path in _transcripts(ws_root)] == ["old session screen\n"]


def test_replacing_a_fleet_session_kills_the_old_one_before_starting_the_new(
    fake_tmux: FakeTmux,
) -> None:
    operator_session.start(Scope.FLEET, None)
    operator_session.start(Scope.REPO, "o/r", replace=True)
    subcommands = fake_tmux.subcommands()
    assert fake_tmux.calls_of("capture-pane") == []
    kill_at = subcommands.index("kill-session")
    assert subcommands.index("new-session") < kill_at < len(subcommands) - 1
    assert subcommands.count("new-session") == 2
    current = operator_session.current()
    assert current is not None
    assert current.scope is Scope.REPO
    assert current.target == "o/r"

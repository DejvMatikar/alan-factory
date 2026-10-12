from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import operator_tmux, roots
from alan.operator_tmux import run_tmux as real_run_tmux
from tests.conftest import FakeTmux, make_fake_run, spawns_real_tmux


def test_socket_and_conf_live_in_a_private_directory_under_the_state_root() -> None:
    assert operator_tmux.state_dir() == roots.state_root() / "operator"
    assert operator_tmux.socket_path() == roots.state_root() / "operator" / "tmux.sock"
    assert operator_tmux.conf_path() == roots.state_root() / "operator" / "tmux.conf"


def test_session_env_is_the_allow_list_plus_term(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("ALAN_CONTROL_TOKEN", "control-secret")
    monkeypatch.setenv("SERVICE_ONLY_VARIABLE", "service-value")
    env = operator_tmux.session_env()
    assert env["TERM"] == "xterm-256color"
    assert set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "SHELL"}
    assert "control-secret" not in env.values()
    assert "service-value" not in env.values()


def test_session_env_matches_the_driver_loop_allow_list_apart_from_term() -> None:
    from alan.driver_loop_cmd import build_env

    without_term = {k: v for k, v in operator_tmux.session_env().items() if k != "TERM"}
    assert without_term == {k: v for k, v in build_env().items() if k != "TERM"}


def test_real_runner_execs_tmux_with_the_session_env_and_no_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("SERVICE_ONLY_VARIABLE", "service-value")
    result = real_run_tmux(["-V"], cwd="/somewhere", timeout=3.0)
    assert result.stdout == "out"
    assert captured["cmd"] == ["tmux", "-V"]
    assert captured["env"] == operator_tmux.session_env()
    assert captured["cwd"] == "/somewhere"
    assert captured["timeout"] == 3.0
    assert captured["stdin"] == subprocess.DEVNULL


def test_the_session_wide_guard_refuses_a_real_tmux_spawn() -> None:
    with pytest.raises(RuntimeError, match="real 'tmux'"):
        real_run_tmux(["-V"])


def test_the_autouse_guard_refuses_the_module_level_runner() -> None:
    with pytest.raises(RuntimeError, match="real 'tmux'"):
        operator_tmux.run_tmux(["-V"])


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        (["tmux", "-V"], True),
        ("tmux", True),
        (b"/usr/bin/tmux", True),
        (["/opt/homebrew/bin/tmux"], True),
        (["tmuxinator"], False),
        (["git", "tmux"], False),
        ([], False),
        ([1], False),
        (None, False),
    ],
)
def test_spawns_real_tmux_matches_on_the_executable_basename(cmd: object, expected: bool) -> None:
    assert spawns_real_tmux(cmd) is expected


def test_spawns_real_tmux_is_false_for_an_undecodable_path() -> None:
    assert spawns_real_tmux(["\udcff"]) is False


def test_prepare_state_dir_writes_the_constant_conf_privately(tmp_path: Path) -> None:
    operator_tmux.prepare_state_dir()
    directory = operator_tmux.state_dir()
    conf = operator_tmux.conf_path()
    assert conf.read_text() == operator_tmux.TMUX_CONF
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600


def test_prepare_state_dir_is_idempotent_and_restores_a_damaged_conf() -> None:
    operator_tmux.prepare_state_dir()
    operator_tmux.conf_path().write_text("tampered")
    operator_tmux.prepare_state_dir()
    assert operator_tmux.conf_path().read_text() == operator_tmux.TMUX_CONF


def test_conf_frees_every_key_for_claude_code_and_keeps_scrollback() -> None:
    lines = operator_tmux.TMUX_CONF.splitlines()
    assert "set -g prefix None" in lines
    assert "unbind-key C-b" in lines
    assert "set -g remain-on-exit on" in lines
    assert "set -g status off" in lines
    assert "set -g window-size latest" in lines
    assert operator_tmux.TMUX_CONF.endswith("\n")


def test_attach_argv_detaches_other_clients_on_the_private_socket() -> None:
    argv = operator_tmux.attach_argv()
    assert argv[0] == "tmux"
    assert argv[1:5] == [
        "-f",
        str(operator_tmux.conf_path()),
        "-S",
        str(operator_tmux.socket_path()),
    ]
    assert argv[5:] == ["attach-session", "-d", "-t", "=alan-operator"]


def test_has_session_reflects_the_return_code(fake_tmux: FakeTmux) -> None:
    assert operator_tmux.has_session() is False
    fake_tmux.live = True
    assert operator_tmux.has_session() is True
    assert fake_tmux.calls[-1][4:] == ["has-session", "-t", "=alan-operator"]


@pytest.mark.parametrize("error", [OSError("no tmux"), subprocess.TimeoutExpired(["tmux"], 1)])
def test_an_unrunnable_or_hung_tmux_reads_as_no_session(
    fake_tmux: FakeTmux, error: BaseException
) -> None:
    fake_tmux.live = True
    fake_tmux.raises["has-session"] = error
    assert operator_tmux.has_session() is False


@pytest.mark.parametrize(("live", "expected"), [(True, True), (False, False)])
def test_probe_session_reports_what_tmux_answered(
    fake_tmux: FakeTmux, live: bool, expected: bool
) -> None:
    fake_tmux.live = live
    assert operator_tmux.probe_session() is expected


@pytest.mark.parametrize("error", [OSError("no tmux"), subprocess.TimeoutExpired(["tmux"], 1)])
def test_probe_session_is_unknown_when_tmux_cannot_answer(
    fake_tmux: FakeTmux, error: BaseException
) -> None:
    fake_tmux.live = False
    fake_tmux.raises["has-session"] = error
    assert operator_tmux.probe_session() is None


@pytest.mark.parametrize(("dead", "expected"), [(True, True), (False, False)])
def test_pane_exited_reads_the_dead_flag(
    fake_tmux: FakeTmux, dead: bool, expected: bool
) -> None:
    fake_tmux.pane_dead = dead
    assert operator_tmux.pane_exited() is expected
    assert fake_tmux.calls[-1][4:] == ["list-panes", "-t", "=alan-operator:", "-F", "#{pane_dead}"]


def test_pane_exited_is_false_when_tmux_fails(fake_tmux: FakeTmux) -> None:
    fake_tmux.raises["list-panes"] = OSError("gone")
    assert operator_tmux.pane_exited() is False


def test_capture_pane_returns_the_full_scrollback_text(fake_tmux: FakeTmux) -> None:
    fake_tmux.capture = "line one\nline two\n"
    assert operator_tmux.capture_pane() == "line one\nline two\n"
    assert fake_tmux.calls[-1][4:] == [
        "capture-pane",
        "-p",
        "-J",
        "-S",
        "-",
        "-t",
        "=alan-operator:",
    ]


def test_capture_pane_is_none_on_a_tmux_error(fake_tmux: FakeTmux) -> None:
    fake_tmux.capture_rc = 1
    assert operator_tmux.capture_pane() is None


def test_capture_pane_is_none_when_tmux_cannot_run(fake_tmux: FakeTmux) -> None:
    fake_tmux.raises["capture-pane"] = subprocess.TimeoutExpired(["tmux"], 1)
    assert operator_tmux.capture_pane() is None


def test_kill_session_reports_success(fake_tmux: FakeTmux) -> None:
    fake_tmux.live = True
    assert operator_tmux.kill_session() is True
    assert fake_tmux.live is False
    assert fake_tmux.calls[-1][4:] == ["kill-session", "-t", "=alan-operator"]


def test_kill_session_reports_failure_when_tmux_cannot_run(fake_tmux: FakeTmux) -> None:
    fake_tmux.raises["kill-session"] = OSError("gone")
    assert operator_tmux.kill_session() is False


def test_new_session_starts_a_detached_named_session_in_the_checkout(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    prompt = "a prompt with 'quotes' and $vars"
    result = operator_tmux.new_session(["claude", prompt], cwd=tmp_path)
    assert result.returncode == 0
    call = fake_tmux.calls[-1]
    server_args = ["-f", str(operator_tmux.conf_path()), "-S", str(operator_tmux.socket_path())]
    assert call[:4] == server_args
    assert call[4:8] == ["new-session", "-d", "-s", "alan-operator"]
    assert call[8:12] == ["-c", str(tmp_path), "-x", "200"]
    assert call[12:14] == ["-y", "50"]
    assert shlex.split(call[14]) == ["claude", prompt]
    assert len(call) == 15
    assert operator_tmux.conf_path().read_text() == operator_tmux.TMUX_CONF


def test_new_session_lets_a_spawn_error_reach_the_caller(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    fake_tmux.raises["new-session"] = OSError("no tmux")
    with pytest.raises(OSError, match="no tmux"):
        operator_tmux.new_session(["claude"], cwd=tmp_path)


def test_make_fake_run_is_still_available_to_other_tests() -> None:
    assert make_fake_run(returncode=3)().returncode == 3

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from alan import operator_checkout
from alan.operator_checkout import (
    CheckoutCloneFailed,
    clone,
    fast_forward,
    install_origin_url,
    legacy_checkout_path,
    redact,
)
from alan.operator_checkout import install_root as real_install_root
from alan.operator_checkout import run_git as real_run_git

_ORIGIN = "https://example.test/some-owner/some-repo.git"


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Git:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, float]] = []
        self.replies: dict[str, _Proc | BaseException] = {}

    def verbs(self) -> list[str]:
        return [args[0] for args, _cwd, _timeout in self.calls]

    def __call__(self, args: Sequence[str], *, cwd: Path, timeout: float) -> _Proc:
        self.calls.append((list(args), cwd, timeout))
        reply = self.replies.get(args[0], _Proc())
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Git:
    fake = _Git()
    fake.replies["config"] = _Proc(stdout=f"{_ORIGIN}\n")
    monkeypatch.setattr(operator_checkout, "run_git", fake)
    monkeypatch.setattr(operator_checkout, "install_root", lambda: tmp_path / "myinstall")
    return fake


def test_default_install_root_is_the_directory_holding_the_alan_package() -> None:
    assert (real_install_root() / "alan" / "operator_checkout.py").is_file()


def test_the_legacy_checkout_sits_beside_the_install_named_after_it(
    git: _Git, tmp_path: Path
) -> None:
    del git
    assert legacy_checkout_path() == tmp_path / "myinstall-operator"


def test_the_installs_origin_is_read_from_its_own_directory(git: _Git, tmp_path: Path) -> None:
    assert install_origin_url() == _ORIGIN
    assert git.calls[0][0] == ["config", "--get", "remote.origin.url"]
    assert git.calls[0][1] == tmp_path / "myinstall"
    assert git.verbs() == ["config"]


@pytest.mark.parametrize(
    "reply",
    [_Proc(returncode=1, stderr="no such key"), _Proc(stdout="\n"), OSError("no git")],
    ids=["config_fails", "config_empty", "git_unrunnable"],
)
def test_no_origin_to_clone_from_is_a_clone_failure(git: _Git, reply: _Proc | OSError) -> None:
    git.replies["config"] = reply
    with pytest.raises(CheckoutCloneFailed, match="no origin remote"):
        install_origin_url()
    assert git.verbs() == ["config"]


def test_a_clone_runs_in_the_target_parent_with_the_url_and_path_as_given(
    git: _Git, tmp_path: Path
) -> None:
    clone(_ORIGIN, tmp_path / "owner" / "repo")
    args, cwd, _ = git.calls[0]
    assert args == ["clone", "--", _ORIGIN, str(tmp_path / "owner" / "repo")]
    assert cwd == tmp_path / "owner"
    assert git.verbs() == ["clone"]


def test_a_clone_has_a_longer_budget_than_an_update(git: _Git, tmp_path: Path) -> None:
    clone(_ORIGIN, tmp_path / "owner" / "repo")
    fast_forward(tmp_path / "owner" / "repo")
    clone_timeout = git.calls[0][2]
    update_timeout = git.calls[-1][2]
    assert clone_timeout == operator_checkout.CLONE_TIMEOUT_S
    assert update_timeout == operator_checkout.UPDATE_TIMEOUT_S
    assert clone_timeout > update_timeout


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:s3cret@example.test/o/r.git",
        "https://token-only@example.test/o/r.git",
        "http://token-only@example.test/o/r.git",
        "ssh://user:s3cret@example.test/o/r.git",
        "https://tok@[bad/o/r.git",
    ],
    ids=["https_password", "https_token", "http_token", "ssh_password", "malformed"],
)
def test_an_origin_embedding_credentials_is_refused_and_never_reaches_git_argv(
    git: _Git, tmp_path: Path, origin: str
) -> None:
    with pytest.raises(CheckoutCloneFailed, match="embeds credentials") as excinfo:
        clone(origin, tmp_path / "owner" / "repo")
    assert "s3cret" not in str(excinfo.value)
    assert "token-only" not in str(excinfo.value)
    assert git.calls == []


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/o/r.git",
        "ssh://git@example.test/o/r.git",
        "git@example.test:o/r.git",
        "/srv/git/some-repo.git",
    ],
    ids=["https", "ssh_user", "scp_style", "local_path"],
)
def test_an_origin_without_a_secret_is_cloned_as_given(
    git: _Git, tmp_path: Path, origin: str
) -> None:
    clone(origin, tmp_path / "owner" / "repo")
    assert git.calls[0][0][:3] == ["clone", "--", origin]


def test_a_failed_clone_is_a_clone_failure_with_redacted_output(
    git: _Git, tmp_path: Path
) -> None:
    git.replies["clone"] = _Proc(
        returncode=128, stderr="fatal: unable to access 'https://user:s3cret@example.test/x.git/'"
    )
    with pytest.raises(CheckoutCloneFailed) as excinfo:
        clone(_ORIGIN, tmp_path / "owner" / "repo")
    assert "s3cret" not in str(excinfo.value)
    assert "clone failed" in str(excinfo.value)


def test_a_clone_failure_falls_back_to_stdout_when_stderr_is_empty(
    git: _Git, tmp_path: Path
) -> None:
    git.replies["clone"] = _Proc(returncode=1, stdout="out only")
    with pytest.raises(CheckoutCloneFailed, match="out only"):
        clone(_ORIGIN, tmp_path / "owner" / "repo")


def test_a_timed_out_clone_is_a_clone_failure(git: _Git, tmp_path: Path) -> None:
    git.replies["clone"] = subprocess.TimeoutExpired(["git", "clone"], 1)
    with pytest.raises(CheckoutCloneFailed, match="timed out"):
        clone(_ORIGIN, tmp_path / "owner" / "repo")


def test_an_unrunnable_git_during_clone_is_a_clone_failure(git: _Git, tmp_path: Path) -> None:
    git.replies["clone"] = FileNotFoundError(2, "No such file or directory")
    with pytest.raises(CheckoutCloneFailed, match="No such file or directory"):
        clone(_ORIGIN, tmp_path / "owner" / "repo")


def test_an_oserror_without_text_still_names_its_type(git: _Git, tmp_path: Path) -> None:
    git.replies["clone"] = OSError()
    with pytest.raises(CheckoutCloneFailed, match="OSError"):
        clone(_ORIGIN, tmp_path / "owner" / "repo")


def test_an_existing_checkout_is_fetched_then_fast_forwarded(git: _Git, tmp_path: Path) -> None:
    assert fast_forward(tmp_path) is None
    assert git.verbs() == ["fetch", "merge"]
    assert git.calls[0][0] == ["fetch", "--quiet"]
    assert git.calls[1][0] == ["merge", "--ff-only", "--quiet", "@{upstream}"]
    assert {cwd for _args, cwd, _t in git.calls} == {tmp_path}


def test_an_update_is_never_a_reset_or_a_clone(git: _Git, tmp_path: Path) -> None:
    fast_forward(tmp_path)
    assert set(git.verbs()) == {"fetch", "merge"}


def test_a_failed_fetch_warns_and_skips_the_merge(git: _Git, tmp_path: Path) -> None:
    git.replies["fetch"] = _Proc(
        returncode=1, stderr="fatal: could not read from https://tok:en@example.test/r.git"
    )
    warning = fast_forward(tmp_path)
    assert warning is not None
    assert "fetch failed" in warning
    assert "tok:en" not in warning
    assert git.verbs() == ["fetch"]


def test_a_failed_merge_warns_and_leaves_the_checkout_alone(git: _Git, tmp_path: Path) -> None:
    git.replies["merge"] = _Proc(returncode=128, stderr="fatal: Not possible to fast-forward")
    warning = fast_forward(tmp_path)
    assert warning is not None
    assert "merge failed" in warning
    assert "Not possible to fast-forward" in warning
    assert git.verbs() == ["fetch", "merge"]


def test_a_timed_out_update_warns(git: _Git, tmp_path: Path) -> None:
    git.replies["fetch"] = subprocess.TimeoutExpired(["git", "fetch"], 1)
    warning = fast_forward(tmp_path)
    assert warning is not None
    assert "timed out" in warning


def test_redact_hides_credentials_inside_urls() -> None:
    text = "fatal: https://user:pw@host.test/a.git and ssh://git@host.test/b.git"
    redacted = redact(text)
    assert "user:pw" not in redacted
    assert "https://***@host.test/a.git" in redacted


def test_redact_leaves_urls_without_credentials_alone() -> None:
    assert redact("see https://host.test/a.git") == "see https://host.test/a.git"


@pytest.mark.parametrize(
    "text",
    ["x://@host.test/a", "x://host.test", "x://host.test other@mail.test", "x:// user@host.test"],
)
def test_redact_leaves_text_without_userinfo_after_the_scheme_alone(text: str) -> None:
    assert redact(text) == text


def test_redact_hides_credentials_after_every_scheme_separator() -> None:
    assert redact("a://u:p@h.test b://k@h.test") == "a://***@h.test b://***@h.test"


def test_redact_flattens_control_characters_and_whitespace() -> None:
    assert redact("a\x1b[31m red\n\nb\t\tc\x00d") == "a [31m red b c d"


def test_redact_caps_the_length() -> None:
    assert len(redact("x" * 5000)) == 300


def test_real_runner_passes_only_the_allow_listed_environment_plus_term_and_no_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("ALAN_CONTROL_TOKEN", "control-secret")
    monkeypatch.setenv("SERVICE_ONLY_VARIABLE", "service-value")
    monkeypatch.setenv("GH_TOKEN", "forge-secret")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    real_run_git(["status"], cwd=tmp_path, timeout=5.0)
    env = captured["env"]
    assert captured["cmd"] == ["git", "status"]
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 5.0
    assert captured["stdin"] == subprocess.DEVNULL
    assert set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "SHELL"} | {
        "GIT_TERMINAL_PROMPT"
    }
    assert env["TERM"] == "xterm-256color"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "control-secret" not in env.values()
    assert "service-value" not in env.values()
    assert "forge-secret" not in env.values()


def test_the_autouse_guard_refuses_the_real_git_runner_in_tests(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="operator checkout's real git runner"):
        operator_checkout.run_git(["status"], cwd=tmp_path, timeout=1.0)

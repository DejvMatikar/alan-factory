from __future__ import annotations

import getpass
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._setup_stubs import HOMEBREW_INSTALLER_URL
from tests._setup_support import (
    Call,
    Sandbox,
    Scenario,
    SetupRun,
    brew_formulae,
    leaks,
    make_sandbox,
    mutating_calls,
    run_setup,
)

FORMULAE = brew_formulae()
LOCKED = pytest.mark.skipif(os.geteuid() == 0, reason="root can always write the prefix")


@pytest.fixture
def restore_prefix_mode(tmp_path: Path) -> Iterator[None]:
    yield
    prefix = tmp_path / "homebrew"
    if prefix.exists():
        prefix.chmod(0o755)


def _lock_prefix(sandbox: Sandbox, present: tuple[str, ...]) -> Path:
    prefix = sandbox.base / "homebrew"
    for formula in present:
        (prefix / "opt" / formula / "bin").mkdir(parents=True, exist_ok=True)
    prefix.mkdir(exist_ok=True)
    prefix.chmod(0o555)
    return prefix


def _missing(present: tuple[str, ...]) -> list[str]:
    return [formula for formula in FORMULAE if formula not in present]


def _refusal_line(run: SetupRun) -> str:
    lines = [line for line in run.stderr.splitlines() if "refused at" in line]
    assert len(lines) == 1, run.stderr
    return lines[0]


def _installer_calls(run: SetupRun) -> list[Call]:
    return [call for call in run.called("curl") if HOMEBREW_INSTALLER_URL in call.args]


def _refused_run(tmp_path: Path, present: tuple[str, ...], *args: str) -> tuple[Path, SetupRun]:
    sandbox = make_sandbox(tmp_path)
    prefix = _lock_prefix(sandbox, present)
    return prefix, run_setup(sandbox, *args, scenario=Scenario(brew_installed=present))


def _assert_nothing_installed(run: SetupRun) -> None:
    assert run.called("brew", "install") == []
    assert run.called("brew", "link") == []
    assert _installer_calls(run) == []


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_a_full_run_refuses_at_preflight_when_the_prefix_is_unwritable_and_formulae_are_missing(
    tmp_path: Path,
) -> None:
    present = ("git", "gh")
    prefix, run = _refused_run(tmp_path, present)
    assert run.returncode == 1, run.output
    assert "refused at preflight: homebrew:" in run.stderr
    assert run.headers() == ["preflight"]
    assert mutating_calls(run) == []
    _assert_nothing_installed(run)
    line = _refusal_line(run)
    assert line.endswith(": brew install " + " ".join(_missing(present)))
    assert "re-run setup" in line
    assert str(prefix) in line


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_the_refusal_lists_only_the_missing_formulae_in_setup_order(tmp_path: Path) -> None:
    present = ("gh", "pre-commit")
    _, run = _refused_run(tmp_path, present)
    missing = _missing(present)
    assert missing == ["git", "uv", "node@22", "gitleaks", "tmux"]
    assert run.returncode == 1, run.output
    line = _refusal_line(run)
    assert line.endswith(": brew install " + " ".join(missing))
    assert f"lacks: {' '.join(missing)}." in line


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
@pytest.mark.parametrize("flag", ["--from", "--only"])
def test_a_run_starting_at_install_refuses_at_install_with_the_same_command(
    tmp_path: Path, flag: str
) -> None:
    present = ("git",)
    _, run = _refused_run(tmp_path, present, flag, "install")
    assert run.returncode == 1, run.output
    assert "refused at install: homebrew:" in run.stderr
    assert run.headers() == ["install"]
    assert _refusal_line(run).endswith(": brew install " + " ".join(_missing(present)))
    _assert_nothing_installed(run)
    assert mutating_calls(run) == []


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_an_unwritable_prefix_with_every_formula_present_reports_already_and_installs_nothing(
    tmp_path: Path,
) -> None:
    _, run = _refused_run(tmp_path, tuple(FORMULAE), "--only", "install")
    assert run.returncode == 0, run.output
    assert "[already] Homebrew" in run.stdout
    for formula in FORMULAE:
        assert f"[already] brew formula {formula}" in run.stdout
    _assert_nothing_installed(run)


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_a_full_run_with_an_unwritable_prefix_and_every_formula_present_loads_the_environment_once(
    tmp_path: Path,
) -> None:
    _, run = _refused_run(tmp_path, tuple(FORMULAE))
    assert run.returncode == 0, run.output
    assert run.headers()[:2] == ["preflight", "install"]
    assert len(run.called("brew", "shellenv")) == 1
    assert run.called("brew", "install") == []


def _brew_sequence(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in run.called("brew")]


def test_a_writable_prefix_installs_every_formula_in_order_and_exits_zero(
    tmp_path: Path,
) -> None:
    writable = make_sandbox(tmp_path / "writable")
    (writable.base / "homebrew").mkdir(mode=0o755)
    absent = make_sandbox(tmp_path / "absent")
    first = run_setup(writable)
    second = run_setup(absent)
    assert first.returncode == 0, first.output
    assert second.returncode == 0, second.output
    assert first.headers() == second.headers()
    assert [call.args[1] for call in first.called("brew", "install")] == FORMULAE
    assert _brew_sequence(first) == _brew_sequence(second)
    assert _brew_sequence(first)[0] == ("--prefix",)
    assert _brew_sequence(first).index(("--prefix",)) < _brew_sequence(first).index(("shellenv",))


def test_no_homebrew_means_preflight_makes_no_brew_call_and_install_installs_homebrew(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "brew"}))
    run = run_setup(sandbox)
    assert run.returncode == 0, run.output
    installers = _installer_calls(run)
    assert len(installers) == 1
    assert "[done] Homebrew" in run.stdout
    first_brew = next(index for index, call in enumerate(run.calls) if call.tool == "brew")
    assert first_brew > run.calls.index(installers[0])


def test_a_prefix_that_does_not_exist_is_never_treated_as_unwritable(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    assert not (sandbox.base / "homebrew").exists()
    run = run_setup(sandbox, "--only", "preflight")
    assert run.returncode == 0, run.output
    assert _brew_sequence(run) == [("--prefix",)]
    assert run.called("brew", "list") == []


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_the_refusal_never_names_the_owning_account_and_passes_the_leak_patterns(
    tmp_path: Path,
) -> None:
    prefix, run = _refused_run(tmp_path, ("git",))
    assert run.returncode == 1, run.output
    assert leaks(run.output) == {}
    without_prefix = run.stderr.replace(str(prefix), "")
    assert re.search(rf"\b{re.escape(getpass.getuser())}\b", without_prefix) is None
    assert "/opt/homebrew" not in run.output


@LOCKED
@pytest.mark.usefixtures("restore_prefix_mode")
def test_a_single_missing_formula_gives_a_one_formula_command(tmp_path: Path) -> None:
    present = tuple(formula for formula in FORMULAE if formula != "tmux")
    _, run = _refused_run(tmp_path, present)
    assert run.returncode == 1, run.output
    assert _refusal_line(run).endswith(": brew install tmux")

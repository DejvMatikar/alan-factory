from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path

import pytest

from alan import setup_claude_check as scc
from tests._setup_ci_stubs import CLAUDE_LABEL, PLANTED_EMAIL, PLANTED_KEY
from tests._setup_support import (
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    leaks,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    seed_bound_service,
)

UID = os.getuid()
TARGET = f"gui/{UID}/{CLAUDE_LABEL}"
DOMAIN = f"gui/{UID}"
RESUME = "Resume with: scripts/setup_macos.sh --check-claude"
LABELS = frozenset(scc.METHOD_LABELS.values())


def _sandbox(tmp_path: Path) -> Sandbox:
    return make_sandbox(tmp_path, absent=frozenset(), ci_steps=True, finish_steps=True)


def _check(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    seed_bound_service(sandbox)
    return run_setup(sandbox, "--check-claude", scenario=scenario)


def _launchctl(run: SetupRun, verb: str) -> list[tuple[str, ...]]:
    return [call.args for call in run.called("launchctl", verb)]


def _assert_clean(sandbox: Sandbox, run: SetupRun) -> None:
    assert list(sandbox.tmp_dir.iterdir()) == []
    assert sorted(path.name for path in sandbox.launch_agents_dir.glob("*claude-check*")) == []
    assert not (sandbox.stub_root / "state" / "loaded" / f"{CLAUDE_LABEL}.json").exists()
    assert PLANTED_EMAIL not in run.output
    assert PLANTED_KEY not in run.output
    assert leaks(run.output) == {}
    assert [call for call in run.calls if PLANTED_EMAIL in " ".join(call.args)] == []


def _log(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "claude-check.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_check_alone_loads_one_transient_agent_into_the_gui_domain_and_boots_it_out(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox)
    assert run.returncode == 0, run.output
    assert len(_launchctl(run, "bootstrap")) == 1
    assert _launchctl(run, "bootstrap")[0][1] == DOMAIN
    assert _launchctl(run, "bootstrap")[0][2].endswith(f"{CLAUDE_LABEL}.plist")
    assert _launchctl(run, "bootout") == [("bootout", TARGET)]
    assert "Claude check: pass (method: Claude account)" in run.stdout
    _assert_clean(sandbox, run)


def test_the_check_alone_runs_no_other_step_and_makes_no_github_or_api_call(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox)
    assert run.headers() == ["claude"]
    assert run.called("gh") == []
    assert run.called("curl") == []
    assert {(call.tool, call.args[0]) for call in mutating_calls(run)} == {
        ("launchctl", "bootstrap"),
        ("launchctl", "bootout"),
    }
    assert not sandbox.state_file.read_text(encoding="utf-8").count("claude")


def test_the_agent_runs_the_claude_status_command_and_nothing_else(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox)
    claude_calls = [call.args for call in run.calls if call.tool == "claude"]
    assert claude_calls == [("auth", "status")]


def test_the_agents_launch_file_and_log_live_in_the_per_run_directory(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    run = run_setup(sandbox, "--check-claude")
    plist = Path(_launchctl(run, "bootstrap")[0][2])
    assert plist.parent.parent == sandbox.tmp_dir
    assert plist.parent.name.startswith("setup-bind.")
    assert not plist.exists()
    assert not plist.parent.exists()


@pytest.mark.parametrize(
    ("method", "shown"),
    [
        ("claude.ai", "method: Claude account"),
        ("console", "method: Anthropic Console"),
        ("bedrock", "method: Amazon Bedrock"),
        ("something-new", "method not reported"),
        (None, "method not reported"),
    ],
)
def test_the_output_shows_only_pass_or_fail_and_a_method_label_from_a_fixed_set(
    tmp_path: Path, method: str | None, shown: str
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(claude_method=method))
    assert run.returncode == 0, run.output
    assert f"Claude check: pass ({shown})" in run.stdout
    _assert_clean(sandbox, run)


def test_output_that_is_not_json_still_passes_on_a_zero_exit_with_no_method(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(claude_status="no-json"))
    assert run.returncode == 0, run.output
    assert "Claude check: pass (method not reported)" in run.stdout
    _assert_clean(sandbox, run)


@pytest.mark.parametrize("status", ["logged-out", "crashes"])
def test_a_claude_that_reports_failure_fails_the_check_and_still_cleans_up(
    tmp_path: Path, status: str
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(claude_status=status))
    assert run.returncode == 1
    assert "[failed] Claude check: fail (" in run.stdout
    assert "sign in yourself by running claude once in a terminal" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME
    assert "setup: incomplete: claude did not complete" in run.stderr
    assert _launchctl(run, "bootout") == [("bootout", TARGET)]
    _assert_clean(sandbox, run)


def test_a_logged_out_claude_still_shows_only_a_fixed_label(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(claude_status="logged-out", claude_method=None))
    assert "Claude check: fail (method not reported)" in run.stdout


def test_a_check_that_never_finishes_times_out_fails_and_still_boots_the_agent_out(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(claude_status="hangs"))
    assert run.returncode == 1
    assert (
        f"[failed] Claude check: fail (method not reported); it did not finish within "
        f"{WAIT_BUDGET} checks"
    ) in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert _launchctl(run, "bootout") == [("bootout", TARGET)]
    _assert_clean(sandbox, run)


def test_a_launchctl_that_cannot_load_the_agent_fails_the_check_and_cleans_up(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _check(sandbox, Scenario(bootstrap_fails=True))
    assert run.returncode == 1
    assert f"[failed] launchctl could not load {CLAUDE_LABEL}" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME
    assert _launchctl(run, "bootout") == [("bootout", TARGET)]
    _assert_clean(sandbox, run)


def test_a_claude_that_is_not_installed_fails_the_check_without_loading_anything(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, ci_steps=True, finish_steps=True)
    run = _check(sandbox)
    assert run.returncode == 1
    assert "the Claude Code CLI is not on PATH" in run.stdout
    assert _launchctl(run, "bootstrap") == []
    assert _launchctl(run, "bootout") == []
    assert run.stderr.strip().splitlines()[-1] == RESUME


def test_a_leftover_agent_and_launch_file_are_swept_before_the_check_loads_its_own(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    preload_agent(sandbox, CLAUDE_LABEL)
    sandbox.launch_agents_dir.mkdir(parents=True)
    stray = sandbox.launch_agents_dir / f"{CLAUDE_LABEL}.plist"
    stray.write_text("stale", encoding="utf-8")
    run = run_setup(sandbox, "--check-claude")
    assert run.returncode == 0, run.output
    calls = [call.args[0] for call in run.called("launchctl") if call.args[0] != "print"]
    assert calls == ["bootout", "bootstrap", "bootout"]
    assert not stray.exists()
    _assert_clean(sandbox, run)


def test_the_full_step_loads_one_transient_agent_boots_it_out_and_marks_the_step(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    run = run_setup(sandbox, "--only", "claude")
    assert run.returncode == 0, run.output
    assert len(_launchctl(run, "bootstrap")) == 1
    assert _launchctl(run, "bootout") == [("bootout", TARGET)]
    assert "claude" in sandbox.state_file.read_text(encoding="utf-8").splitlines()
    _assert_clean(sandbox, run)


def test_a_failed_full_step_is_not_marked_and_resumes_at_the_claude_step(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    run = run_setup(sandbox, "--only", "claude", scenario=Scenario(claude_status="logged-out"))
    assert run.returncode == 1
    assert "claude" not in sandbox.state_file.read_text(encoding="utf-8").splitlines()
    resume = "Resume with: scripts/setup_macos.sh --from claude"
    assert run.stderr.strip().splitlines()[-1] == resume
    _assert_clean(sandbox, run)


def test_a_re_run_loads_and_boots_out_a_fresh_agent_rather_than_trusting_the_mark(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    assert run_setup(sandbox, "--only", "claude").returncode == 0
    second = run_setup(sandbox, "--only", "claude")
    assert len(_launchctl(second, "bootstrap")) == 1
    assert len(_launchctl(second, "bootout")) == 1


@pytest.mark.parametrize(
    "flags",
    [
        ("--check-claude", "--from", "build"),
        ("--check-claude", "--only", "claude"),
        ("--check-claude", "--uninstall"),
        ("--uninstall", "--check-claude"),
    ],
)
def test_the_check_combined_with_from_only_or_uninstall_is_a_usage_error(
    tmp_path: Path, flags: tuple[str, ...]
) -> None:
    sandbox = _sandbox(tmp_path)
    run = run_setup(sandbox, *flags)
    assert run.returncode == 2
    assert "--check-claude cannot be combined with" in run.stderr
    assert run.calls == ()


def test_the_bootstrap_entry_refuses_the_flag_and_names_the_engine_command(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = run_setup(sandbox, "--check-claude", entry="setup.sh")
    assert run.returncode == 2
    assert "--check-claude is only accepted by scripts/setup_macos.sh" in run.stderr
    assert "scripts/setup_macos.sh --check-claude" in run.stderr
    assert run.calls == ()


def test_the_flag_is_accepted_beside_the_flags_that_only_change_other_steps(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    seed_bound_service(sandbox)
    run = run_setup(sandbox, "--check-claude", "--accept-sonar-user-token", "--use-gh-token")
    assert run.returncode == 0, run.output


def test_the_launch_file_names_the_label_the_wrapper_and_the_log(tmp_path: Path) -> None:
    log = tmp_path / "claude-check.log"
    manifest = plistlib.loads(scc.plist_text("/opt/claude", log).encode("utf-8"))
    assert manifest["Label"] == CLAUDE_LABEL
    assert manifest["RunAtLoad"] is True
    assert manifest["StandardOutPath"] == str(log)
    assert manifest["ProgramArguments"][0:2] == ["/bin/sh", "-c"]
    assert manifest["ProgramArguments"][-1] == "/opt/claude"
    assert "auth status" in manifest["ProgramArguments"][2]
    assert "KeepAlive" not in manifest


def test_a_relative_claude_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(scc.ClaudeCheckRefused, match="absolute path"):
        scc.plist_text("claude", tmp_path / "log")


def test_the_plist_command_writes_a_private_file_and_refuses_a_relative_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "check.plist"
    argv = ["plist", "--claude", "/opt/claude", "--plist", str(out), "--log", str(tmp_path / "l")]
    assert scc.main(argv) == 0
    assert out.stat().st_mode & 0o777 == 0o600
    assert plistlib.loads(out.read_bytes())["Label"] == CLAUDE_LABEL
    bad = ["plist", "--claude", "claude", "--plist", str(out), "--log", str(tmp_path / "l")]
    assert scc.main(bad) == 1
    assert "absolute path" in capsys.readouterr().err
    missing = ["plist", "--claude", "/c", "--plist", str(tmp_path / "no" / "p"), "--log", "/l"]
    assert scc.main(missing) == 1
    assert capsys.readouterr().err.strip() != ""


def test_a_log_that_is_not_there_yet_or_has_no_exit_line_is_unfinished(tmp_path: Path) -> None:
    assert scc.verdict(tmp_path / "absent.log") == (scc.EXIT_UNFINISHED, [])
    assert scc.verdict(_log(tmp_path, "")) == (scc.EXIT_UNFINISHED, [])
    assert scc.verdict(_log(tmp_path, "not a number\n{}")) == (scc.EXIT_UNFINISHED, [])


@pytest.mark.parametrize("method", sorted(scc.METHOD_LABELS))
def test_every_reported_method_maps_to_its_fixed_label(tmp_path: Path, method: str) -> None:
    body = json.dumps({"loggedIn": True, "authMethod": method.upper(), "email": PLANTED_EMAIL})
    code, lines = scc.verdict(_log(tmp_path, f"0\n{body}\n"))
    assert code == scc.EXIT_PASS
    assert lines == ["pass", f"method: {scc.METHOD_LABELS[method]}"]


def test_the_verdict_never_repeats_anything_the_claude_output_holds(tmp_path: Path) -> None:
    body = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": PLANTED_EMAIL})
    text = "\n".join(scc.verdict(_log(tmp_path, f"0\n{body}\n"))[1])
    assert PLANTED_EMAIL not in text
    assert PLANTED_KEY not in text
    assert text in {"pass\nmethod: Claude account"}


@pytest.mark.parametrize(
    ("log", "outcome"),
    [
        ('1\n{"loggedIn": false, "authMethod": "claude.ai"}\n', "fail"),
        ('0\n{"loggedIn": false}\n', "fail"),
        ("2\ncrashed\n", "fail"),
        ("0\nsigned in\n", "pass"),
        ("0\n[1, 2]\n", "pass"),
        ('0\n{"authMethod": 5}\n', "pass"),
    ],
)
def test_the_verdict_reads_the_exit_line_and_the_logged_in_flag(
    tmp_path: Path, log: str, outcome: str
) -> None:
    code, lines = scc.verdict(_log(tmp_path, log))
    assert lines[0] == outcome
    assert code == (scc.EXIT_PASS if outcome == "pass" else scc.EXIT_FAIL)
    assert lines[1] in {*(f"method: {label}" for label in LABELS), scc.NO_METHOD_LABEL}


def test_the_verdict_command_prints_only_its_two_fixed_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _log(tmp_path, '0\n{"authMethod": "claude.ai"}\n')
    assert scc.main(["verdict", "--log", str(log)]) == 0
    assert capsys.readouterr().out.splitlines() == ["pass", "method: Claude account"]
    assert scc.main(["verdict", "--log", str(tmp_path / "absent")]) == scc.EXIT_UNFINISHED
    assert capsys.readouterr().out == ""

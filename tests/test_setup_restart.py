from __future__ import annotations

import os
from pathlib import Path

from tests._setup_support import (
    BOT_LOGIN,
    BOT_SENTINEL,
    WAIT_BUDGET,
    Call,
    Sandbox,
    Scenario,
    SetupRun,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_running_service,
    seed_secrets_env,
    snapshot_files,
    stored_secrets,
)

UID = os.getuid()
TARGET = f"gui/{UID}/ai.alan"
STALE_SHA = "0" * 40
TYPED = BOT_SENTINEL + "\n"
LOGIN_LINE = f"ALAN_VERIFIER_LOGIN={BOT_LOGIN}\n"


def _ready(tmp_path: Path, secrets: str = "EXAMPLE_KEY=x\n") -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, secrets)
    return sandbox


def _verify(
    sandbox: Sandbox, scenario: Scenario | None = None, stdin: str | None = TYPED
) -> SetupRun:
    return run_setup(sandbox, "--only", "verifier", scenario=scenario, stdin=stdin)


def _restarts(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in run.called("launchctl", "kickstart")]


def _index(calls: tuple[Call, ...], tool: str, suffix: str, after: int = -1) -> int:
    return next(
        i
        for i, call in enumerate(calls)
        if i > after and call.tool == tool and call.args[-1].endswith(suffix)
    )


def test_a_changed_login_line_restarts_the_service_exactly_once(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox)
    assert run.returncode == 0, run.output
    assert _restarts(run) == [("kickstart", "-k", TARGET)]
    assert [call.args[0] for call in mutating_calls(run) if call.tool == "launchctl"] == [
        "kickstart"
    ]
    assert "[done] restarted ai.alan so it picks up the changed configuration" in run.stdout


def test_health_reaches_the_served_commit_before_the_preflight_is_re_run(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox)
    kick = next(i for i, call in enumerate(run.calls) if call.matches("launchctl", "kickstart"))
    health = _index(run.calls, "curl", "/api/health", kick)
    preflight = _index(run.calls, "curl", "/preflight", kick)
    assert kick < health < preflight
    assert "[ok] health ok, serving commit " + sandbox.head() in run.stdout
    assert "[ok] preflight re-run against the restarted service" in run.stdout


def test_the_preflight_is_not_run_before_the_restart(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox)
    kick = next(i for i, call in enumerate(run.calls) if call.matches("launchctl", "kickstart"))
    before = [
        call
        for call in run.calls[:kick]
        if call.tool == "curl" and call.args[-1].endswith("/preflight")
    ]
    assert before == []


def test_the_restart_marker_is_removed_once_the_service_has_been_reloaded(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _verify(sandbox).returncode == 0
    assert not sandbox.restart_marker.exists()


def test_a_rerun_with_nothing_changed_restarts_nothing_and_writes_no_secret(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _verify(sandbox).returncode == 0
    watched = snapshot_files(sandbox.secrets_file, sandbox.state_file)
    second = _verify(sandbox, stdin="another-value\n")
    assert second.returncode == 0, second.output
    assert _restarts(second) == []
    assert mutating_calls(second) == []
    assert second.called("sleep") == []
    assert snapshot_files(sandbox.secrets_file, sandbox.state_file) == watched
    assert len(stored_secrets(sandbox)) == 1
    assert not sandbox.restart_marker.exists()


def test_a_service_that_does_not_serve_the_commit_after_the_restart_is_refused(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(served_sha_after_restart=STALE_SHA))
    assert run.returncode == 1
    assert "refused at verifier: health:" in run.stderr
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert _restarts(run) == [("kickstart", "-k", TARGET)]
    assert [c for c in run.calls if c.tool == "curl" and c.args[-1].endswith("/preflight")] == []
    assert sandbox.restart_marker.exists()
    assert "verifier" not in sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_a_marker_left_by_an_interrupted_run_restarts_the_service_on_the_next_run(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    interrupted = _verify(sandbox, Scenario(secret_post_status=422))
    assert interrupted.returncode == 1
    assert sandbox.restart_marker.exists()
    assert _restarts(interrupted) == []
    resumed = _verify(sandbox)
    assert resumed.returncode == 0, resumed.output
    assert _restarts(resumed) == [("kickstart", "-k", TARGET)]
    assert not sandbox.restart_marker.exists()


def test_a_pending_marker_restarts_even_when_the_verifier_is_already_complete(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, "EXAMPLE_KEY=x\n" + LOGIN_LINE)
    sandbox.restart_marker.write_text("", encoding="utf-8")
    run = _verify(sandbox, Scenario(reviewer_stored=True), stdin=None)
    assert run.returncode == 0, run.output
    assert "[already] reviewer credential and verifier login" in run.stdout
    assert _restarts(run) == [("kickstart", "-k", TARGET)]
    assert not sandbox.restart_marker.exists()


def test_a_failed_check_creates_no_marker_and_restarts_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(bot_identity_kind="fine-grained"))
    assert run.returncode == 1
    assert not sandbox.restart_marker.exists()
    assert _restarts(run) == []


def test_the_login_line_is_written_before_the_reviewer_record_is_stored(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(secret_post_status=422))
    assert run.returncode == 1
    assert sandbox.secrets_file.read_text(encoding="utf-8") == "EXAMPLE_KEY=x\n" + LOGIN_LINE
    assert stored_secrets(sandbox) == []

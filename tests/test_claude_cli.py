from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli, control_plane
from alan.claude_cli import (
    BUILD_SUMMARIZE_SENTINEL,
    DISPATCH_PROPOSAL_SENTINEL,
    ChatTurnResult,
    ClaudeCliError,
    ClaudeCliTimeout,
    ServerErrorRetryable,
    TurnCeilingReached,
    UsageLimitError,
    _check_deadlines,
    _ftimeout_from_env,
    _kill_and_reap,
    format_build_system_prompt,
    format_explore_system_prompt,
    format_user_text,
    run_agent,
    run_chat_turn,
)
from alan.claude_cli_grants import GENERAL_EXEC_GRANTS, NO_TOOLS_BLOCK
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity, register

_DUMMY_SYSTEM_PROMPT = "dummy system prompt"


def test_run_agent_happy_path(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result="rewritten prompt"))
    assert run_agent("sys", "user") == "rewritten prompt"


def test_run_agent_strips_whitespace_from_result(patch_subprocess, claude_json):
    patch_subprocess(stdout="\n  " + claude_json(result="  trimmed  ") + "  \n")
    assert run_agent("sys", "user") == "trimmed"


def test_run_agent_file_not_found_wrapped(patch_subprocess):
    patch_subprocess(raises=FileNotFoundError("no claude binary"))
    with pytest.raises(ClaudeCliError, match="not found on PATH"):
        run_agent("sys", "user")


def test_run_agent_timeout_wrapped(patch_subprocess):
    patch_subprocess(raises=subprocess.TimeoutExpired(cmd="claude", timeout=120))
    with pytest.raises(ClaudeCliError, match="timed out after 120s"):
        run_agent("sys", "user")


def test_run_agent_nonzero_exit_with_stderr(patch_subprocess):
    patch_subprocess(returncode=2, stderr="permission denied")
    with pytest.raises(ClaudeCliError, match=r"exited 2: stderr=permission denied"):
        run_agent("sys", "user")


def test_run_agent_nonzero_exit_with_empty_stderr(patch_subprocess):
    patch_subprocess(returncode=1, stderr="")
    with pytest.raises(ClaudeCliError, match=r"exited 1: stderr=<empty>; stdout=<empty>"):
        run_agent("sys", "user")


def test_run_agent_nonzero_exit_with_none_stderr(patch_subprocess):
    patch_subprocess(returncode=1, stderr=None)
    with pytest.raises(ClaudeCliError, match=r"exited 1: stderr=<empty>; stdout=<empty>"):
        run_agent("sys", "user")


def test_run_agent_nonzero_exit_includes_stdout(patch_subprocess):
    patch_subprocess(returncode=1, stderr="", stdout='{"is_error":true,"result":"quota"}')
    with pytest.raises(ClaudeCliError, match=r'stdout=\{"is_error":true,"result":"quota"\}'):
        run_agent("sys", "user")


def test_run_agent_nonzero_exit_logs_stdout_and_stderr(patch_subprocess, caplog):
    patch_subprocess(returncode=1, stderr="boom", stdout="stdout payload")
    with caplog.at_level("WARNING", logger="alan.claude_cli"), pytest.raises(ClaudeCliError):
        run_agent("sys", "user")
    record = next(r for r in caplog.records if "claude CLI exited" in r.message)
    assert "stderr=boom" in record.message
    assert "stdout=stdout payload" in record.message


def test_run_agent_empty_stdout(patch_subprocess):
    patch_subprocess(stdout="   \n")
    with pytest.raises(ClaudeCliError, match="empty stdout"):
        run_agent("sys", "user")


def test_run_agent_empty_stdout_logs_stderr(patch_subprocess, caplog):
    patch_subprocess(stdout="", stderr="hint about why")
    with caplog.at_level("WARNING", logger="alan.claude_cli"), pytest.raises(ClaudeCliError):
        run_agent("sys", "user")
    assert any("stderr=hint about why" in r.message for r in caplog.records)


def test_run_agent_none_stdout(patch_subprocess):
    patch_subprocess(stdout=None)
    with pytest.raises(ClaudeCliError, match="empty stdout"):
        run_agent("sys", "user")


def test_run_agent_non_json_stdout(patch_subprocess):
    patch_subprocess(stdout="not json {")
    with pytest.raises(ClaudeCliError, match="returned non-JSON"):
        run_agent("sys", "user")


def test_run_agent_non_object_json_stdout(patch_subprocess):
    patch_subprocess(stdout='["array", "not", "object"]')
    with pytest.raises(ClaudeCliError, match="non-object JSON"):
        run_agent("sys", "user")


def test_run_agent_is_error_with_result(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result="rate limit reached", is_error=True))
    with pytest.raises(ClaudeCliError, match="rate limit reached"):
        run_agent("sys", "user")


def test_run_agent_usage_limit_raises_usage_limit_error(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result="You've hit your limit", is_error=True))
    with pytest.raises(UsageLimitError) as exc_info:
        run_agent("sys", "user")
    assert exc_info.value.raw_message == "You've hit your limit"
    assert exc_info.value.reset_at > 0


def test_run_agent_usage_limit_error_is_caught_as_claude_cli_error(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="usage limit exceeded", is_error=True))
    with pytest.raises(ClaudeCliError):
        run_agent("sys", "user")


def test_run_agent_is_error_falls_back_to_subtype(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(omit_result=True, is_error=True, subtype="error_quota"))
    with pytest.raises(ClaudeCliError, match="error_quota"):
        run_agent("sys", "user")


def test_run_agent_is_error_falls_back_to_unknown(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(omit_result=True, is_error=True))
    with pytest.raises(ClaudeCliError, match="unknown"):
        run_agent("sys", "user")


def test_run_agent_subtype_starts_with_error(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result="some text", subtype="error_max_turns"))
    with pytest.raises(ClaudeCliError, match="some text"):
        run_agent("sys", "user")


def test_run_agent_empty_result_field(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result=""))
    with pytest.raises(ClaudeCliError, match="result field was empty"):
        run_agent("sys", "user")


def test_run_agent_whitespace_only_result_field(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(result="   \n  "))
    with pytest.raises(ClaudeCliError, match="result field was empty"):
        run_agent("sys", "user")


def test_run_agent_missing_result_field(patch_subprocess, claude_json):
    patch_subprocess(stdout=claude_json(omit_result=True))
    with pytest.raises(ClaudeCliError, match="result field was empty"):
        run_agent("sys", "user")


def test_run_agent_cli_invocation_args(monkeypatch, claude_json):
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=claude_json(result="ok"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_agent("sys", "user")

    cmd = captured[0]
    assert cmd[0] == "claude"
    assert cmd[1] == "-p"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "sonnet"
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "3"
    assert "--permission-mode" in cmd and cmd[cmd.index("--permission-mode") + 1] == "auto"
    assert "--append-system-prompt" in cmd
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg.startswith("sys")
    assert claude_cli._HEADLESS_EXECUTION_BLOCK in prompt_arg
    assert "--system-prompt" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_worker_env_neutralizes_slot_pool_root_and_preserves_others(monkeypatch):
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", "/srv/alan-ci/slots")
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", "/srv/checkouts/alan")
    monkeypatch.setenv("ALAN_WORKER_ENV_MARKER", "kept")
    env = claude_cli._worker_env()
    assert env["ALAN_SLOT_POOL_ROOT"] == ""
    assert env["ALAN_WORKTREE_REPO_ROOT"] == ""
    assert env["ALAN_WORKER_ENV_MARKER"] == "kept"


def test_worker_env_strips_verifier_token_but_keeps_gh_token(monkeypatch):
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "ghp_bot")
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    monkeypatch.setenv("ALAN_WORKER_ENV_MARKER", "kept")
    env = claude_cli._worker_env()
    assert "ALAN_VERIFIER_GH_TOKEN" not in env
    assert env["GH_TOKEN"] == "ghp_ambient"  # noqa: S105
    assert env["ALAN_WORKER_ENV_MARKER"] == "kept"


def test_worker_env_strips_registered_code_quality_token(monkeypatch):
    monkeypatch.setenv("SONAR_TOKEN", "sonar-secret")
    monkeypatch.setenv("ALAN_WORKER_ENV_MARKER", "kept")
    env = claude_cli._worker_env()
    assert "SONAR_TOKEN" not in env
    assert env["ALAN_WORKER_ENV_MARKER"] == "kept"


def test_worker_env_strips_control_token(monkeypatch):
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, "top-secret-control-token")
    monkeypatch.setenv("ALAN_WORKER_ENV_MARKER", "kept")
    env = claude_cli._worker_env()
    assert control_plane.CONTROL_TOKEN_ENV not in env
    assert env["ALAN_WORKER_ENV_MARKER"] == "kept"


def test_worker_env_control_token_strip_is_distinct_from_secret_env_names(monkeypatch):
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    from alan.engine_config import registered_secret_env_names

    assert control_plane.CONTROL_TOKEN_ENV not in registered_secret_env_names()


def test_worker_env_strips_every_registered_engine_credential_token(monkeypatch):
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "ghp_bot")
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "ghp_acme")
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
            forge=ForgeConfig(
                repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
                credential_env_name="ACME_WIDGET_GH_TOKEN",
            ),
        )
    )

    env = claude_cli._worker_env()

    assert "ALAN_VERIFIER_GH_TOKEN" not in env
    assert "ACME_WIDGET_GH_TOKEN" not in env
    assert env["GH_TOKEN"] == "ghp_ambient"  # noqa: S105


def test_worker_env_sandboxes_state_roots_away_from_live(monkeypatch):
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", "/srv/alan-ci/workspaces")
    monkeypatch.setenv("ALAN_LOGS_ROOT", "/srv/alan-ci/logs")
    monkeypatch.setenv("ALAN_STATE_ROOT", "/srv/alan-ci/state")
    env = claude_cli._worker_env()
    sandbox = claude_cli._worker_sandbox_root()
    assert env["ALAN_WORKSPACES_ROOT"] == str(sandbox / "workspaces")
    assert env["ALAN_LOGS_ROOT"] == str(sandbox / "logs")
    assert env["ALAN_STATE_ROOT"] == str(sandbox / "state")
    assert str(sandbox).startswith(tempfile.gettempdir())
    assert sandbox.is_dir()
    assert (sandbox.stat().st_mode & 0o777) == 0o700
    assert os.environ["ALAN_WORKSPACES_ROOT"] == "/srv/alan-ci/workspaces"
    assert os.environ["ALAN_LOGS_ROOT"] == "/srv/alan-ci/logs"
    assert os.environ["ALAN_STATE_ROOT"] == "/srv/alan-ci/state"


def test_worker_env_sandboxes_chat_session_roots(monkeypatch):
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", "/srv/alan-ci/build-chat-sessions")
    monkeypatch.setenv(
        "ALAN_EXPLORE_CHAT_SESSIONS_ROOT", "/srv/alan-ci/explore-chat-sessions"
    )
    env = claude_cli._worker_env()
    sandbox = claude_cli._worker_sandbox_root()
    assert env["ALAN_BUILD_CHAT_SESSIONS_ROOT"] == str(sandbox / "build-chat-sessions")
    assert env["ALAN_EXPLORE_CHAT_SESSIONS_ROOT"] == str(sandbox / "explore-chat-sessions")


def test_worker_env_sandboxes_secrets_store_root(monkeypatch):
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", "/srv/alan-ci/secrets")
    env = claude_cli._worker_env()
    sandbox = claude_cli._worker_sandbox_root()
    assert env["ALAN_SECRETS_STORE_ROOT"] == str(sandbox / "secrets")


def test_worker_env_sets_subprocess_env_scrub():
    env = claude_cli._worker_env()
    assert env[claude_cli._SUBPROCESS_ENV_SCRUB_ENV] == claude_cli._SUBPROCESS_ENV_SCRUB_VALUE


def test_worker_env_preserves_load_bearing_credentials_and_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/srv/home")
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    for name in claude_cli._WORKER_SANDBOX_ROOT_ENVS:
        monkeypatch.setenv(name, f"/srv/alan-ci/{name.lower()}")

    env = claude_cli._worker_env()

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/srv/home"
    assert env["GH_TOKEN"] == "ghp_ambient"  # noqa: S105
    sandbox = claude_cli._worker_sandbox_root()
    for name, sub in claude_cli._WORKER_SANDBOX_ROOT_ENVS.items():
        assert env[name] == str(sandbox / sub)


def test_worker_env_no_cache_env_and_no_active_context_sets_nothing(monkeypatch):
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("npm_config_cache", raising=False)
    monkeypatch.delenv("NUGET_PACKAGES", raising=False)
    claude_cli.set_active_cache_env(None)
    env = claude_cli._worker_env()
    assert "UV_CACHE_DIR" not in env
    assert "npm_config_cache" not in env


def test_worker_env_explicit_cache_env_resolves_into_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    env = claude_cli._worker_env(cache_env={"NUGET_PACKAGES": "nuget"})
    assert env["NUGET_PACKAGES"] == str(tmp_path / "cache" / "nuget")


def test_worker_env_falls_back_to_active_cache_env_context(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    claude_cli.set_active_cache_env({"UV_CACHE_DIR": "uv"})
    try:
        env = claude_cli._worker_env()
        assert env["UV_CACHE_DIR"] == str(tmp_path / "cache" / "uv")
    finally:
        claude_cli.set_active_cache_env(None)


def test_worker_env_explicit_cache_env_overrides_active_context(monkeypatch, tmp_path):
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    claude_cli.set_active_cache_env({"UV_CACHE_DIR": "uv"})
    try:
        env = claude_cli._worker_env(cache_env={"NUGET_PACKAGES": "nuget"})
        assert "NUGET_PACKAGES" in env
        assert "UV_CACHE_DIR" not in env
    finally:
        claude_cli.set_active_cache_env(None)


def test_get_set_active_cache_env_round_trips():
    assert claude_cli.get_active_cache_env() is None
    claude_cli.set_active_cache_env({"UV_CACHE_DIR": "uv"})
    try:
        assert claude_cli.get_active_cache_env() == {"UV_CACHE_DIR": "uv"}
    finally:
        claude_cli.set_active_cache_env(None)


def test_worker_env_cache_env_cannot_clobber_sandboxed_root():
    env = claude_cli._worker_env(cache_env={"ALAN_STATE_ROOT": "evil"})
    sandbox = claude_cli._worker_sandbox_root()
    assert env["ALAN_STATE_ROOT"] == str(sandbox / "state")


def test_worker_env_cache_env_cannot_clobber_control_token(monkeypatch):
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, "top-secret-control-token")
    env = claude_cli._worker_env(cache_env={control_plane.CONTROL_TOKEN_ENV: "evil"})
    assert control_plane.CONTROL_TOKEN_ENV not in env


def test_invoke_cli_passes_ci_parity_slot_pool_env(monkeypatch, claude_json):
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", "/srv/alan-ci/slots")
    captured: list[dict[str, Any]] = []

    def fake_run(cmd, **kwargs):
        captured.append(kwargs)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=claude_json(result="ok"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_agent("sys", "user")

    assert captured[0]["env"]["ALAN_SLOT_POOL_ROOT"] == ""


@pytest.mark.parametrize(
    ("model", "expected"),
    [("opus", "xhigh"), ("sonnet", "high"), ("haiku", None)],
)
def test_effort_for_model_mapping(model, expected):
    assert claude_cli._effort_for_model(model) == expected


def test_resolve_effort_prefers_explicit_override():
    assert claude_cli._resolve_effort("xhigh", "sonnet") == "xhigh"
    assert claude_cli._resolve_effort(None, "sonnet") == "high"


def test_run_agent_opus_model_adds_effort_xhigh(monkeypatch, claude_json):
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=claude_json(result="ok"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_agent("sys", "user", model="opus")

    cmd = captured[0]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_chat_default_allowed_tools_includes_gh_pr_read():
    from alan.claude_cli import _DEFAULT_CHAT_ALLOWED_TOOLS

    assert "Bash(gh pr list:*)" in _DEFAULT_CHAT_ALLOWED_TOOLS
    assert "Bash(gh pr view:*)" in _DEFAULT_CHAT_ALLOWED_TOOLS


@pytest.mark.parametrize(
    "title,description,expected",
    [
        ("", "", "Title: (no title)\n\nDescription:\n(no description provided)"),
        ("  ", "  ", "Title: (no title)\n\nDescription:\n(no description provided)"),
        ("Build a thing", "Long story", "Title: Build a thing\n\nDescription:\nLong story"),
        ("  trim me  ", "  and me  ", "Title: trim me\n\nDescription:\nand me"),
        ("only title", "", "Title: only title\n\nDescription:\n(no description provided)"),
        ("", "only desc", "Title: (no title)\n\nDescription:\nonly desc"),
    ],
)
def test_format_user_text(title, description, expected):
    assert format_user_text(title, description) == expected


def test_run_agent_passes_custom_system_prompt(monkeypatch, claude_json):
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=claude_json(result="agent output"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_agent("custom system prompt", "user text") == "agent output"
    cmd = captured[0]
    idx = cmd.index("--append-system-prompt")
    prompt_arg = cmd[idx + 1]
    assert prompt_arg.startswith("custom system prompt")
    assert claude_cli._HEADLESS_EXECUTION_BLOCK in prompt_arg


def test_run_agent_propagates_cli_failure(patch_subprocess):
    patch_subprocess(returncode=1, stderr="boom")
    with pytest.raises(ClaudeCliError):
        run_agent("system", "user")


def test_timeout_env_var_overrides_default(patch_subprocess, monkeypatch):
    monkeypatch.setenv("ALAN_CLAUDE_TIMEOUT_SECONDS", "5")
    patch_subprocess(raises=subprocess.TimeoutExpired(cmd="claude", timeout=5))
    with pytest.raises(ClaudeCliError, match="timed out after 5s"):
        run_agent("sys", "user")


@pytest.mark.parametrize(
    "value",
    ["", "  ", "not-a-number", "0", "-1"],
)
def test_timeout_env_var_invalid_falls_back_to_default(
    patch_subprocess, monkeypatch, value
):
    monkeypatch.setenv("ALAN_CLAUDE_TIMEOUT_SECONDS", value)
    patch_subprocess(raises=subprocess.TimeoutExpired(cmd="claude", timeout=120))
    with pytest.raises(ClaudeCliError, match="timed out after 120s"):
        run_agent("sys", "user")


def test_timeout_env_var_unset_uses_default(patch_subprocess, monkeypatch):
    monkeypatch.delenv("ALAN_CLAUDE_TIMEOUT_SECONDS", raising=False)
    patch_subprocess(raises=subprocess.TimeoutExpired(cmd="claude", timeout=120))
    with pytest.raises(ClaudeCliError, match="timed out after 120s"):
        run_agent("sys", "user")


class _BlockingIter:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def __iter__(self) -> "_BlockingIter":
        return self

    def __next__(self) -> str:
        self._ev.wait()
        raise StopIteration

    def release(self) -> None:
        self._ev.set()

    def close(self) -> None:
        pass


class _FakePopen:
    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr: str = "",
        returncode: int = 0,
        stall_stdout: bool = False,
    ) -> None:
        self.stdin = io.StringIO()
        self._blocking: _BlockingIter | None = None
        if stall_stdout:
            self._blocking = _BlockingIter()
            self.stdout: Iterator[str] = self._blocking
        else:
            self.stdout = io.StringIO("".join(stdout_lines or []))
        self.stderr = io.StringIO(stderr)
        self._rc = returncode
        self.returncode: int | None = None
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        self.killed = True
        if self._blocking is not None:
            self._blocking.release()


def _result_event(text: str = "final", **extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "result": text, **extra}


def _lines(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout_lines: list[str] | None = None,
    stderr: str = "",
    returncode: int = 0,
    capture_cmds: list[list[str]] | None = None,
) -> None:
    def fake(cmd: list[str], **_kwargs: Any) -> _FakePopen:
        if capture_cmds is not None:
            capture_cmds.append(cmd)
        return _FakePopen(
            stdout_lines=stdout_lines,
            stderr=stderr,
            returncode=returncode,
        )

    monkeypatch.setattr(subprocess, "Popen", fake)


def test_run_agent_stream_path_none_keeps_json_one_shot_path(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="one-shot"))
    assert run_agent("sys", "user", stream_path=None) == "one-shot"


def test_run_agent_stream_path_routes_through_streaming_subprocess(
    monkeypatch, tmp_path
):
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("streamed result")),
        capture_cmds=cmds,
    )

    result = run_agent(
        "sys", "user", deny_all_tools=True, stream_path=tmp_path / "aux.jsonl"
    )

    assert result == "streamed result"
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--max-turns") + 1] == "3"
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"
    assert "--allowedTools" not in cmd


def test_run_agent_stream_path_writes_run_activity_to_file(monkeypatch, tmp_path):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("recorded")),
    )
    stream_path = tmp_path / "aux.jsonl"

    run_agent("sys", "user", stream_path=stream_path)

    written = stream_path.read_text(encoding="utf-8")
    assert '"recorded"' in written


def test_run_agent_stream_path_appends_across_repeated_runs(monkeypatch, tmp_path):
    stream_path = tmp_path / "aux.jsonl"
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("first")))
    run_agent("sys", "user", stream_path=stream_path)
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("second")))
    run_agent("sys", "user", stream_path=stream_path)

    written = stream_path.read_text(encoding="utf-8")
    assert '"first"' in written
    assert '"second"' in written


def test_run_agent_stream_path_passes_expected_kwargs_to_streaming(
    monkeypatch, tmp_path
):
    captured: dict[str, Any] = {}

    def fake_streaming(system_prompt, user_text, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_text"] = user_text
        captured.update(kwargs)
        return claude_cli.AgentStreamingResult(result_text="mocked")

    monkeypatch.setattr(claude_cli, "run_agent_streaming", fake_streaming)
    stream_path = tmp_path / "aux.jsonl"

    result = run_agent(
        "sys", "user", model="haiku", deny_all_tools=True, stream_path=stream_path
    )

    assert result == "mocked"
    assert captured["system_prompt"] == "sys"
    assert captured["user_text"] == "user"
    assert captured["model"] == "haiku"
    assert captured["deny_all_tools"] is True
    assert captured["max_turns"] == 3
    assert captured["stream_path"] == stream_path
    proc_config = captured["proc_config"]
    assert proc_config.timeout_seconds == claude_cli._cli_timeout_seconds()  # noqa: SLF001


_UUID = "12345678-1234-1234-1234-1234567890ab"


def test_run_chat_turn_first_uses_session_id_and_system_prompt(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": _UUID},
            _result_event("response"),
        ),
        capture_cmds=captured,
    )
    result = run_chat_turn(
        user_text="hello", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.result_text == "response"
    assert result.claude_session_id == _UUID
    cmd = captured[0]
    assert "--session-id" in cmd and cmd[cmd.index("--session-id") + 1] == _UUID
    assert "--resume" not in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == _DUMMY_SYSTEM_PROMPT
    assert "--allowedTools" in cmd
    from alan.claude_cli import _DEFAULT_CHAT_ALLOWED_TOOLS

    idx = cmd.index("--allowedTools")
    assert (
        cmd[idx + 1 : idx + 1 + len(_DEFAULT_CHAT_ALLOWED_TOOLS)]
        == _DEFAULT_CHAT_ALLOWED_TOOLS
    )
    assert cmd[idx + 1 + len(_DEFAULT_CHAT_ALLOWED_TOOLS)] == "--disallowedTools"
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "80"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "xhigh"


def test_run_chat_turn_resume_appends_system_prompt(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ack")),
        capture_cmds=captured,
    )
    result = run_chat_turn(
        user_text="again", session_id=_UUID, resume=True,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.result_text == "ack"
    cmd = captured[0]
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == _UUID
    assert "--session-id" not in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == _DUMMY_SYSTEM_PROMPT


def test_run_chat_turn_disallows_write_tools(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    cmd = captured[0]
    assert "--disallowedTools" in cmd
    deny_tokens = cmd[cmd.index("--disallowedTools") + 1].split()
    assert deny_tokens == ["Write", "Edit", "NotebookEdit"]


def test_build_cmd_allowed_tools_survive_as_discrete_argv_tokens() -> None:
    allowed_tools = ["Read", "Bash(git log:*)", "Bash(npm run:*)"]
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        allowed_tools=allowed_tools,
    )
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1 : idx + 4] == allowed_tools
    assert cmd[idx + 2] == "Bash(git log:*)"
    assert cmd[idx + 4] == "--disallowedTools"
    assert cmd[idx + 1 : idx + 1 + len(allowed_tools)] == allowed_tools


def test_build_cmd_allowed_tools_no_collapsed_form_regression() -> None:
    allowed_tools = ["Read", "Bash(git log:*)", "Bash(npm run:*)"]
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        allowed_tools=allowed_tools,
    )
    assert " ".join(allowed_tools) not in cmd
    idx = cmd.index("--allowedTools")
    for offset, entry in enumerate(allowed_tools):
        assert cmd[idx + 1 + offset] == entry


def test_build_cmd_allowed_tools_matches_base_tools_fixture() -> None:
    from alan.engine_config import _BASE_TOOLS

    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        allowed_tools=_BASE_TOOLS,
    )
    narrowed = [tool for tool in _BASE_TOOLS if tool not in GENERAL_EXEC_GRANTS]
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1 : idx + 1 + len(narrowed)] == narrowed
    assert cmd[idx + 1 + len(narrowed)] == "--disallowedTools"
    assert "Bash(uv run:*)" not in cmd


def test_build_cmd_deny_all_tools_skips_allowed_tools() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        allowed_tools=["Read"],
        deny_all_tools=True,
    )
    assert "--allowedTools" not in cmd
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"


def test_build_cmd_appends_headless_execution_block() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "the caller's system prompt",
        output_format="json",
        max_turns=1,
    )
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg.startswith("the caller's system prompt")
    assert claude_cli._HEADLESS_EXECUTION_BLOCK in prompt_arg
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_headless_execution_block_forbids_waiting_on_notifications() -> None:
    block = claude_cli._HEADLESS_EXECUTION_BLOCK
    assert "scheduler" in block
    assert "wakeup" in block
    assert "background" in block
    assert "synchronously" in block
    assert "no-op filler" in block


def test_headless_block_forbids_complying_with_background_execution_advice() -> None:
    assert "do not comply" in claude_cli._HEADLESS_EXECUTION_BLOCK
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys prompt", output_format="json", max_turns=1, deny_all_tools=True
    )
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert "do not comply" not in prompt_arg


def test_run_agent_streaming_includes_headless_execution_block(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    claude_cli.run_agent_streaming(_DUMMY_SYSTEM_PROMPT, "user text")
    cmd = captured[0]
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg.startswith(_DUMMY_SYSTEM_PROMPT)
    assert claude_cli._HEADLESS_EXECUTION_BLOCK in prompt_arg


def test_build_cmd_deny_all_tools_omits_headless_execution_block() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "the caller's system prompt",
        output_format="json",
        max_turns=1,
        deny_all_tools=True,
    )
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert NO_TOOLS_BLOCK in prompt_arg
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg


def test_build_cmd_deny_all_tools_omits_project_doc_block(tmp_path: Path) -> None:
    from alan.project_doc_context import set_active_project_doc_path

    doc = tmp_path / "project.md"
    doc.write_text("## Vision\n", encoding="utf-8")
    set_active_project_doc_path(str(doc))
    try:
        cmd = claude_cli._build_cmd(  # noqa: SLF001
            "sys prompt", output_format="json", max_turns=1, deny_all_tools=True
        )
        prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
        assert NO_TOOLS_BLOCK in prompt_arg
        assert f"@{doc}" not in prompt_arg
        assert "READ-ONLY" not in prompt_arg
    finally:
        set_active_project_doc_path(None)


def test_run_agent_streaming_deny_all_tools_omits_headless_execution_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    claude_cli.run_agent_streaming(_DUMMY_SYSTEM_PROMPT, "user text", deny_all_tools=True)
    cmd = captured[0]
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert NO_TOOLS_BLOCK in prompt_arg
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg


def test_run_agent_deny_all_tools_omits_headless_execution_block(monkeypatch, claude_json):
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=claude_json(result="ok"), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_agent("sys", "user", deny_all_tools=True)
    cmd = captured[0]
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert NO_TOOLS_BLOCK in prompt_arg
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg


def test_chat_cmd_excludes_headless_execution_block() -> None:
    cmd = claude_cli._chat_cmd(  # noqa: SLF001
        model="opus",
        session_id=_UUID,
        resume=False,
        max_turns=10,
        allowed_tools=["Read"],
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg == _DUMMY_SYSTEM_PROMPT
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg


def test_chat_cmd_allowed_tools_survive_intact_with_internal_spaces() -> None:
    allowed_tools = ["Bash(gh pr view:*)", "Bash(git log:*)"]
    cmd = claude_cli._chat_cmd(  # noqa: SLF001
        model="opus",
        session_id=_UUID,
        resume=False,
        max_turns=10,
        allowed_tools=allowed_tools,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1 : idx + 3] == allowed_tools
    assert cmd[idx + 1] == "Bash(gh pr view:*)"
    assert cmd[idx + 2] == "Bash(git log:*)"
    assert cmd[idx + 3] == "--disallowedTools"
    assert " ".join(allowed_tools) not in cmd


def test_run_chat_turn_excludes_headless_execution_block(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    cmd = captured[0]
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg == _DUMMY_SYSTEM_PROMPT
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg


def test_run_chat_turn_invokes_on_proc_started_with_live_popen(monkeypatch):
    captured_procs: list[subprocess.Popen[str]] = []
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    run_chat_turn(
        user_text="x",
        session_id=_UUID,
        resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
        on_proc_started=captured_procs.append,
    )
    assert len(captured_procs) == 1
    assert isinstance(captured_procs[0], _FakePopen)


def test_run_chat_turn_swallows_on_proc_started_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))

    def boom(_proc: subprocess.Popen[str]) -> None:
        raise RuntimeError("callback failed")

    caplog.set_level("ERROR", logger="alan.claude_cli")
    result = run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT, on_proc_started=boom,
    )
    assert result.result_text == "ok"
    assert any("on_proc_started callback raised" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "allowed_tools",
    [
        ["Read", "Grep"],
        ["Read", "Bash(git log:*)"],
    ],
)
def test_run_chat_turn_uses_custom_allowed_tools(monkeypatch, allowed_tools):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x",
        session_id=_UUID,
        resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
        allowed_tools=allowed_tools,
    )
    cmd = captured[0]
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1 : idx + 1 + len(allowed_tools)] == allowed_tools


def test_run_chat_turn_passes_custom_max_turns_and_model(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x",
        session_id=_UUID,
        resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
        max_turns=20,
        model="opus",
    )
    cmd = captured[0]
    assert cmd[cmd.index("--max-turns") + 1] == "20"
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_run_chat_turn_sonnet_uses_effort_high(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x",
        session_id=_UUID,
        resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
        model="sonnet",
    )
    cmd = captured[0]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_run_chat_turn_haiku_omits_effort_flag(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="x",
        session_id=_UUID,
        resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
        model="haiku",
    )
    cmd = captured[0]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert "--effort" not in cmd


def test_run_chat_turn_logs_session_id_mismatch(monkeypatch, caplog):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": "other-id"},
            _result_event("ok"),
        ),
    )
    with caplog.at_level("WARNING", logger="alan.claude_cli"):
        result = run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert result.claude_session_id == "other-id"
    assert any("session_id mismatch" in r.message for r in caplog.records)


def test_run_chat_turn_falls_back_when_init_missing(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "assistant", "message": "no init"},
            _result_event("ok"),
        ),
    )
    result = run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.claude_session_id == _UUID


def test_run_chat_turn_captures_usage(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": _UUID},
            _result_event("ok", usage={"input_tokens": 10, "output_tokens": 5}),
        ),
    )
    result = run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.usage == {"input_tokens": 10, "output_tokens": 5}


def test_run_chat_turn_usage_default_when_missing(monkeypatch):
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    result = run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.usage == {}


def test_extract_chat_usage_skips_non_result_events_before_finding_result():
    events: list[tuple[str, dict[str, Any]]] = [
        ("a", {"type": "system"}),
        ("b", {"type": "assistant"}),
        ("c", {"type": "result", "usage": {"k": 1}}),
        ("d", {"type": "system"}),
    ]
    assert claude_cli._extract_chat_usage(events) == {"k": 1}


def test_extract_chat_usage_no_result_returns_empty():
    assert claude_cli._extract_chat_usage([("a", {"type": "system"})]) == {}


def test_extract_chat_usage_invalid_usage_returns_empty():
    events = [("a", {"type": "result", "usage": "not-a-dict"})]
    assert claude_cli._extract_chat_usage(events) == {}


def test_run_chat_turn_writes_stream_path(monkeypatch, tmp_path):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init"},
            _result_event("ok"),
        ),
    )
    stream = tmp_path / "chat.stream.jsonl"
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT, stream_path=stream,
    )
    lines = stream.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "system"
    assert json.loads(lines[1])["type"] == "result"


def test_run_chat_turn_invokes_on_event(monkeypatch):
    captured: list[dict[str, Any]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system"},
            {"type": "assistant"},
            _result_event("ok"),
        ),
    )
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT, on_event=captured.append,
    )
    assert [e["type"] for e in captured] == ["system", "assistant", "result"]


def test_run_chat_turn_invokes_on_session_id_live_from_system_init(monkeypatch):
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": _UUID},
            _result_event("ok"),
        ),
    )
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT, on_session_id=captured.append,
    )
    assert captured == [_UUID]


def test_run_chat_turn_on_session_id_not_called_without_system_init(monkeypatch):
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "assistant"},
            _result_event("ok"),
        ),
    )
    run_chat_turn(
        user_text="x", session_id=_UUID, resume=False,
        system_prompt=_DUMMY_SYSTEM_PROMPT, on_session_id=captured.append,
    )
    assert captured == []


def _make_stream_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[float | None]:
    captured: list[float | None] = []
    orig = claude_cli._stream_subprocess

    def _spy(
        cmd: list[str],
        user_text: str,
        *,
        timeout_seconds: float | None = None,
        **kw: Any,
    ) -> Any:
        captured.append(timeout_seconds)
        return orig(cmd, user_text, timeout_seconds=timeout_seconds, **kw)

    monkeypatch.setattr(claude_cli, "_stream_subprocess", _spy)
    return captured


def test_run_chat_turn_timeout_uses_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_CHAT_TURN_TIMEOUT_SECONDS", "7")
    captured = _make_stream_spy(monkeypatch)
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    run_chat_turn(user_text="x", session_id=_UUID, resume=False, system_prompt=_DUMMY_SYSTEM_PROMPT)
    assert captured[0] == 7


def test_run_chat_turn_chat_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CHAT_TURN_TIMEOUT_SECONDS", raising=False)
    captured = _make_stream_spy(monkeypatch)
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    run_chat_turn(user_text="x", session_id=_UUID, resume=False, system_prompt=_DUMMY_SYSTEM_PROMPT)
    assert captured[0] == 600


@pytest.mark.parametrize("bad", ["", "  ", "not-int", "0", "-5"])
def test_chat_timeout_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("ALAN_CHAT_TURN_TIMEOUT_SECONDS", bad)
    captured = _make_stream_spy(monkeypatch)
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    run_chat_turn(user_text="x", session_id=_UUID, resume=False, system_prompt=_DUMMY_SYSTEM_PROMPT)
    assert captured[0] == 600


def test_chat_turn_result_default_usage_is_empty():
    result = ChatTurnResult(result_text="t", claude_session_id=_UUID)
    assert result.usage == {}


def test_module_exports():
    assert callable(claude_cli.run_chat_turn)
    assert claude_cli.ChatTurnResult is ChatTurnResult


def test_run_chat_turn_uses_custom_system_prompt(monkeypatch):
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": _UUID},
            _result_event("ok"),
        ),
        capture_cmds=captured,
    )
    run_chat_turn(
        user_text="hi",
        session_id=_UUID,
        resume=False,
        system_prompt="MY_CUSTOM_PROMPT",
    )
    joined = " ".join(captured[0])
    assert "MY_CUSTOM_PROMPT" in joined


def test_streaming_usage_limit_raises_usage_limit_error(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "hit your limit",
                "subtype": "error_limit",
            }
        ),
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert exc_info.value.raw_message == "hit your limit"


def test_streaming_session_limit_text_raises_usage_limit_error(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "You've hit your session limit · resets 3:50pm (Europe/London)",
                "subtype": "success",
            }
        ),
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert "session limit" in exc_info.value.raw_message
    assert exc_info.value.reset_at > 0


def test_streaming_rate_limit_event_rejected_raises_usage_limit_error(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1779976200,
                    "rateLimitType": "five_hour",
                },
            },
            {
                "type": "result",
                "is_error": True,
                "result": "Some wording Anthropic has not used before",
                "subtype": "success",
            }
        ),
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert exc_info.value.reset_at == pytest.approx(1779976200.0 + 120.0, abs=1.0)


def test_streaming_rate_limit_event_allowed_warning_does_not_pause(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed_warning",
                    "resetsAt": 1779976200,
                    "rateLimitType": "five_hour",
                },
            },
            {
                "type": "result",
                "is_error": True,
                "result": "transient model error",
                "subtype": "success",
            }
        ),
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, UsageLimitError)


def test_streaming_rate_limit_event_rejected_raises_even_when_result_ok(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1779976200,
                    "rateLimitType": "five_hour",
                },
            },
            _result_event("ok"),
        ),
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert exc_info.value.reset_at == pytest.approx(1779976200.0 + 120.0, abs=1.0)


def test_build_system_prompt_contains_sentinels():
    prompt = format_build_system_prompt()
    assert BUILD_SUMMARIZE_SENTINEL in prompt
    assert DISPATCH_PROPOSAL_SENTINEL in prompt


def test_build_system_prompt_read_only_posture():
    prompt = format_build_system_prompt()
    assert "Read-only investigation posture" in prompt
    assert "build planning assistant" in prompt


def test_build_system_prompt_forbids_file_level_detail():
    prompt = format_build_system_prompt()
    assert "NOT implementation detail" in prompt
    assert "implementation_plan" in prompt


def test_build_system_prompt_has_ux_lens():
    prompt = format_build_system_prompt()
    assert "## UX lens" in prompt
    assert "WCAG-AA accessibility" in prompt


def test_build_system_prompt_ux_lens_references_design_system():
    assert "docs/DESIGN_SYSTEM.md" in format_build_system_prompt()


def test_build_system_prompt_ux_lens_states_lens_application():
    assert "applying this lens" in format_build_system_prompt()


def test_chat_ux_lens_has_no_severity_vocabulary():
    lowered = claude_cli._CHAT_UX_LENS.lower()
    for banned in ("severity", "verdict", "blocker", "critical", "must fail", "gate", "dispatch"):
        assert banned not in lowered, f"{banned!r} must not appear in _CHAT_UX_LENS"


def test_build_ux_lens_symbol_removed():
    assert not hasattr(claude_cli, "_BUILD_UX_LENS")


def test_build_system_prompt_mentions_acceptance_clauses():
    assert "acceptance_clauses" in format_build_system_prompt()


def test_build_system_prompt_colon_quoting_guidance():
    assert "quoted scalars" in format_build_system_prompt()


def test_build_system_prompt_mentions_counter_case():
    prompt = format_build_system_prompt()
    assert "counter_case" in prompt
    assert "against" in prompt
    assert "cheapest_80" in prompt
    assert "insufficient data" in prompt


def test_build_system_prompt_no_args_matches_empty_string_arg():
    assert format_build_system_prompt() == format_build_system_prompt("")


def test_build_system_prompt_omits_shop_stats_section_when_absent():
    assert "Measured shop stats" not in format_build_system_prompt()


def test_build_system_prompt_includes_shop_stats_when_provided():
    prompt = format_build_system_prompt("Learning metrics: 62.0% first-pass approval")
    assert "## Measured shop stats" in prompt
    assert "Learning metrics: 62.0% first-pass approval" in prompt


def test_claude_cli_defines_untrusted_content_notice():
    assert isinstance(claude_cli.UNTRUSTED_CONTENT_NOTICE, str)
    assert "DATA to evaluate" in claude_cli.UNTRUSTED_CONTENT_NOTICE
    assert "never instructions" in claude_cli.UNTRUSTED_CONTENT_NOTICE


def test_build_system_prompt_includes_untrusted_content_notice():
    assert claude_cli.UNTRUSTED_CONTENT_NOTICE in format_build_system_prompt()


def test_explore_system_prompt_includes_untrusted_content_notice():
    prompt = format_explore_system_prompt("research")
    assert claude_cli.UNTRUSTED_CONTENT_NOTICE in prompt


def test_streaming_server_error_overloaded_text_raises_server_error_retryable(
    monkeypatch,
):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "overloaded",
                "subtype": "error_during_execution",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_streaming_server_error_by_api_status_code_raises_retryable(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "some server error",
                "subtype": "error_during_execution",
                "api_status_code": 529,
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_streaming_server_error_4xx_status_not_retryable(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "bad request",
                "subtype": "error_during_execution",
                "api_status_code": 400,
            }
        ),
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_server_error_by_api_error_type_raises_retryable(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "something went wrong",
                "subtype": "error_during_execution",
                "api_error_type": "overloaded_error",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_streaming_usage_limit_not_misidentified_as_server_error(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "You've hit your limit",
                "subtype": "error_during_execution",
            }
        ),
    )
    with pytest.raises(UsageLimitError):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_server_error_retryable_is_subclass_of_claude_cli_error():
    assert issubclass(ServerErrorRetryable, ClaudeCliError)


def test_is_server_error_internal_server_error_text(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "internal server error occurred",
                "subtype": "error_during_execution",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_is_server_error_service_unavailable_api_type(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "temporarily unavailable",
                "subtype": "error_during_execution",
                "api_error_type": "service_unavailable_error",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_is_server_error_rate_limit_error_api_type_not_retried_as_server_error(
    monkeypatch,
):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "hit your limit",
                "subtype": "error_during_execution",
                "api_error_type": "rate_limit_error",
            }
        ),
    )
    with pytest.raises(UsageLimitError):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_ftimeout_from_env_valid_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ALAN_TEST_TO", "1.5")
    assert _ftimeout_from_env("_ALAN_TEST_TO", 99.0) == 1.5


def test_ftimeout_from_env_missing_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("_ALAN_TEST_TO", raising=False)
    assert _ftimeout_from_env("_ALAN_TEST_TO", 42.0) == 42.0


def test_ftimeout_from_env_empty_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ALAN_TEST_TO", "  ")
    assert _ftimeout_from_env("_ALAN_TEST_TO", 42.0) == 42.0


def test_ftimeout_from_env_invalid_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ALAN_TEST_TO", "not-a-number")
    assert _ftimeout_from_env("_ALAN_TEST_TO", 42.0) == 42.0


def test_ftimeout_from_env_zero_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ALAN_TEST_TO", "0")
    assert _ftimeout_from_env("_ALAN_TEST_TO", 42.0) == 42.0


def test_check_deadlines_returns_remaining_when_in_budget() -> None:
    now = time.monotonic()
    remaining = _check_deadlines(now, now, 60.0, 3600.0)
    assert 0 < remaining <= 60.0


def test_check_deadlines_abs_timeout_exceeded_raises() -> None:
    now = time.monotonic()
    with pytest.raises(ClaudeCliTimeout, match="absolute ceiling"):
        _check_deadlines(now - 100.0, now, 9999.0, 1.0)


def test_check_deadlines_idle_elapsed_raises() -> None:
    now = time.monotonic()
    with pytest.raises(ClaudeCliTimeout, match="idle for"):
        _check_deadlines(now - 5.0, now - 10.0, 1.0, 9999.0)


def test_kill_and_reap_calls_kill_and_wait() -> None:
    class _P:
        killed = False
        waited = False

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            self.waited = True
            return 0

    p = _P()
    _kill_and_reap(p)  # type: ignore[arg-type]
    assert p.killed
    assert p.waited


def test_kill_and_reap_suppresses_timeout_expired() -> None:
    class _P:
        killed = False

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="c", timeout=1)

    _kill_and_reap(_P())  # type: ignore[arg-type]  # must not raise


def test_idle_timeout_raises_claude_cli_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_cli, "_chat_turn_timeout_seconds", lambda: 0.05)
    holder: list[_FakePopen] = []

    def fake(cmd: list[str], **_kw: Any) -> _FakePopen:
        p = _FakePopen(stall_stdout=True)
        holder.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout, match="idle for"):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert holder[0].killed is True


def test_blank_stdout_lines_not_counted_as_events(monkeypatch: pytest.MonkeyPatch) -> None:
    blank_then_result = ["\n", "  \n", json.dumps(_result_event("done")) + "\n"]
    _patch_popen(monkeypatch, stdout_lines=blank_then_result)
    result = run_chat_turn(
        user_text="x", session_id=_UUID, resume=False, system_prompt=_DUMMY_SYSTEM_PROMPT,
    )
    assert result.result_text == "done"


def test_run_agent_error_max_turns_raises_turn_ceiling_reached(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="some output", subtype="error_max_turns"))
    with pytest.raises(TurnCeilingReached):
        run_agent("sys", "user")


def test_run_agent_error_max_turns_message_contains_ceiling(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="ceiling detail", subtype="error_max_turns"))
    with pytest.raises(TurnCeilingReached, match="ceiling"):
        run_agent("sys", "user")


def test_run_agent_turn_ceiling_reached_is_caught_as_claude_cli_error(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="x", subtype="error_max_turns"))
    with pytest.raises(ClaudeCliError):
        run_agent("sys", "user")


def test_run_agent_non_ceiling_error_subtype_not_turn_ceiling(
    patch_subprocess, claude_json
):
    patch_subprocess(stdout=claude_json(result="boom", subtype="error_during_execution"))
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent("sys", "user")
    assert not isinstance(exc_info.value, TurnCeilingReached)


def test_turn_ceiling_reached_is_not_server_error_retryable():
    assert not issubclass(TurnCeilingReached, ServerErrorRetryable)


def test_success_contradiction_401_is_retryable_with_distinct_marker(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "Failed to authenticate. API Error: 401 Invalid authentication credentials",
                is_error=True,
                subtype="success",
            )
        ),
        returncode=1,
    )
    with pytest.raises(ServerErrorRetryable) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert "success-contradiction" in str(exc_info.value)
    assert "claude CLI: success;" not in str(exc_info.value)


def test_success_contradiction_connection_refused_is_retryable(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "API Error: Unable to connect to API (ConnectionRefused)",
                is_error=True,
                subtype="success",
            )
        ),
        returncode=1,
    )
    with pytest.raises(ServerErrorRetryable):
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )


def test_startup_auth_error_with_no_result_event_stays_terminal(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=[],
        stderr="Error: 401 authentication failed, invalid api key",
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_success_contradiction_non_transient_stays_terminal(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "something else entirely broke",
                is_error=True,
                subtype="success",
            )
        ),
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, ServerErrorRetryable)
    assert "claude CLI: success;" in str(exc_info.value)


def test_success_contradiction_requires_literal_success_subtype(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "Failed to authenticate. API Error: 401 Invalid authentication credentials",
                is_error=True,
                subtype="",
            )
        ),
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_contradiction_gate_does_not_widen_existing_error_subtype_path(monkeypatch):
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "Failed to authenticate. API Error: 401 Invalid authentication credentials",
                is_error=True,
                subtype="error_during_execution",
            )
        ),
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_chat_turn(
            user_text="x", session_id=_UUID, resume=False,
            system_prompt=_DUMMY_SYSTEM_PROMPT,
        )
    assert not isinstance(exc_info.value, ServerErrorRetryable)

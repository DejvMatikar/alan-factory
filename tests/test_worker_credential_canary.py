from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import claude_cli
from alan.claude_cli_grants import ACCEPT_EDITS_MODE, credential_deny_rules

_STUB = Path(__file__).resolve().parent.parent / "alan" / "web" / "e2e" / "fixtures" / "claude"
_TOOLS = ["Read", "Grep", "Bash(git log:*)", "Bash(cat:*)"]


@pytest.fixture(autouse=True)
def synthetic_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    alan_home = tmp_path / "home" / ".alan"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(alan_home / "state"))
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(alan_home / "secrets"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(alan_home / "engines"))
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return alan_home


def worker_cmd(
    mode: str = "auto", output_format: str = "json", resume: str | None = None
) -> list[str]:
    return claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format=output_format,
        max_turns=1,
        allowed_tools=list(_TOOLS),
        permission_mode=mode,
        resume_session_id=resume,
    )


def run_stub(argv: list[str], stdin: str) -> list[dict[str, object]]:
    result = subprocess.run(
        ["bash", str(_STUB), *argv],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def probe(cmd: list[str], target: Path) -> str:
    events = run_stub(cmd[1:], f"READ_PROBE: {target}\n")
    return str(events[-1]["result"])


def test_stub_refuses_a_read_of_a_canary_in_the_secrets_store(synthetic_roots: Path) -> None:
    verdict = probe(worker_cmd(), synthetic_roots / "secrets" / "canary-token")
    assert verdict.startswith("DENIED: Read(//")
    assert verdict.endswith("/secrets/**)")


def test_stub_refuses_a_read_of_the_control_token_file(synthetic_roots: Path) -> None:
    verdict = probe(worker_cmd(), synthetic_roots / "state" / "control_token")
    assert verdict.startswith("DENIED: Read(//")
    assert verdict.endswith("/state/control_token)")


def test_stub_refuses_a_read_of_the_control_token_metadata_file(synthetic_roots: Path) -> None:
    verdict = probe(worker_cmd(), synthetic_roots / "state" / "control_token_meta.json")
    assert verdict.startswith("DENIED: Read(//")


def test_stub_refuses_a_read_of_the_secrets_env_file(synthetic_roots: Path) -> None:
    verdict = probe(worker_cmd(), synthetic_roots / "secrets.env")
    assert verdict.endswith("/.alan/secrets.env)")


def test_stub_refuses_a_read_under_the_gh_config_dir(tmp_path: Path) -> None:
    verdict = probe(worker_cmd(), tmp_path / "gh-config" / "hosts.yml")
    assert verdict.startswith("DENIED: Read(//")
    assert verdict.endswith("/gh-config/**)")


def test_stub_refuses_a_read_of_an_engine_record(synthetic_roots: Path) -> None:
    verdict = probe(worker_cmd(), synthetic_roots / "engines" / "record.json")
    assert verdict.endswith("/engines/**)")


def test_stub_refuses_a_read_of_the_claude_credential_file(tmp_path: Path) -> None:
    verdict = probe(worker_cmd(), tmp_path / "home" / ".claude" / ".credentials.json")
    assert verdict.endswith("/.claude/.credentials.json)")


def test_stub_refuses_the_secrets_directory_itself(synthetic_roots: Path) -> None:
    assert probe(worker_cmd(), synthetic_roots / "secrets").startswith("DENIED: Read(//")


def test_stub_refuses_the_same_canary_under_accept_edits(synthetic_roots: Path) -> None:
    cmd = worker_cmd(mode=ACCEPT_EDITS_MODE)
    assert probe(cmd, synthetic_roots / "secrets" / "canary-token").startswith("DENIED: ")


def test_stub_refuses_in_stream_json_output(synthetic_roots: Path) -> None:
    cmd = worker_cmd(output_format="stream-json")
    events = run_stub(cmd[1:], f"READ_PROBE: {synthetic_roots / 'state' / 'control_token'}\n")
    assert events[0]["type"] == "system"
    assert str(events[-1]["result"]).startswith("DENIED: ")


def test_stub_stops_collecting_deny_rules_at_the_next_flag(synthetic_roots: Path) -> None:
    cmd = worker_cmd(resume="00000000-0000-4000-8000-000000000000")
    assert cmd[-2] == "--resume"
    assert probe(cmd, synthetic_roots / "secrets" / "canary-token").startswith("DENIED: ")


def test_stub_allows_a_path_no_rule_covers(tmp_path: Path) -> None:
    assert probe(worker_cmd(), tmp_path / "workspace" / "notes.txt") == "ALLOWED"


def test_stub_allows_a_sibling_that_only_shares_a_name_prefix(synthetic_roots: Path) -> None:
    assert probe(worker_cmd(), synthetic_roots / "secrets-other" / "file") == "ALLOWED"
    assert probe(worker_cmd(), synthetic_roots / "engines2" / "file") == "ALLOWED"


def test_stub_allows_everything_when_no_deny_rule_was_passed(synthetic_roots: Path) -> None:
    events = run_stub(["--output-format", "json"], f"READ_PROBE: {synthetic_roots}/secrets/x\n")
    assert events[-1]["result"] == "ALLOWED"


def test_stub_allows_everything_when_the_deny_list_is_empty(synthetic_roots: Path) -> None:
    argv = ["--disallowedTools", "--output-format", "json"]
    events = run_stub(argv, f"READ_PROBE: {synthetic_roots}/secrets/x\n")
    assert events[-1]["result"] == "ALLOWED"


def test_stub_treats_the_wildcard_deny_as_covering_every_path(tmp_path: Path) -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys", output_format="json", max_turns=1, deny_all_tools=True
    )
    assert probe(cmd, tmp_path / "anything") == "DENIED: *"


def test_stub_ignores_non_read_rules_when_probing_a_read(tmp_path: Path) -> None:
    argv = ["--disallowedTools", "Bash(security:*)", "Write Edit", "--output-format", "json"]
    events = run_stub(argv, f"READ_PROBE: {tmp_path}/x\n")
    assert events[-1]["result"] == "ALLOWED"


def test_stub_keeps_a_rule_with_a_space_in_the_path_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spaced = tmp_path / "my state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(spaced / "state"))
    assert probe(worker_cmd(), spaced / "state" / "control_token").startswith("DENIED: ")


def test_stub_routing_is_unchanged_when_stdin_has_no_probe() -> None:
    cmd = ["--append-system-prompt", "You are the architect", "--output-format", "json"]
    events = run_stub(cmd, "hello\n")
    assert events == [{"result": "STUB ARCHITECT", "is_error": False}]


def test_stub_routing_is_unchanged_for_a_worker_argument_list() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "You are the architect", output_format="json", max_turns=1, allowed_tools=list(_TOOLS)
    )
    events = run_stub(cmd[1:], "hello\n")
    assert events[-1]["result"] == "STUB ARCHITECT"


def test_every_credential_rule_reaches_the_stub_as_its_own_argument() -> None:
    cmd = worker_cmd()
    start = cmd.index("--disallowedTools") + 1
    assert cmd[start + 1 : start + 1 + len(credential_deny_rules())] == credential_deny_rules()


def test_the_real_claude_spawn_guard_still_refuses_a_direct_spawn() -> None:
    with pytest.raises(RuntimeError, match="real 'claude' CLI"):
        subprocess.run(["claude", "--version"], check=False)
    with pytest.raises(RuntimeError, match="real 'claude' CLI"):
        subprocess.Popen(["claude", "--version"])

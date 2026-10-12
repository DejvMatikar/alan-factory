from __future__ import annotations

from pathlib import Path

import pytest

from alan.driver_loop_cmd import (
    VERIFIER_GH_TOKEN_ENV,
    build_argv,
    build_env,
    render_for_doc,
)
from alan.driver_loop_registry import DriverLoopEntry


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "command": "/alan-issue-driver",
        "cwd": "/opt/checkouts/alan-issue-driver-clone",
        "self_paced": False,
        "interval_seconds": 1800,
        "timeout_seconds": 3600,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


def test_build_argv_starts_with_claude_p_prompt() -> None:
    argv = build_argv(_entry())
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[2] == "/alan-issue-driver"


def test_build_argv_includes_args_in_prompt() -> None:
    argv = build_argv(_entry(args="DejvMatikar/alan"))
    assert argv[2] == "/alan-issue-driver DejvMatikar/alan"


def test_build_argv_uses_dont_ask_permission_mode() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--permission-mode")
    assert argv[idx + 1] == "dontAsk"


def test_build_argv_includes_fixed_flags() -> None:
    argv = build_argv(_entry())
    for flag in (
        "--tools",
        "--setting-sources",
        "--strict-mcp-config",
        "--output-format",
        "--verbose",
        "--disallowedTools",
    ):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_build_argv_tools_gate_defaults_to_base_read_only_tools() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert tools == ["Read", "Grep", "Glob"]


def test_build_argv_tools_gate_excludes_bash_without_bash_grant() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "Bash" not in tools


def test_build_argv_tools_gate_includes_bash_when_bash_grant_present() -> None:
    entry = _entry(allowed_tools=("Bash(git log:*)",))
    argv = build_argv(entry)
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "Bash" in tools


def test_build_argv_tools_gate_excludes_write_edit_without_own_state_write() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "Write" not in tools
    assert "Edit" not in tools


def test_build_argv_tools_gate_includes_write_edit_when_own_state_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(tmp_path))
    entry = _entry(own_state_write=True)
    argv = build_argv(entry)
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "Write" in tools
    assert "Edit" in tools


def test_build_argv_tools_gate_excludes_webfetch_without_domains() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "WebFetch" not in tools


def test_build_argv_tools_gate_includes_webfetch_when_domains_set() -> None:
    entry = _entry(web_fetch_domains=("github.com",))
    argv = build_argv(entry)
    idx = argv.index("--tools")
    tools = argv[idx + 1 : argv.index("--setting-sources")]
    assert "WebFetch" in tools


def test_build_argv_never_grants_permission_mode_flag_beyond_dont_ask() -> None:
    argv = build_argv(_entry())
    assert argv.count("--permission-mode") == 1


def test_build_argv_disallowed_tools_never_include_raw_gh_api() -> None:
    argv = build_argv(_entry())
    idx = argv.index("--disallowedTools")
    disallowed = argv[idx + 1]
    assert "gh api" in disallowed


def test_build_argv_omits_add_dir_when_no_grants() -> None:
    argv = build_argv(_entry())
    assert "--add-dir" not in argv


def test_build_argv_expands_tilde_in_add_dirs() -> None:
    entry = _entry(add_dirs=("~/extra-dir",))
    argv = build_argv(entry)
    idx = argv.index("--add-dir")
    add_dirs = argv[idx + 1 :]
    assert str(Path.home() / "extra-dir") in add_dirs
    assert not any(d.startswith("~") for d in add_dirs)


def test_build_argv_adds_state_dir_when_own_state_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(tmp_path))
    entry = _entry(own_state_write=True, allowed_tools=("Bash(git log:*)",))
    argv = build_argv(entry)
    idx = argv.index("--add-dir")
    add_dirs = argv[idx + 1 :]
    assert str(entry.state_dir) in add_dirs


def test_build_argv_grants_scoped_write_edit_for_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(tmp_path))
    entry = _entry(own_state_write=True)
    argv = build_argv(entry)
    idx = argv.index("--allowedTools")
    allowed = argv[idx + 1 :]
    assert any(t.startswith("Write(") for t in allowed)
    assert any(t.startswith("Edit(") for t in allowed)


def test_build_argv_emits_double_slash_write_edit_rules_for_state_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", "/srv/driver-loops")
    entry = _entry(own_state_write=True)
    argv = build_argv(entry)
    idx = argv.index("--allowedTools")
    allowed = argv[idx + 1 :]
    write_edit = [t for t in allowed if t.startswith("Write(") or t.startswith("Edit(")]
    assert write_edit == [
        "Write(//srv/driver-loops/alan-issue-driver/**)",
        "Edit(//srv/driver-loops/alan-issue-driver/**)",
    ]


def test_build_argv_keeps_add_dir_values_as_plain_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", "/srv/driver-loops")
    entry = _entry(own_state_write=True, add_dirs=("/opt/checkouts/extra",))
    argv = build_argv(entry)
    idx = argv.index("--add-dir")
    add_dirs = argv[idx + 1 :]
    assert add_dirs == ["/opt/checkouts/extra", "/srv/driver-loops/alan-issue-driver"]


def test_second_engine_shaped_entry_writes_only_to_its_state_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", "/srv/driver-loops")
    entry = _entry(
        name="sample-issue-driver",
        own_state_write=True,
        add_dirs=("~/projects/alan/docs",),
        dedicated_clone=True,
    )
    argv = build_argv(entry)
    idx = argv.index("--allowedTools")
    allowed = argv[idx + 1 :]
    write_edit = [t for t in allowed if t.startswith("Write(") or t.startswith("Edit(")]
    assert write_edit == [
        "Write(//srv/driver-loops/sample-issue-driver/**)",
        "Edit(//srv/driver-loops/sample-issue-driver/**)",
    ]
    add_idx = argv.index("--add-dir")
    add_dirs = argv[add_idx + 1 :]
    assert add_dirs == [
        str(Path.home() / "projects" / "alan" / "docs"),
        "/srv/driver-loops/sample-issue-driver",
    ]
    assert not any("projects" in t for t in write_edit)


@pytest.mark.parametrize(
    "overrides",
    [
        {"add_dirs": ("/opt/checkouts/extra",)},
        {"own_state_write": True},
        {"own_state_write": True, "add_dirs": ("/opt/checkouts/extra",)},
    ],
    ids=["add-dirs-only", "own-state-write-only", "both"],
)
def test_synthetic_entries_never_emit_double_slash_add_dirs(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", "/srv/driver-loops")
    entry = _entry(**overrides)
    argv = build_argv(entry)
    idx = argv.index("--add-dir")
    add_dirs = argv[idx + 1 :]
    assert not any(d.startswith("//") for d in add_dirs)


def test_build_argv_web_fetch_domains_become_scoped_tools() -> None:
    entry = _entry(web_fetch_domains=("github.com",))
    argv = build_argv(entry)
    idx = argv.index("--allowedTools")
    allowed = argv[idx + 1 :]
    assert "WebFetch(domain:github.com)" in allowed


def test_build_argv_omits_model_flag_when_unset() -> None:
    argv = build_argv(_entry())
    assert "--model" not in argv


def test_build_argv_includes_model_flag_when_set() -> None:
    argv = build_argv(_entry(model="opus"))
    idx = argv.index("--model")
    assert argv[idx + 1] == "opus"


def test_build_env_excludes_verifier_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERIFIER_GH_TOKEN_ENV, "secret-token")
    env = build_env()
    assert VERIFIER_GH_TOKEN_ENV not in env


def test_build_env_excludes_control_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_CONTROL_TOKEN", "secret-control")
    env = build_env()
    assert "ALAN_CONTROL_TOKEN" not in env


def test_build_env_excludes_registered_secret_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONAR_TOKEN", "secret-sonar")
    env = build_env()
    assert "SONAR_TOKEN" not in env


def test_build_env_includes_path_and_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/srv/alan")
    env = build_env()
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/srv/alan"


def test_build_env_excludes_unlisted_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_RANDOM_VAR", "value")
    env = build_env()
    assert "SOME_RANDOM_VAR" not in env


def test_render_for_doc_matches_shlex_join_of_argv() -> None:
    entry = _entry()
    rendered = render_for_doc(entry)
    assert rendered.startswith("claude -p ")
    assert "/alan-issue-driver" in rendered

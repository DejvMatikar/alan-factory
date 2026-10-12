from __future__ import annotations

from collections.abc import Callable

import pytest

from alan import claude_cli, default_profile
from alan.claude_cli import run_agent_streaming
from alan.claude_cli_grants import (
    OWN_REPO_LABEL,
    PREP_CHAT_SCOPE_CLAUSE,
    build_no_impl,
    configured_prep_chat_origin,
    credential_deny_rules,
    disallowed_tools_args,
    explore_no_impl,
    grant_args,
    prep_chat_scope_clause,
)
from alan.default_profile import Resolved, Source
from alan.engine_config import default_engine_config
from alan.teams import _base as base
from alan.teams._base import WorkerOverrides
from tests.test_claude_cli_streaming import _lines, _patch_popen, _result_event

_NEUTRAL_ORIGIN = "https://prep.example.test"
_BUILD_SESSIONS = "/api/build-chat/sessions/"
_EXPLORE_SESSIONS = "/api/explore-chat/sessions/"
_BRACED_ORIGINS = [
    "https://prep.example.test/{repo_label}",
    "https://prep.example.test/{x}",
    "https://prep.example.test/{}",
    "https://prep.example.test/{0}",
]
_POSTURE_RENDERERS = pytest.mark.parametrize(
    "render", [build_no_impl, explore_no_impl], ids=["build", "explore"]
)


def with_prep_chat_origin(monkeypatch: pytest.MonkeyPatch, origin: str | None) -> None:
    profile = default_profile.resolved().profile
    resolved = Resolved(profile.model_copy(update={"prep_chat_origin": origin}), Source.profile)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


def test_grant_args_emits_allowed_tools_then_add_dirs() -> None:
    assert grant_args(
        allowed_tools=["Read"], add_dirs=["/live/.alan"], deny_all_tools=False
    ) == ["--allowedTools", "Read", "--add-dir", "/live/.alan"]


def test_grant_args_empty_when_nothing_granted() -> None:
    assert grant_args(allowed_tools=None, add_dirs=None, deny_all_tools=False) == []


def test_grant_args_deny_all_tools_suppresses_every_grant() -> None:
    assert (
        grant_args(
            allowed_tools=["Read"], add_dirs=["/live/.alan"], deny_all_tools=True
        )
        == []
    )


def test_disallowed_tools_args_deny_all_overrides_permission_mode() -> None:
    for mode in ("auto", "acceptEdits"):
        assert disallowed_tools_args(deny_all_tools=True, permission_mode=mode) == [
            "--disallowedTools",
            "*",
        ]


def test_disallowed_tools_args_blocks_write_tools_outside_accept_edits() -> None:
    args = disallowed_tools_args(deny_all_tools=False, permission_mode="auto")
    assert args[:2] == ["--disallowedTools", "Write Edit NotebookEdit"]
    assert args[2:] == credential_deny_rules()


def test_disallowed_tools_args_carry_only_credential_rules_under_accept_edits() -> None:
    args = disallowed_tools_args(deny_all_tools=False, permission_mode="acceptEdits")
    assert args == ["--disallowedTools", *credential_deny_rules()]


def test_build_cmd_add_dirs_emitted_as_discrete_argv_tokens() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        add_dirs=["/live/.alan", "/live/other"],
    )
    idx = cmd.index("--add-dir")
    assert cmd[idx + 1] == "/live/.alan"
    assert cmd[idx + 2] == "/live/other"


def test_build_cmd_omits_add_dir_flag_by_default() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
    )
    assert "--add-dir" not in cmd


def test_build_cmd_deny_all_tools_skips_add_dirs() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        add_dirs=["/live/.alan"],
        deny_all_tools=True,
    )
    assert "--add-dir" not in cmd


def test_streaming_omits_add_dir_flag_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    assert "--add-dir" not in cmds[0]


def test_streaming_appends_add_dirs_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", add_dirs=["/live/.alan"])
    cmd = cmds[0]
    assert cmd[cmd.index("--add-dir") + 1] == "/live/.alan"


def test_overrides_to_kwargs_omits_add_dirs_when_unset() -> None:
    kwargs = base._overrides_to_kwargs(WorkerOverrides())  # noqa: SLF001
    assert "add_dirs" not in kwargs


def test_overrides_to_kwargs_passes_add_dirs_through() -> None:
    kwargs = base._overrides_to_kwargs(  # noqa: SLF001
        WorkerOverrides(add_dirs=["/live/.alan"])
    )
    assert kwargs["add_dirs"] == ["/live/.alan"]


def test_default_team_tools_grants_maintenance_read_probes() -> None:
    cfg = default_engine_config()
    assert cfg.team_tools["maintenance"] == [
        "Bash(gh run list:*)",
        "Bash(ps:*)",
        "Bash(stat:*)",
        "Bash(find:*)",
    ]


def test_prep_chat_scope_clause_fetches_against_the_given_origin() -> None:
    clause = prep_chat_scope_clause(OWN_REPO_LABEL, _NEUTRAL_ORIGIN)
    assert f"{_NEUTRAL_ORIGIN}{_BUILD_SESSIONS}<id>" in clause
    assert _EXPLORE_SESSIONS in clause


def test_prep_chat_scope_clause_is_empty_without_an_origin() -> None:
    assert prep_chat_scope_clause(OWN_REPO_LABEL, None) == ""


@_POSTURE_RENDERERS
def test_posture_block_without_an_origin_grants_no_session_fetch(
    render: Callable[[str, str | None], str],
) -> None:
    block = render(OWN_REPO_LABEL, None)
    assert _BUILD_SESSIONS not in block
    assert _EXPLORE_SESSIONS not in block


@_POSTURE_RENDERERS
@pytest.mark.parametrize("label", [OWN_REPO_LABEL, "acme/widget"])
def test_posture_block_with_an_origin_names_exactly_that_origin(
    render: Callable[[str, str | None], str], label: str
) -> None:
    block = render(label, _NEUTRAL_ORIGIN)
    assert f"{_NEUTRAL_ORIGIN}{_BUILD_SESSIONS}<id>" in block
    assert f"bound to {label}" in block


@_POSTURE_RENDERERS
@pytest.mark.parametrize("origin", _BRACED_ORIGINS)
def test_posture_block_renders_a_braced_origin_as_written(
    render: Callable[[str, str | None], str], origin: str
) -> None:
    block = render("acme/widget", origin)
    assert f"{origin}{_BUILD_SESSIONS}<id>" in block
    assert "bound to acme/widget" in block


def test_configured_prep_chat_origin_is_none_under_the_suite_profile() -> None:
    assert configured_prep_chat_origin() is None


def test_configured_prep_chat_origin_reads_the_resolved_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    assert configured_prep_chat_origin() == _NEUTRAL_ORIGIN


def test_build_prompt_resolves_the_origin_on_every_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, "https://first.example.test")
    claude_cli.format_build_system_prompt()
    with_prep_chat_origin(monkeypatch, "https://second.example.test")
    second = claude_cli.format_build_system_prompt()
    assert "https://second.example.test" in second
    assert "https://first.example.test" not in second


def test_explore_prompt_resolves_the_origin_on_every_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, "https://first.example.test")
    claude_cli.format_explore_system_prompt("architecture")
    with_prep_chat_origin(monkeypatch, "https://second.example.test")
    second = claude_cli.format_explore_system_prompt("architecture")
    assert "https://second.example.test" in second
    assert "https://first.example.test" not in second


def test_prep_chat_scope_clause_declines_on_engine_key_mismatch() -> None:
    assert "{repo_label}" in PREP_CHAT_SCOPE_CLAUSE
    assert "engine_key" in PREP_CHAT_SCOPE_CLAUSE
    assert "different repository" in PREP_CHAT_SCOPE_CLAUSE
    assert "decline" in PREP_CHAT_SCOPE_CLAUSE

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from alan import (
    agent_catalogue,
    agent_catalogue_teams,
    agent_catalogue_teams_impl,
    agent_catalogue_teams_orchestrated,
    agent_key,
    claude_cli,
)
from alan import agent_catalogue_helpers as helpers
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource
from alan.agent_catalogue_models import AgentCatalogue, AgentEntry, FlagOut
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.driver_loop_cmd import VERIFIER_GH_TOKEN_ENV
from alan.engine_config import (
    EngineConfig,
    RepoIdentity,
    RoleConfig,
    default_engine_config,
    registered_secret_env_names,
)
from alan.launch_spec import PromptSegment
from alan.teams._base import TERSENESS_BLOCK
from tests._agent_catalogue_support import demo_slot, make_resolved


def _group(name: str, agents: list[ResolvedAgent]) -> ResolverGroup:
    return ResolverGroup(
        id=name,
        name=name,
        key=agent_key.AgentKey("background_helper", name),
        resolve=lambda _cfg: agents,
    )


def _failing_group(name: str) -> ResolverGroup:
    def boom(_cfg: EngineConfig) -> list[ResolvedAgent]:
        raise RuntimeError("resolver exploded")

    return ResolverGroup(
        id=name, name=name, key=agent_key.AgentKey("background_helper", name), resolve=boom
    )


def test_runtime_slot_is_idempotent_and_rejects_a_conflicting_description() -> None:
    first = helpers.runtime_slot("CATALOGUE_TEST_SLOT", "what the slot holds")
    assert helpers.runtime_slot("CATALOGUE_TEST_SLOT", "what the slot holds") == first
    assert helpers.slot_description("CATALOGUE_TEST_SLOT") == "what the slot holds"
    with pytest.raises(ValueError, match="CATALOGUE_TEST_SLOT"):
        helpers.runtime_slot("CATALOGUE_TEST_SLOT", "another description")


def test_runtime_slot_declarations_from_many_threads_agree_on_one_description() -> None:
    descriptions = ["first description", "second description"]
    outcomes: list[str | ValueError] = []

    def declare(description: str) -> None:
        try:
            outcomes.append(helpers.runtime_slot("CATALOGUE_THREADED_SLOT", description))
        except ValueError as exc:
            outcomes.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(declare, descriptions * 16))
    registered = helpers.slot_description("CATALOGUE_THREADED_SLOT")
    assert registered in descriptions
    assert sum(isinstance(outcome, str) for outcome in outcomes) == 16
    assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 16


def test_slot_description_falls_back_to_the_name_for_an_unknown_slot() -> None:
    assert helpers.slot_description("NEVER_DECLARED_SLOT") == "NEVER_DECLARED_SLOT"


def test_segments_from_text_splits_at_slots_and_labels_them() -> None:
    segments = helpers.segments_from_text(f"before {demo_slot()} after", "body")
    assert [(s.kind, s.label) for s in segments] == [
        ("text", "body"),
        ("placeholder", "DEMO_SLOT"),
        ("text", "body"),
    ]
    assert segments[0].text == "before "
    assert segments[1].description == "Value inserted by the demo agent at run time"
    assert segments[2].text == " after"


def test_segments_from_text_labels_shared_blocks_and_repeats() -> None:
    text = f"intro\n\n{TERSENESS_BLOCK}\n\nmiddle\n\n{TERSENESS_BLOCK}"
    segments = helpers.segments_from_text(text, "body")
    shared = [s for s in segments if s.shared]
    assert [s.label for s in shared] == ["terseness_block", "terseness_block"]
    assert all(s.text == TERSENESS_BLOCK for s in shared)
    assert "".join(s.text for s in segments) == text


def test_segments_from_text_accepts_caller_supplied_shared_blocks() -> None:
    segments = helpers.segments_from_text("a SHARED-X b", "body", (("custom_block", "SHARED-X"),))
    assert [(s.label, s.shared) for s in segments] == [
        ("body", False),
        ("custom_block", True),
        ("body", False),
    ]


def test_segments_from_text_of_empty_text_is_empty() -> None:
    assert helpers.segments_from_text("", "body") == []


def test_segments_from_text_prefers_the_longer_of_two_overlapping_markers() -> None:
    text = f"{demo_slot()} B tail"
    segments = helpers.segments_from_text(text, "body", (("overlap", f"{demo_slot()} B"),))
    assert [(s.label, s.shared, s.kind) for s in segments] == [
        ("overlap", True, "text"),
        ("body", False, "text"),
    ]


def test_segments_from_spec_preserves_the_whole_prompt() -> None:
    parts = (
        PromptSegment("system_prompt", f"hello {demo_slot()}"),
        PromptSegment("headless_execution", "\n\ntail", shared=True),
    )
    segments = helpers.segments_from_spec(parts)
    assert [s.kind for s in segments] == ["text", "placeholder", "text"]
    assert segments[-1].label == "headless_execution"


def test_value_out_shows_cli_default_when_alan_passes_nothing() -> None:
    shown = helpers.value_out(None, ValueSource("builtin", "ignored"))
    assert shown.display == "CLI default"
    assert shown.value is None
    assert shown.source == "cli_default"
    assert shown.source_detail == "ignored"
    plain = helpers.value_out(None, ValueSource("builtin"))
    assert plain.source_detail == "alan passes no value; the CLI default applies"


def test_value_out_keeps_the_declared_source_for_a_value() -> None:
    shown = helpers.value_out("opus", ValueSource("engine_role", "engine role 'judge'", "judge"))
    assert (shown.display, shown.source, shown.source_detail) == (
        "opus",
        "engine_role",
        "engine role 'judge'",
    )


def test_prompt_segments_add_the_project_doc_placeholder_for_tool_using_agents() -> None:
    resolved = make_resolved()
    segments = helpers.prompt_segments(resolved)
    assert segments[-1].label == helpers.LABEL_PROJECT_DOC_SLOT
    assert segments[-1].kind == "placeholder"
    assert any(s.label == "headless_execution" and s.shared for s in segments)


def test_prompt_segments_omit_the_project_doc_placeholder_when_tools_are_denied() -> None:
    segments = helpers.prompt_segments(make_resolved(deny_all_tools=True))
    assert all(s.label != helpers.LABEL_PROJECT_DOC_SLOT for s in segments)
    assert any(s.label == "no_tools" and s.shared for s in segments)


def test_prompt_segments_omit_the_project_doc_placeholder_for_chat_layouts() -> None:
    spec = claude_cli.chat_launch_spec(
        model="opus", session_id="s", resume=False, max_turns=3, allowed_tools=[],
        system_prompt="chat",
    )
    resolved = make_resolved()
    chat = ResolvedAgent(**{**resolved.__dict__, "spec": spec})
    assert all(s.label != helpers.LABEL_PROJECT_DOC_SLOT for s in helpers.prompt_segments(chat))


def test_to_entry_projects_every_catalogue_field() -> None:
    resolved = make_resolved(
        "demo", model="opus", effort=None, stored_tools=("Read", "Bash(uv run:*)"),
        tags=("eval",), scope="machine", prompt_overridden=True,
    )
    entry = helpers.to_entry(resolved, [])
    assert entry.id == "background_helper:demo"
    assert entry.kind == "background_helper"
    assert entry.tags == ["eval"]
    assert entry.scope == "machine"
    assert entry.model.display == "opus"
    assert entry.effort.display == "xhigh"
    assert entry.turn_limit.max_turns == 7
    assert entry.permission_mode == "auto"
    assert entry.tools.stored == ["Read", "Bash(uv run:*)"]
    assert entry.tools.effective == ["Read"]
    assert "Write" in entry.tools.denied
    assert entry.prompt_overridden is True
    assert [s.label for s in entry.user_message] == ["user_message", "DEMO_SLOT"]
    assert entry.session.behaviour == "new"
    assert entry.working_directory == "the service working directory"
    assert entry.environment_note == helpers.ENVIRONMENT_NOTE
    assert entry.error is None


def test_to_entry_lists_environment_by_name_and_category_only() -> None:
    entry = helpers.to_entry(make_resolved(), [])
    assert entry.environment
    assert {tuple(vars(item)) for item in entry.environment} == {("name", "category")}


def test_error_entry_carries_the_failure_and_neutral_values() -> None:
    entry = helpers.error_entry(agent_key.COLLISION, "collision", "ValueError: nope")
    assert entry.error == "ValueError: nope"
    assert entry.id == "background_helper:collision"
    assert entry.model.display == "CLI default"
    assert entry.prompt == []


def test_resolve_all_isolates_a_failing_resolver(caplog: pytest.LogCaptureFixture) -> None:
    good = make_resolved("good")
    groups = [_failing_group("broken"), _group("good", [good])]
    with caplog.at_level(logging.ERROR, logger="alan.agent_catalogue"):
        resolved, errors = agent_catalogue.resolve_all(default_engine_config(), groups)
    assert resolved == [good]
    assert [entry.id for entry in errors] == ["background_helper:broken"]
    assert errors[0].error == "RuntimeError: resolver exploded"
    assert "agent catalogue resolver broken failed" in caplog.text


def test_resolver_groups_concatenate_every_resolver_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = _group("marker", [])
    monkeypatch.setattr(agent_catalogue_teams, "GROUPS", (marker,))
    monkeypatch.setattr(agent_catalogue_teams_orchestrated, "GROUPS", ())
    monkeypatch.setattr(agent_catalogue_teams_impl, "GROUPS", ())
    monkeypatch.setattr(agent_catalogue, "_STANDALONE_GROUPS", ())
    assert agent_catalogue.resolver_groups() == (marker,)


def _patch_groups(monkeypatch: pytest.MonkeyPatch, groups: tuple[ResolverGroup, ...]) -> None:
    monkeypatch.setattr(agent_catalogue, "resolver_groups", lambda: groups)


def _cfg_with_roles(**roles: RoleConfig) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), roles=roles)


def test_build_catalogue_collects_entries_notes_and_unread_role_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = make_resolved(
        "reader", model_source=ValueSource("engine_role", "engine role 'judge'", "judge")
    )
    _patch_groups(monkeypatch, (_group("reader", [reader]), _failing_group("broken")))
    cfg = _cfg_with_roles(
        judge=RoleConfig(model="opus"), remediator=RoleConfig(model="opus", effort="xhigh")
    )
    catalogue = agent_catalogue.build_catalogue(cfg)
    assert catalogue.engine == "acme/widget"
    assert [entry.id for entry in catalogue.entries] == [
        "background_helper:reader",
        "background_helper:broken",
    ]
    assert [flag.code for flag in catalogue.flags] == ["unread_stored_role"]
    assert "'remediator'" in catalogue.flags[0].title
    assert catalogue.notes.appended_prompt == agent_catalogue.APPENDED_PROMPT_NOTE
    assert "built-in system prompt" in catalogue.notes.appended_prompt


def test_build_catalogue_turns_an_entry_that_fails_to_build_into_an_error_entry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_groups(
        monkeypatch, (_group("pair", [make_resolved("broken"), make_resolved("healthy")]),)
    )
    real_to_entry = helpers.to_entry

    def flaky(agent: ResolvedAgent, flags: list[FlagOut]) -> AgentEntry:
        if agent.key.name == "broken":
            raise ValueError("malformed agent")
        return real_to_entry(agent, flags)

    monkeypatch.setattr(agent_catalogue, "to_entry", flaky)
    with caplog.at_level(logging.ERROR, logger="alan.agent_catalogue"):
        catalogue = agent_catalogue.build_catalogue(default_engine_config())
    by_id = {entry.id: entry for entry in catalogue.entries}
    assert by_id["background_helper:broken"].error == "ValueError: malformed agent"
    assert by_id["background_helper:healthy"].error is None
    assert "agent catalogue entry background_helper:broken failed" in caplog.text


def test_build_catalogue_reports_a_failing_unread_role_check_as_a_flag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_groups(monkeypatch, (_group("one", [make_resolved("one")]),))

    def boom(_resolved: list[ResolvedAgent], _cfg: EngineConfig) -> list[FlagOut]:
        raise RuntimeError("no roles")

    monkeypatch.setattr(agent_catalogue, "unread_role_flags", boom)
    with caplog.at_level(logging.ERROR, logger="alan.agent_catalogue"):
        catalogue = agent_catalogue.build_catalogue(default_engine_config())
    assert [entry.error for entry in catalogue.entries] == [None]
    assert [(flag.code, flag.severity, flag.detail) for flag in catalogue.flags] == [
        ("other_mismatch", "warn", "RuntimeError: no roles")
    ]
    assert "agent catalogue unread-role check failed" in caplog.text


def test_notes_name_every_inherited_item_without_showing_contents() -> None:
    names = {item.name for item in agent_catalogue.NOTES.inherited}
    assert names == {
        "Claude Code user-level settings",
        "Repository guidance file",
        "User-level permissions",
        "Hooks",
        "MCP servers",
        "Plugins",
    }
    assert all("contents not shown" in item.note for item in agent_catalogue.NOTES.inherited)


def test_summary_counts_models_efforts_kinds_flags_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_resolved("a", model="opus")
    second = make_resolved("b", model="haiku", engine_role_hint="judge")
    _patch_groups(monkeypatch, (_group("pair", [first, second]), _failing_group("broken")))
    cfg = _cfg_with_roles(judge=RoleConfig(model="opus"), orphan=RoleConfig(model="haiku"))
    summary = agent_catalogue.summarize(agent_catalogue.build_catalogue(cfg))
    assert summary.engine == "acme/widget"
    assert summary.total == 3
    assert summary.errors == 1
    assert {(c.name, c.count) for c in summary.by_model} == {("opus", 1), ("haiku", 1)}
    assert {(c.name, c.count) for c in summary.by_kind} == {("background_helper", 2)}
    assert summary.warnings >= 2
    assert sum(c.count for c in summary.by_effort) == 2


def test_the_catalogue_serialises_to_json_without_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SONAR_TOKEN", "never-show-this-token")
    _patch_groups(monkeypatch, (_group("one", [make_resolved("one")]),))
    catalogue = agent_catalogue.build_catalogue(default_engine_config())
    payload = json.dumps(catalogue.model_dump(mode="json"))
    assert "never-show-this-token" not in payload
    assert AgentCatalogue.model_validate_json(payload) == catalogue


def test_the_real_catalogue_never_contains_a_secret_or_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = sorted(registered_secret_env_names() | {CONTROL_TOKEN_ENV, VERIFIER_GH_TOKEN_ENV})
    for index, name in enumerate(names):
        monkeypatch.setenv(name, f"secret-value-{index}-b7e1")
    payload = json.dumps(agent_catalogue.build_catalogue(default_engine_config()).model_dump())
    assert "secret-value-" not in payload
    assert all(f'"{name}"' in payload for name in names if name != VERIFIER_GH_TOKEN_ENV)

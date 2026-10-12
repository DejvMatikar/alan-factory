from __future__ import annotations

import pytest

from alan.claude_cli import (
    CONSULTANTS,
    format_build_system_prompt,
    format_explore_system_prompt,
)
from alan.claude_cli_grants import (
    BUILD_REPO_BINDING,
    OWN_REPO_LABEL,
    PREP_CHAT_SCOPE_CLAUSE,
    build_no_impl,
    explore_no_impl,
)
from tests.test_claude_cli_grants import with_prep_chat_origin

_NEUTRAL_ORIGIN = "https://prep.example.test"
_BUILD_SESSIONS = "/api/build-chat/sessions/"


def _clause(repo_label: str) -> str:
    return PREP_CHAT_SCOPE_CLAUSE.format(prep_chat_origin=_NEUTRAL_ORIGIN, repo_label=repo_label)


@pytest.fixture
def neutral_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)


@pytest.mark.usefixtures("neutral_origin")
def test_build_prompt_advertises_prep_chat_read() -> None:
    prompt = format_build_system_prompt()
    assert _clause(OWN_REPO_LABEL) in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_explore_prompt_advertises_prep_chat_read_for_every_consultant() -> None:
    for consultant in CONSULTANTS:
        prompt = format_explore_system_prompt(consultant.key)
        assert _clause(OWN_REPO_LABEL) in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_build_prompt_prep_chat_clause_names_the_session_own_repo_label() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert _clause("acme/widget") in prompt
    assert _clause(OWN_REPO_LABEL) not in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_explore_prompt_prep_chat_clause_names_the_session_own_repo_label() -> None:
    prompt = format_explore_system_prompt("architecture", repo_label="acme/widget")
    assert _clause("acme/widget") in prompt
    assert _clause(OWN_REPO_LABEL) not in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_explore_prompt_prep_chat_clause_defaults_to_own_repo_label() -> None:
    prompt = format_explore_system_prompt("architecture")
    assert _clause(OWN_REPO_LABEL) in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_build_prompt_embeds_the_posture_block_for_the_configured_origin() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert build_no_impl("acme/widget", _NEUTRAL_ORIGIN) in prompt


@pytest.mark.usefixtures("neutral_origin")
def test_explore_prompt_embeds_the_posture_block_for_every_consultant() -> None:
    for consultant in CONSULTANTS:
        prompt = format_explore_system_prompt(consultant.key, repo_label="acme/widget")
        assert explore_no_impl("acme/widget", _NEUTRAL_ORIGIN) in prompt


def test_build_prompt_grants_no_session_fetch_without_a_configured_origin() -> None:
    assert _BUILD_SESSIONS not in format_build_system_prompt()


def test_explore_prompt_grants_no_session_fetch_without_a_configured_origin() -> None:
    for consultant in CONSULTANTS:
        assert _BUILD_SESSIONS not in format_explore_system_prompt(consultant.key)


def test_build_prompt_keeps_the_binding_carve_out_without_a_configured_origin() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert BUILD_REPO_BINDING.format(repo_label="acme/widget") in prompt


def test_build_repo_binding_carves_out_prep_chat_reads() -> None:
    rendered = BUILD_REPO_BINDING.format(repo_label="acme/widget")
    assert "not a violation of this binding" in rendered
    assert "preparation conversation" in rendered


@pytest.mark.usefixtures("neutral_origin")
def test_build_repo_binding_carve_out_survives_non_alan_repo_label() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert "not a violation of this binding" in prompt
    assert _clause("acme/widget") in prompt

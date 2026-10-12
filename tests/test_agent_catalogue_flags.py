from __future__ import annotations

import pytest

from alan.agent_catalogue_flags import agent_flags, unread_role_flags
from alan.agent_catalogue_helpers import ValueSource
from alan.agent_catalogue_models import FlagOut
from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig
from tests._agent_catalogue_support import make_resolved

_ENGINE_ROLE = ValueSource("engine_role", "engine role 'judge'", "judge")


def _cfg(**roles: RoleConfig) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), roles=roles)


def _codes(flags: list[FlagOut]) -> list[tuple[str, str]]:
    return [(flag.code, flag.severity) for flag in flags]


def test_a_builtin_model_equal_to_the_engine_role_is_an_info_flag() -> None:
    resolved = make_resolved(model="opus", role="judge")
    flags = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus")))
    assert _codes(flags) == [("ignores_engine_roles", "info")]
    assert "'judge'" in flags[0].detail
    assert "opus" in flags[0].detail


def test_a_builtin_model_different_from_the_engine_role_is_a_warning() -> None:
    resolved = make_resolved(model="sonnet", role="judge")
    flags = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus")))
    assert _codes(flags) == [("ignores_engine_roles", "warn")]


def test_the_hint_names_the_engine_role_instead_of_the_agent_role() -> None:
    resolved = make_resolved(model="sonnet", role="devil_advocate", engine_role_hint="judge")
    flags = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus")))
    assert _codes(flags) == [("ignores_engine_roles", "warn")]


@pytest.mark.parametrize(
    "resolved",
    [
        make_resolved(model="opus", role="judge", model_source=_ENGINE_ROLE),
        make_resolved(model="opus", role="nothing_like_it"),
        make_resolved(model="opus"),
    ],
)
def test_no_roles_flag_when_the_role_is_read_or_unmatched(resolved: object) -> None:
    flags = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus")))  # type: ignore[arg-type]
    assert [flag for flag in flags if flag.code == "ignores_engine_roles"] == []


def test_a_configured_effort_that_is_ignored_is_flagged_by_agreement() -> None:
    resolved = make_resolved(model="opus", effort="xhigh", role="judge")
    equal = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus", effort="xhigh")))
    differs = agent_flags(resolved, _cfg(judge=RoleConfig(model="opus", effort="low")))
    assert ("ignores_engine_effort", "info") in _codes(equal)
    assert ("ignores_engine_effort", "warn") in _codes(differs)
    assert "low" in [flag for flag in differs if flag.code == "ignores_engine_effort"][0].detail


def test_an_ignored_effort_shows_cli_default_when_alan_passes_none() -> None:
    resolved = make_resolved(model="haiku", role="judge")
    flags = agent_flags(resolved, _cfg(judge=RoleConfig(model="haiku", effort="high")))
    effort_flag = [flag for flag in flags if flag.code == "ignores_engine_effort"][0]
    assert effort_flag.severity == "warn"
    assert "CLI default" in effort_flag.detail


def test_no_effort_flag_when_the_role_has_no_effort_or_the_effort_is_read() -> None:
    plain = make_resolved(model="opus", role="judge")
    assert [
        f for f in agent_flags(plain, _cfg(judge=RoleConfig(model="opus"))) if "effort" in f.code
    ] == []
    reading = make_resolved(
        model="opus", effort="xhigh", role="judge", effort_source=_ENGINE_ROLE
    )
    flags = agent_flags(reading, _cfg(judge=RoleConfig(model="opus", effort="xhigh")))
    assert [f for f in flags if f.code == "ignores_engine_effort"] == []


def test_the_legacy_tag_raises_the_legacy_planning_warning() -> None:
    flags = agent_flags(make_resolved(tags=("legacy",)), _cfg())
    assert _codes(flags) == [("legacy_planning_endpoint", "warn")]
    assert "follow-up" in flags[0].detail


def test_configured_values_that_differ_from_the_launch_are_mismatches() -> None:
    resolved = make_resolved(
        model="haiku", effort=None, configured_model="opus", configured_effort="xhigh"
    )
    flags = agent_flags(resolved, _cfg())
    assert _codes(flags) == [("model_mismatch", "warn"), ("effort_mismatch", "warn")]
    assert "Configured: opus" in flags[0].detail
    assert "CLI default" in flags[1].detail


def test_configured_values_equal_to_the_launch_raise_nothing() -> None:
    resolved = make_resolved(
        model="sonnet", effort="high", configured_model="sonnet", configured_effort="high"
    )
    assert agent_flags(resolved, _cfg()) == []


def test_resolver_supplied_flags_are_appended_last() -> None:
    extra = FlagOut(code="other_mismatch", severity="warn", title="odd", detail="because")
    resolved = make_resolved(tags=("legacy",), extra_flags=(extra,))
    flags = agent_flags(resolved, _cfg())
    assert flags[-1] == extra
    assert flags[0].code == "legacy_planning_endpoint"


def test_unread_roles_are_those_no_agent_reads_for_model_or_effort() -> None:
    model_reader = make_resolved("m", model_source=ValueSource("engine_role", None, "judge"))
    effort_reader = make_resolved(
        "e", effort_source=ValueSource("engine_role", None, "implementer")
    )
    other = make_resolved("o")
    cfg = _cfg(
        judge=RoleConfig(model="opus"),
        implementer=RoleConfig(model="sonnet", effort="xhigh"),
        remediator=RoleConfig(model="opus", effort="xhigh"),
        failure_scanner=RoleConfig(model="haiku"),
    )
    flags = unread_role_flags([model_reader, effort_reader, other], cfg)
    assert [flag.title for flag in flags] == [
        "Stored engine role 'failure_scanner' is not read by anything",
        "Stored engine role 'remediator' is not read by anything",
    ]
    assert all(flag.code == "unread_stored_role" and flag.severity == "warn" for flag in flags)
    assert "effort xhigh" in flags[1].detail
    assert "effort CLI default" in flags[0].detail


def test_a_source_without_a_role_name_does_not_count_as_reading_anything() -> None:
    nameless = make_resolved("n", model_source=ValueSource("engine_role", "detail only"))
    flags = unread_role_flags([nameless], _cfg(judge=RoleConfig(model="opus")))
    assert [flag.code for flag in flags] == ["unread_stored_role"]


def test_no_roles_means_no_unread_flags() -> None:
    assert unread_role_flags([], _cfg()) == []

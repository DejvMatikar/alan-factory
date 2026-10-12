from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from alan import agent_catalogue, agent_key
from alan.agent_catalogue_helpers import ResolvedAgent, ValueSource, to_entry
from alan.agent_catalogue_models import PromptSegmentOut
from alan.engine_config import EngineConfig, default_engine_config
from tests import agent_catalogue_parity as parity
from tests.agent_catalogue_parity import Captured, ParityEnv
from tests.agent_catalogue_parity_drivers import register_all_drivers
from tests.test_agent_catalogue_parity import (
    SENTINEL_EFFORT,
    SENTINEL_MODEL,
    _compare,
    _expected_effort,
    _follows,
    _source_problems,
)

register_all_drivers()

_KEY = agent_key.MANAGER_SUMMARY


def _resolved(cfg: EngineConfig) -> ResolvedAgent:
    resolved, errors = agent_catalogue.resolve_all(cfg)
    assert errors == []
    return next(agent for agent in resolved if agent.key == _KEY)


def _launches(
    cfg: EngineConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[Captured]:
    driver = parity.covering(_KEY, cfg)
    assert driver is not None
    with monkeypatch.context() as patch:
        recorder = parity.install_recorder(patch)
        driver.run(ParityEnv(patch, tmp_path, recorder, cfg))
    return recorder.for_agent(_KEY)


@pytest.fixture
def agent_and_launches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]]:
    cfg = default_engine_config()
    agent = _resolved(cfg)
    return agent, to_entry(agent, []).prompt, _launches(cfg, monkeypatch, tmp_path)


def test_a_faithful_catalogue_entry_passes_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, prompt, launches = agent_and_launches
    assert launches
    assert _compare(agent, prompt, launches) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("model", "opus"), ("effort", "low"), ("max_turns", 99), ("permission_mode", "plan")],
)
def test_a_catalogue_value_that_differs_from_the_launch_fails_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
    field: str,
    value: Any,
) -> None:
    agent, prompt, launches = agent_and_launches
    changes: dict[str, Any] = {field: value}
    drifted = replace(agent, spec=replace(agent.spec, **changes))
    problems = _compare(drifted, prompt, launches)
    assert problems
    assert field in problems[0]


def test_a_changed_tool_grant_fails_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, prompt, launches = agent_and_launches
    drifted = replace(agent, spec=replace(agent.spec, allowed_tools=("Read", "Bash(rm:*)")))
    assert "allowed_tools" in _compare(drifted, prompt, launches)[0]


def test_a_prompt_template_that_no_longer_matches_fails_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    stale = [PromptSegmentOut(kind="text", label="system_prompt", text="an outdated prompt")]
    problems = _compare(agent, stale, launches)
    assert any("template does not match" in problem for problem in problems)


def test_a_changed_user_message_template_fails_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, prompt, launches = agent_and_launches
    drifted = replace(agent, user_message="an outdated user message")
    problems = _compare(drifted, prompt, launches)
    assert any("user message template" in problem for problem in problems)


def test_an_agent_that_is_never_launched_fails_the_comparison(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, prompt, _launches_unused = agent_and_launches
    assert _compare(agent, prompt, []) == [f"{_KEY.id}: the driver never launched this agent"]


def test_an_agent_with_no_registered_driver_is_found_by_the_lookup() -> None:
    cfg = default_engine_config()
    assert parity.covering(agent_key.AgentKey("chat", "ghost"), cfg) is None
    assert parity.covering(_KEY, cfg) is not None


def test_the_source_proof_flags_a_model_source_that_lies(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    lying = replace(agent, model_source=ValueSource("engine_role", None, "judge"))
    assert _follows(lying.model_source, "judge")
    problems = _source_problems("judge", lying, launches, launches, agent)
    assert any("model launch" in problem for problem in problems)
    assert any(SENTINEL_MODEL in problem for problem in problems)


def test_the_source_proof_accepts_builtin_sources_that_ignore_the_role(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    assert _source_problems("judge", agent, launches, launches, agent) == []
    assert _expected_effort("judge", agent, launches[0].spec) == launches[0].spec.effort


def test_the_source_proof_expects_the_sentinel_effort_for_an_engine_role_effort(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    reading = replace(agent, effort_source=ValueSource("engine_role", None, "judge"))
    assert _expected_effort("judge", reading, launches[0].spec) == SENTINEL_EFFORT


def test_the_source_proof_expects_a_derived_effort_to_follow_a_followed_model(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    following = replace(agent, model_source=ValueSource("engine_role", None, "judge"))
    assert _expected_effort("judge", following, launches[0].spec) is None


def test_the_source_proof_reports_a_changed_launch_count(
    agent_and_launches: tuple[ResolvedAgent, list[PromptSegmentOut], list[Captured]],
) -> None:
    agent, _prompt, launches = agent_and_launches
    problems = _source_problems("judge", agent, launches, [], agent)
    assert problems == [f"{_KEY.id}: launch count changed {len(launches)} -> 0"]

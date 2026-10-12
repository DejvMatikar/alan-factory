from __future__ import annotations

import os
from pathlib import Path

import pytest

from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT
from alan.engine_config import (
    AGGREGATE_BUDGET_DEFAULT,
    SCENARIO_TIMEOUT_DEFAULT,
    EngineConfig,
    RepoIdentity,
    ScenarioConfig,
)
from alan.handover import TestScenario
from alan.teams._scenario_runner import (
    _ALLOWED_COMMAND_PREFIXES,
    _allowed_prefixes_for,
    resolve_aggregate_budget,
    run_scenarios,
    scenario_timeout,
)

_MEASURED_IOS_CI_MEDIAN_S = 675.0


def _ios_engine_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="DejvMatikar", repo="example-repo"),
        scenario=ScenarioConfig(
            command_prefixes=[["xcodebuild"], ["tuist"], ["swift"], ["xcrun"]],
            timeout_s=900.0,
            aggregate_budget_s=900.0,
        ),
    )


def test_scenario_timeout_env_wins_over_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_SCENARIO_TIMEOUT_S", "42")
    assert scenario_timeout(_ios_engine_cfg()) == 42.0


def test_scenario_timeout_falls_back_to_cfg_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_SCENARIO_TIMEOUT_S", raising=False)
    assert scenario_timeout(_ios_engine_cfg()) == 900.0


def test_scenario_timeout_falls_back_to_default_when_cfg_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_SCENARIO_TIMEOUT_S", raising=False)
    assert scenario_timeout(None) == SCENARIO_TIMEOUT_DEFAULT


def test_scenario_timeout_falls_back_to_default_when_cfg_field_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_SCENARIO_TIMEOUT_S", raising=False)
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert scenario_timeout(cfg) == SCENARIO_TIMEOUT_DEFAULT


def test_resolve_aggregate_budget_uses_cfg_when_populated() -> None:
    assert resolve_aggregate_budget(_ios_engine_cfg()) == 900.0


def test_resolve_aggregate_budget_falls_back_to_default_when_cfg_none() -> None:
    assert resolve_aggregate_budget(None) == AGGREGATE_BUDGET_DEFAULT


def test_resolve_aggregate_budget_falls_back_to_default_when_cfg_field_unset() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert resolve_aggregate_budget(cfg) == AGGREGATE_BUDGET_DEFAULT


def test_allowed_prefixes_for_ios_engine_cfg_contains_xcodebuild() -> None:
    assert ("xcodebuild",) in _allowed_prefixes_for(_ios_engine_cfg())


def test_allowed_prefixes_for_none_is_unchanged() -> None:
    assert _allowed_prefixes_for(None) == _ALLOWED_COMMAND_PREFIXES


def test_allowed_prefixes_for_cfg_with_empty_gate_commands_still_includes_configured_prefixes() -> (
    None
):
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        gate_commands=[],
        scenario=ScenarioConfig(command_prefixes=[["xcodebuild"]]),
    )
    assert ("xcodebuild",) in _allowed_prefixes_for(cfg)


def test_allowed_prefixes_for_configured_prefix_duplicating_static_entry_appears_once() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        scenario=ScenarioConfig(command_prefixes=[["uv", "run"]]),
    )
    prefixes = _allowed_prefixes_for(cfg)
    assert prefixes.count(("uv", "run")) == 1


def test_ios_engine_shaped_scenario_executes_under_resolved_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "ran"
    script = bin_dir / "xcodebuild"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("ALAN_SCENARIO_TIMEOUT_S", raising=False)
    cfg = _ios_engine_cfg()
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="xcodebuild test -only-testing:AppTests/FooTests", then="",
    )
    results = run_scenarios([scenario], cwd=str(tmp_path), cfg=cfg)
    assert results[0].status != "DOCUMENTED"
    assert sentinel.exists()
    assert scenario_timeout(cfg) > _MEASURED_IOS_CI_MEDIAN_S
    assert resolve_aggregate_budget(cfg) > _MEASURED_IOS_CI_MEDIAN_S


def test_run_scenarios_alan_cfg_matches_cfg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SCENARIO_TIMEOUT_S", raising=False)
    assert scenario_timeout(_ENGINE_DEFAULT) == SCENARIO_TIMEOUT_DEFAULT == 120.0
    assert resolve_aggregate_budget(_ENGINE_DEFAULT) == AGGREGATE_BUDGET_DEFAULT == 600.0
    assert scenario_timeout(_ENGINE_DEFAULT) == scenario_timeout(None)
    assert resolve_aggregate_budget(_ENGINE_DEFAULT) == resolve_aggregate_budget(None)


def test_run_scenarios_explicit_zero_aggregate_budget_yields_budget_exhausted() -> None:
    scenario = TestScenario(id="s1", description="d", given="g", when="ls", then="")
    results = run_scenarios([scenario], cwd=".", aggregate_budget=0.0, cfg=None)
    assert results[0].status == "BUDGET_EXHAUSTED"


def test_run_scenarios_caps_timeout_to_remaining_aggregate_budget() -> None:
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="uv run python -c \"__import__('time').sleep(5)\"", then="",
    )
    results = run_scenarios(
        [scenario], cwd=".", per_scenario_timeout=900.0, aggregate_budget=0.05, cfg=None,
    )
    assert results[0].status == "TIMEOUT"

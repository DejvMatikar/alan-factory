from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from alan.engine_config import (
    AGGREGATE_BUDGET_DEFAULT,
    SCENARIO_TIMEOUT_DEFAULT,
    EngineConfig,
    RepoIdentity,
    ScenarioConfig,
)


def _cfg(
    *,
    command_prefixes: list[list[str]] | None = None,
    timeout_s: float | None = None,
    aggregate_budget_s: float | None = None,
) -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        scenario=ScenarioConfig(
            command_prefixes=command_prefixes if command_prefixes is not None else [],
            timeout_s=timeout_s,
            aggregate_budget_s=aggregate_budget_s,
        ),
    )


def test_scenario_defaults_when_no_scenario_block() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.scenario.timeout_s is None
    assert cfg.scenario.aggregate_budget_s is None
    assert cfg.scenario.command_prefixes == []


def test_scenario_rejects_aggregate_budget_below_timeout() -> None:
    with pytest.raises(ValidationError):
        _cfg(timeout_s=900.0, aggregate_budget_s=120.0)


def test_scenario_rejects_resolved_fallback_trap_when_budget_unset() -> None:
    assert SCENARIO_TIMEOUT_DEFAULT < AGGREGATE_BUDGET_DEFAULT
    with pytest.raises(ValidationError):
        _cfg(timeout_s=AGGREGATE_BUDGET_DEFAULT + 1)


def test_scenario_accepts_equal_timeout_and_aggregate_budget() -> None:
    cfg = _cfg(timeout_s=900.0, aggregate_budget_s=900.0)
    assert cfg.scenario.timeout_s == 900.0
    assert cfg.scenario.aggregate_budget_s == 900.0


@pytest.mark.parametrize("raw", [0.0, -5.0, math.inf, math.nan])
def test_scenario_timeout_rejects_nonpositive_or_nonfinite(raw: float) -> None:
    with pytest.raises(ValidationError):
        _cfg(timeout_s=raw)


@pytest.mark.parametrize("raw", [0.0, -5.0, math.inf, math.nan])
def test_scenario_aggregate_budget_rejects_nonpositive_or_nonfinite(raw: float) -> None:
    with pytest.raises(ValidationError):
        _cfg(aggregate_budget_s=raw)


def test_scenario_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        EngineConfig(
            repo=RepoIdentity(owner="a", repo="b"),
            scenario={"bogus_field": True},  # type: ignore[arg-type]
        )


def test_scenario_round_trips_through_json_dump() -> None:
    cfg = _cfg(
        command_prefixes=[["xcodebuild"], ["tuist"]],
        timeout_s=900.0,
        aggregate_budget_s=900.0,
    )
    payload = cfg.model_dump(mode="json")
    reloaded = EngineConfig.model_validate(payload)
    assert reloaded == cfg


def test_scenario_absent_key_loads_defaults_on_pre_change_shaped_record() -> None:
    payload = EngineConfig(repo=RepoIdentity(owner="a", repo="b")).model_dump(mode="json")
    del payload["scenario"]
    reloaded = EngineConfig.model_validate(payload)
    assert reloaded.scenario == ScenarioConfig()

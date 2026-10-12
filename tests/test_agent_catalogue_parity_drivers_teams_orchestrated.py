from __future__ import annotations

from pathlib import Path

import pytest

from alan import agent_catalogue_teams_orchestrated as orchestrated
from alan import agent_key
from alan.engine_config import RoleConfig, default_engine_config
from alan.teams._orchestrated import OrchestratedTeam, parse_dispatch
from tests import agent_catalogue_parity as parity
from tests import agent_catalogue_parity_drivers_teams_orchestrated as drivers

_DEFAULT_CFG = default_engine_config()
_TEAM_PARAMS = pytest.mark.parametrize("team", drivers.TEAMS, ids=lambda team: team.name)


def _run(
    team: OrchestratedTeam, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> parity.LaunchRecorder:
    recorder = parity.install_recorder(monkeypatch)
    env = parity.ParityEnv(monkeypatch, tmp_path, recorder, _DEFAULT_CFG)
    drivers.driver_for(team).run(env)
    return recorder


def _launch_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, roles: dict[str, RoleConfig] | None
) -> list[tuple[object, ...]]:
    cfg = _DEFAULT_CFG if roles is None else _DEFAULT_CFG.model_copy(update={"roles": roles})
    recorder = parity.install_recorder(monkeypatch)
    env = parity.ParityEnv(monkeypatch, tmp_path, recorder, cfg)
    for driver in drivers.drivers():
        driver.run(env)
    return [
        (c.spec.agent, c.spec.model, c.spec.effort, c.spec.allowed_tools) for c in recorder.launches
    ]


def test_the_scripted_dispatch_passes_the_production_dispatch_parser() -> None:
    dispatched = parse_dispatch(drivers.dispatch_reply())
    assert [agent.role for agent in dispatched] == list(drivers.DISPATCHED_ROLES)
    assert all(agent.prompt == drivers.DISPATCH_FIELDS for agent in dispatched)


@_TEAM_PARAMS
def test_the_driver_covers_exactly_the_catalogue_keys_of_its_team(team: OrchestratedTeam) -> None:
    covered = {key.id for key in drivers.driver_for(team).covers(_DEFAULT_CFG)}
    catalogue = {
        agent.key.id
        for group in orchestrated.GROUPS
        for agent in group.resolve(_DEFAULT_CFG)
        if agent.team == team.name
    }
    assert covered == catalogue


def test_register_adds_one_driver_per_orchestrated_team(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[parity.Driver] = []
    monkeypatch.setattr(parity, "DRIVERS", registered)
    drivers.register()
    assert [driver.name for driver in registered] == [
        f"orchestrated/{team.name}" for team in drivers.TEAMS
    ]


@_TEAM_PARAMS
def test_the_driver_launches_every_role_through_the_real_run_path(
    team: OrchestratedTeam, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _run(team, monkeypatch, tmp_path)
    worker = agent_key.team_role(team.name, agent_key.WORKER_ROLE)
    expected = {agent_key.team_role(team.name, role) for role in drivers.launched_roles(team)}
    assert recorder.agents() == expected
    assert len(recorder.for_agent(worker)) == len(drivers.DISPATCHED_ROLES)
    assert len(recorder.launches) == len(expected) - 1 + len(drivers.DISPATCHED_ROLES)
    assert drivers.DISPATCH_INSTRUCTIONS in str(recorder.launches[0].user_text)
    assert [c.user_text for c in recorder.for_agent(worker)] == [
        drivers.DISPATCH_FIELDS
    ] * len(drivers.DISPATCHED_ROLES)


@_TEAM_PARAMS
def test_real_orchestrated_launches_run_with_no_working_directory(
    team: OrchestratedTeam, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _run(team, monkeypatch, tmp_path)
    assert recorder.launches
    assert {launch.spec.process.cwd for launch in recorder.launches} == {None}


def test_changing_engine_roles_changes_no_real_orchestrated_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinels = {
        name: RoleConfig(model=f"sentinel-{name}", effort="sentinel-effort")
        for name in _DEFAULT_CFG.roles
    }
    before = _launch_record(monkeypatch, tmp_path / "before", None)
    after = _launch_record(monkeypatch, tmp_path / "after", sentinels)
    assert before
    assert before == after

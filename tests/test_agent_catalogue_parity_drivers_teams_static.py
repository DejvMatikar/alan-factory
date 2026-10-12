from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from alan import agent_catalogue_teams, agent_key
from alan.agent_catalogue_helpers import ResolvedAgent, segments_from_text, to_entry
from alan.engine_config import EngineConfig, RoleConfig, default_engine_config
from alan.teams import brief_refine, maintenance
from tests import agent_catalogue_parity as parity
from tests import agent_catalogue_parity_drivers_teams_static as drivers

TEAM_NAMES = ["architecture", "brief_refine", "decomposer", "implementation_plan", "maintenance"]

ENGINE_VARIANTS: dict[str, Callable[[], EngineConfig]] = {
    "default": default_engine_config,
    "sentinel_roles": lambda: default_engine_config().model_copy(
        update={
            "roles": {
                **default_engine_config().roles,
                "judge": RoleConfig(model="haiku", effort="low"),
                "diff_reviewer": RoleConfig(model="opus", effort="medium"),
            }
        }
    ),
}


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> list[parity.Driver]:
    drivers_list: list[parity.Driver] = []
    monkeypatch.setattr(parity, "DRIVERS", drivers_list)
    drivers.register()
    return drivers_list


def static_agents(cfg: EngineConfig) -> list[ResolvedAgent]:
    return [
        agent for group in agent_catalogue_teams.GROUPS for agent in group.resolve(cfg)
    ]


def make_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: EngineConfig
) -> parity.ParityEnv:
    return parity.ParityEnv(monkeypatch, tmp_path, parity.install_recorder(monkeypatch), cfg)


def test_there_is_one_driver_per_static_team(registered):
    assert [driver.name for driver in registered] == [
        f"static_pipeline:{team}" for team in TEAM_NAMES
    ]


@pytest.mark.parametrize("variant", sorted(ENGINE_VARIANTS))
def test_the_drivers_cover_exactly_the_catalogue_agents(variant, registered):
    cfg = ENGINE_VARIANTS[variant]()
    covered = [key for driver in registered for key in driver.covers(cfg)]
    assert sorted(covered) == sorted(agent.key for agent in static_agents(cfg))
    assert len(covered) == len(set(covered))


@pytest.mark.parametrize("variant", sorted(ENGINE_VARIANTS))
def test_every_launch_matches_the_catalogue_agent(variant, registered, monkeypatch, tmp_path):
    cfg = ENGINE_VARIANTS[variant]()
    env = make_env(monkeypatch, tmp_path, cfg)
    for driver in registered:
        driver.run(env)
    for agent in static_agents(cfg):
        launches = env.recorder.for_agent(agent.key)
        assert len(launches) >= 2, agent.key.id
        prompt = to_entry(agent, []).prompt
        template = parity.template_regex(segments_from_text(agent.user_message, "user_message"))
        for launch in launches:
            assert parity.spec_differences(agent.spec, launch.spec, agent) == {}, agent.key.id
            assert parity.prompt_mismatch(agent, prompt, launch.spec) is None, agent.key.id
            assert launch.user_text is not None
            assert template.fullmatch(launch.user_text) is not None, agent.key.id


def test_drivers_restore_the_recorder_reply(registered, monkeypatch, tmp_path):
    env = make_env(monkeypatch, tmp_path, default_engine_config())
    before = env.recorder.reply
    for driver in registered:
        driver.run(env)
        assert env.recorder.reply is before


def test_the_second_launch_of_a_pipeline_role_carries_the_optional_context(
    registered, monkeypatch, tmp_path
):
    env = make_env(monkeypatch, tmp_path, default_engine_config())
    registered[0].run(env)
    first, second = env.recorder.for_agent(agent_key.team_role("architecture", "structure_mapper"))
    assert first.user_text is not None and second.user_text is not None
    assert "## User dispatch instructions" not in first.user_text
    assert "## User dispatch instructions\n\nfocus on the data layer" in second.user_text
    assert "Prior handover from brainstorming" in second.user_text


def test_the_decomposer_launch_carries_the_plan_context(registered, monkeypatch, tmp_path):
    env = make_env(monkeypatch, tmp_path, default_engine_config())
    registered[2].run(env)
    _, full = env.recorder.for_agent(agent_key.team_role("decomposer", "decomposer"))
    assert full.user_text is not None
    assert "## Files to edit (from plan)" in full.user_text
    assert "## Parent test scenarios" in full.user_text


def test_the_security_scanner_launch_carries_the_host_read_permissions(
    registered, monkeypatch, tmp_path
):
    env = make_env(monkeypatch, tmp_path, default_engine_config())
    registered[4].run(env)
    key = agent_key.team_role("maintenance", "security_scanner")
    assert all(
        launch.user_text is not None
        and launch.user_text.startswith("## HOST-READ SECRET FILE PERMISSIONS")
        for launch in env.recorder.for_agent(key)
    )


def test_every_maintenance_launch_grants_the_state_tree(registered, monkeypatch, tmp_path):
    env = make_env(monkeypatch, tmp_path, default_engine_config())
    registered[4].run(env)
    launches = [
        launch
        for role in maintenance.TEAM.roles
        for launch in env.recorder.for_agent(agent_key.team_role("maintenance", role.name))
    ]
    assert launches
    assert {launch.spec.add_dirs for launch in launches} == {
        (str(maintenance._state_tree_root()),)
    }


def test_the_brief_refiner_adds_the_workspace_directory_only_with_a_source_file(
    registered, monkeypatch, tmp_path
):
    cfg = default_engine_config()
    env = make_env(monkeypatch, tmp_path, cfg)
    key = agent_key.team_role("brief_refine", "brief_refiner")
    registered[1].run(env)
    *without_source, with_source = env.recorder.for_agent(key)
    assert {launch.spec.add_dirs for launch in without_source} == {()}
    (workspace_dir,) = with_source.spec.add_dirs
    assert (Path(workspace_dir) / brief_refine.SOURCE_FILE_NAME).is_file()
    (agent,) = [a for a in static_agents(cfg) if a.key == key]
    assert agent.spec.add_dirs == ()
    assert [d.label for d in agent.conditional_add_dirs] == ["WORKSPACE_DIR"]
    assert brief_refine.SOURCE_FILE_NAME in agent.conditional_add_dirs[0].condition
    template = parity.template_regex(segments_from_text(agent.user_message, "user_message"))
    assert with_source.user_text is not None
    assert "## Source conversation" in with_source.user_text
    assert "## Prior requirements" in with_source.user_text
    assert template.fullmatch(with_source.user_text) is not None

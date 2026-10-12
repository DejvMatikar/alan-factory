from __future__ import annotations

from pathlib import Path

import pytest

from alan import agent_catalogue_teams_impl, agent_key, engine_config
from alan.agent_catalogue_helpers import to_entry
from alan.engine_config import EngineConfig, RoleConfig, default_engine_config
from alan.launch_spec import appended_prompt
from tests import agent_catalogue_parity as parity
from tests import agent_catalogue_parity_drivers_teams_impl as drivers
from tests._agent_catalogue_impl_support import CONFIG_FACTORIES, custom_config, user_regex

DRIVER_NAMES = [
    "implementation/implementer",
    "implementation/drift_backstop",
    "implementation_fix/implementer",
    "verification",
]
IMPLEMENTER = agent_key.team_role("implementation", "implementer")
FIX = agent_key.team_role("implementation_fix", "implementer")
DIFF_REVIEWER = agent_key.team_role("verification", "diff_reviewer")
SCENARIO_RUNNER = agent_key.team_role("verification", "scenario_runner")
CLAUSE_AUDITOR = agent_key.team_role("verification", "clause_auditor")
JUDGE = agent_key.team_role("verification", "judge")
LAUNCH_COUNTS = {
    IMPLEMENTER: 2,
    agent_key.IMPLEMENTATION_DRIFT_BACKSTOP: 1,
    FIX: 2,
    DIFF_REVIEWER: 2,
    SCENARIO_RUNNER: 2,
    CLAUSE_AUDITOR: 2,
    JUDGE: 3,
}


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> list[parity.Driver]:
    collected: list[parity.Driver] = []
    monkeypatch.setattr(parity, "DRIVERS", collected)
    drivers.register()
    return collected


def run_all(
    registered: list[parity.Driver],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cfg: EngineConfig,
) -> parity.LaunchRecorder:
    recorder = parity.install_recorder(monkeypatch)
    for driver in registered:
        driver.run(parity.ParityEnv(monkeypatch, tmp_path, recorder, cfg))
    return recorder


def user_texts(recorder: parity.LaunchRecorder, key: agent_key.AgentKey) -> list[str]:
    return [str(launch.user_text) for launch in recorder.for_agent(key)]


def test_register_adds_one_driver_per_launch_family(registered: list[parity.Driver]) -> None:
    assert [driver.name for driver in registered] == DRIVER_NAMES


@pytest.mark.parametrize(
    "cfg", [default_engine_config(), custom_config()], ids=["default", "custom"]
)
def test_the_drivers_cover_exactly_the_catalogue_keys_of_the_implementation_family(
    registered: list[parity.Driver], cfg: EngineConfig
) -> None:
    covered = sorted(key.id for driver in registered for key in driver.covers(cfg))
    catalogue = sorted(
        agent.key.id for group in agent_catalogue_teams_impl.GROUPS for agent in group.resolve(cfg)
    )
    assert covered == catalogue


def test_the_drivers_launch_every_agent_through_the_real_run_path(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, default_engine_config())
    assert {key: len(recorder.for_agent(key)) for key in LAUNCH_COUNTS} == LAUNCH_COUNTS
    assert recorder.agents() == set(LAUNCH_COUNTS)


def test_the_implementation_drivers_exercise_both_user_text_shapes(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, default_engine_config())
    for key in (IMPLEMENTER, FIX):
        bare, full = user_texts(recorder, key)
        assert "## Your task" not in bare
        assert "## Your task: Wire the export" in full
        assert drivers.INSTRUCTIONS in full
        assert drivers.INSTRUCTIONS not in bare


def test_the_drift_backstop_driver_resumes_the_recorded_session(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, default_engine_config())
    (launch,) = recorder.for_agent(agent_key.IMPLEMENTATION_DRIFT_BACKSTOP)
    assert launch.spec.session_mode == "resumed"
    assert launch.spec.session_id == "recorded-session"
    assert launch.user_text is not None
    assert "DRIFT GATE BACKSTOP" in launch.user_text


def test_the_verification_driver_runs_both_retry_nodes(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, default_engine_config())
    judge = user_texts(recorder, JUDGE)
    assert ["## RETRY NUDGE" in text for text in judge] == [False, False, True]
    auditor = recorder.for_agent(CLAUSE_AUDITOR)
    assert [
        "TEST OWNERSHIP (RETRY)" in (launch.spec.prompt[0].text if launch.spec.prompt else "")
        for launch in auditor
    ] == [False, True]


def test_the_guided_checkout_adds_the_repository_guidance_to_the_generic_prompt(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, custom_config())
    for key in (IMPLEMENTER, FIX):
        plain, guided = [appended_prompt(launch.spec) or "" for launch in recorder.for_agent(key)]
        assert "REPOSITORY GUIDANCE" not in plain
        assert "REPOSITORY GUIDANCE" in guided


def launch_mismatches(cfg: EngineConfig, recorder: parity.LaunchRecorder) -> list[str]:
    problems: list[str] = []
    for group in agent_catalogue_teams_impl.GROUPS:
        for agent in group.resolve(cfg):
            launches = recorder.for_agent(agent.key)
            if not launches:
                problems.append(f"{agent.key.id}: never launched")
            for launch in launches:
                diffs = parity.spec_differences(agent.spec, launch.spec, agent)
                mismatch = parity.prompt_mismatch(agent, to_entry(agent, []).prompt, launch.spec)
                if diffs or mismatch or user_regex(agent).fullmatch(str(launch.user_text)) is None:
                    problems.append(f"{agent.key.id}: {diffs} {mismatch}")
    return problems


@pytest.mark.parametrize("config_name", ["custom", "bare", "overridden", "ios"])
def test_the_catalogue_matches_every_launch_under_further_engine_variants(
    registered: list[parity.Driver],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_name: str,
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    recorder = run_all(registered, monkeypatch, tmp_path, cfg)
    assert launch_mismatches(cfg, recorder) == []


def test_the_comparison_flags_agents_resolved_for_a_different_engine(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = run_all(registered, monkeypatch, tmp_path, default_engine_config())
    problems = launch_mismatches(custom_config(), recorder)
    assert {problem.split(": ")[0] for problem in problems} == {key.id for key in LAUNCH_COUNTS}


def test_the_drivers_honour_the_engine_config_they_are_given(
    registered: list[parity.Driver], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = default_engine_config()
    roles = {**cfg.roles, "implementer": RoleConfig(model="haiku", effort="low")}
    mutated = cfg.model_copy(update={"roles": roles})
    recorder = run_all(registered, monkeypatch, tmp_path, mutated)
    assert engine_config.lookup_by_slug(mutated.repo.slug) == mutated
    for key in (IMPLEMENTER, FIX):
        assert {(c.spec.model, c.spec.effort) for c in recorder.for_agent(key)} == {
            ("haiku", "low")
        }

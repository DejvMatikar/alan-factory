from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan import scenario_binding
from alan.handover import Handover, TestScenario
from alan.workspace import Meta, ScenarioBinding, Status, Task, TaskStatus, Workspace


def _scenario(
    scenario_id: str, *, when: str = "uv run pytest -k x", test_ref: str | None = None
) -> TestScenario:
    return TestScenario(
        id=scenario_id, description="d", given="g", when=when, then="t", test_ref=test_ref
    )


def _meta(**overrides: object) -> Meta:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "task_id": "ws-1",
        "title": "t",
        "description": "d",
        "status": Status.ACTIVE,
        "tasks": [],
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Meta(**base)  # type: ignore[arg-type]


def _ws(**meta_overrides: object) -> Workspace:
    meta = _meta(**meta_overrides)
    return Workspace(task_id=meta.task_id, path=Path("unused-workspace-path"), meta=meta)


def _handover(
    *, test_scenarios: list[TestScenario] | None = None, **overrides: object
) -> Handover:
    base: dict[str, object] = {
        "handover_id": "h1",
        "handover_type": "forward",
        "task_id": "ws-1",
        "from_team": "implementation",
        "from_invocation_id": "inv1",
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "ok",
        "test_scenarios": test_scenarios or [],
    }
    base.update(overrides)
    return Handover(**base)  # type: ignore[arg-type]


def test_bound_leaves_unbound_scenarios_untouched() -> None:
    meta = _meta()
    scenarios = [_scenario("s1")]
    assert scenario_binding.bound(meta, scenarios) == scenarios


def test_bound_overlays_when_only() -> None:
    meta = _meta(
        scenario_bindings={"s1": ScenarioBinding(when="uv run pytest -k y", reason="r")}
    )
    result = scenario_binding.bound(meta, [_scenario("s1", test_ref="old_ref")])
    assert result[0].when == "uv run pytest -k y"
    assert result[0].test_ref == "old_ref"


def test_bound_overlays_test_ref_only() -> None:
    meta = _meta(
        scenario_bindings={
            "s1": ScenarioBinding(test_ref="tests/test_new.py::test_y", reason="r")
        }
    )
    result = scenario_binding.bound(meta, [_scenario("s1", when="original when")])
    assert result[0].test_ref == "tests/test_new.py::test_y"
    assert result[0].when == ""


def test_bound_test_ref_only_repoint_changes_resolved_command() -> None:
    from alan.teams._scenario_runner import resolved_command

    meta = _meta(
        scenario_bindings={
            "s1": ScenarioBinding(test_ref="tests/test_new.py::test_y", reason="test moved")
        }
    )
    stale = _scenario("s1", when="uv run pytest tests/test_old.py::test_y")
    bound = scenario_binding.bound(meta, [stale])[0]
    assert resolved_command(bound) == "uv run --no-active pytest tests/test_new.py::test_y"


def test_bound_never_changes_id_or_then() -> None:
    meta = _meta(
        scenario_bindings={"s1": ScenarioBinding(when="uv run pytest -k y", reason="r")}
    )
    scenario = TestScenario(id="s1", description="d", given="g", when="orig", then="asserted")
    result = scenario_binding.bound(meta, [scenario])
    assert result[0].id == "s1"
    assert result[0].then == "asserted"


def test_waived_ids_only_counts_present_scenarios() -> None:
    meta = _meta(scenario_bindings={"ghost": ScenarioBinding(waived=True, reason="r")})
    assert scenario_binding.waived_ids(meta, [_scenario("s1")]) == frozenset()


def test_waived_ids_returns_waived_present_ids() -> None:
    meta = _meta(scenario_bindings={"s1": ScenarioBinding(waived=True, reason="r")})
    result = scenario_binding.waived_ids(meta, [_scenario("s1"), _scenario("s2")])
    assert result == frozenset({"s1"})


def test_waived_ids_excludes_non_waived_binding() -> None:
    meta = _meta(
        scenario_bindings={"s1": ScenarioBinding(when="uv run pytest -k y", reason="r")}
    )
    assert scenario_binding.waived_ids(meta, [_scenario("s1")]) == frozenset()


def test_runnable_excludes_waived_and_applies_overlay() -> None:
    meta = _meta(
        scenario_bindings={
            "s1": ScenarioBinding(waived=True, reason="known broken"),
            "s2": ScenarioBinding(when="uv run pytest -k z", reason="repointed"),
        }
    )
    result = scenario_binding.runnable(
        meta, [_scenario("s1"), _scenario("s2"), _scenario("s3")]
    )
    assert [s.id for s in result] == ["s2", "s3"]
    assert next(s for s in result if s.id == "s2").when == "uv run pytest -k z"


def test_base_scenarios_prefers_task_scenarios() -> None:
    task = Task(id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(tasks=[task])
    assert scenario_binding.base_scenarios(ws, None) == [task.scenarios[0]]


def test_base_scenarios_falls_back_to_prior_when_task_has_none() -> None:
    ws = _ws()
    prior = _handover(test_scenarios=[_scenario("s1")])
    assert scenario_binding.base_scenarios(ws, prior) == prior.test_scenarios


def test_base_scenarios_falls_back_to_prior_for_zero_subtask_workspace() -> None:
    ws = _ws(tasks=[])
    prior = _handover(test_scenarios=[_scenario("s1")])
    assert scenario_binding.base_scenarios(ws, prior) == prior.test_scenarios


def test_base_scenarios_empty_when_no_task_and_no_prior() -> None:
    ws = _ws()
    assert scenario_binding.base_scenarios(ws, None) == []


def test_base_scenarios_respects_explicit_task_id() -> None:
    root_task = Task(id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("root")])
    sub_task = Task(id="sub-a", status=TaskStatus.RUNNING, scenarios=[_scenario("sub")])
    ws = _ws(tasks=[root_task, sub_task])
    result = scenario_binding.base_scenarios(ws, None, task_id="sub-a")
    assert [s.id for s in result] == ["sub"]


def test_effective_scenarios_applies_binding_overlay_on_top_of_base() -> None:
    task = Task(id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(
        tasks=[task],
        scenario_bindings={"s1": ScenarioBinding(when="uv run pytest -k y", reason="r")},
    )
    result = scenario_binding.effective_scenarios(ws, None)
    assert result[0].when == "uv run pytest -k y"


def test_scenario_mode_missing_when_no_scenarios() -> None:
    ws = _ws()
    assert scenario_binding.scenario_mode(ws, None) == "MISSING"


def test_scenario_mode_run_when_scenarios_present_and_unwaived() -> None:
    task = Task(id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(tasks=[task])
    assert scenario_binding.scenario_mode(ws, None) == "RUN"


def test_scenario_mode_waived_when_task_scenarios_waived_legacy_flag() -> None:
    task = Task(
        id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")], scenarios_waived=True
    )
    ws = _ws(tasks=[task])
    assert scenario_binding.scenario_mode(ws, None) == "WAIVED"


def test_scenario_mode_waived_when_prior_scenarios_waived_legacy_flag() -> None:
    ws = _ws()
    prior = _handover(test_scenarios=[_scenario("s1")], scenarios_waived=True)
    assert scenario_binding.scenario_mode(ws, prior) == "WAIVED"


def test_scenario_mode_waived_when_dependency_bot_authorized() -> None:
    ws = _ws()
    prior = _handover(test_scenarios=[_scenario("s1")], dependency_bot_authorized=True)
    assert scenario_binding.scenario_mode(ws, prior) == "WAIVED"


def test_scenario_mode_run_when_only_some_ids_waived() -> None:
    task = Task(
        id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1"), _scenario("s2")]
    )
    ws = _ws(
        tasks=[task], scenario_bindings={"s1": ScenarioBinding(waived=True, reason="r")}
    )
    assert scenario_binding.scenario_mode(ws, None) == "RUN"


def test_scenario_mode_waived_when_every_present_id_waived() -> None:
    task = Task(
        id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1"), _scenario("s2")]
    )
    ws = _ws(
        tasks=[task],
        scenario_bindings={
            "s1": ScenarioBinding(waived=True, reason="r1"),
            "s2": ScenarioBinding(waived=True, reason="r2"),
        },
    )
    assert scenario_binding.scenario_mode(ws, None) == "WAIVED"


def test_scenario_mode_unknown_id_waiver_never_forces_waived() -> None:
    task = Task(id="ws-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(
        tasks=[task], scenario_bindings={"ghost": ScenarioBinding(waived=True, reason="r")}
    )
    assert scenario_binding.scenario_mode(ws, None) == "RUN"


def test_fix_bound_ids_only_includes_fix_rebind_written_bindings() -> None:
    meta = _meta(
        scenario_bindings={
            "s1": ScenarioBinding(when="a", reason="r", written_by="fix_rebind"),
            "s2": ScenarioBinding(when="b", reason="r", written_by="operator"),
        }
    )
    result = scenario_binding.fix_bound_ids(meta, [_scenario("s1"), _scenario("s2")])
    assert result == frozenset({"s1"})


def test_fix_bound_ids_excludes_ids_absent_from_scenarios() -> None:
    meta = _meta(
        scenario_bindings={"s1": ScenarioBinding(when="a", reason="r", written_by="fix_rebind")}
    )
    result = scenario_binding.fix_bound_ids(meta, [_scenario("s2")])
    assert result == frozenset()


def test_fix_bound_ids_empty_when_no_bindings() -> None:
    meta = _meta()
    assert scenario_binding.fix_bound_ids(meta, [_scenario("s1")]) == frozenset()


def test_all_base_scenarios_unions_across_tasks_deduped_first_seen_wins() -> None:
    t1 = Task(id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1"), _scenario("shared")])
    t2 = Task(
        id="b", status=TaskStatus.RUNNING,
        scenarios=[_scenario("shared", when="different"), _scenario("s2")],
    )
    ws = _ws(tasks=[t1, t2])
    result = scenario_binding.all_base_scenarios(ws, None)
    assert [s.id for s in result] == ["s1", "shared", "s2"]
    assert next(s for s in result if s.id == "shared").when == "uv run pytest -k x"


def test_all_base_scenarios_falls_back_to_prior_when_no_task_has_scenarios() -> None:
    ws = _ws(tasks=[Task(id="a", status=TaskStatus.RUNNING)])
    prior = _handover(test_scenarios=[_scenario("s1")])
    assert scenario_binding.all_base_scenarios(ws, prior) == prior.test_scenarios


def test_all_base_scenarios_empty_when_no_tasks_and_no_prior() -> None:
    ws = _ws()
    assert scenario_binding.all_base_scenarios(ws, None) == []


def test_all_scenario_mode_missing_when_no_scenarios() -> None:
    ws = _ws()
    assert scenario_binding.all_scenario_mode(ws, None) == "MISSING"


def test_all_scenario_mode_run_across_subtasks() -> None:
    t1 = Task(id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(tasks=[t1])
    assert scenario_binding.all_scenario_mode(ws, None) == "RUN"


def test_all_scenario_mode_waived_when_every_id_waived() -> None:
    t1 = Task(id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = _ws(
        tasks=[t1], scenario_bindings={"s1": ScenarioBinding(waived=True, reason="r")}
    )
    assert scenario_binding.all_scenario_mode(ws, None) == "WAIVED"


def test_all_scenario_mode_waived_when_prior_legacy_flag_set() -> None:
    ws = _ws()
    prior = _handover(test_scenarios=[_scenario("s1")], scenarios_waived=True)
    assert scenario_binding.all_scenario_mode(ws, prior) == "WAIVED"


def test_blanket_waived_ids_collects_only_scenarios_of_blanket_waived_tasks() -> None:
    waived_task = Task(
        id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")], scenarios_waived=True
    )
    running_task = Task(id="b", status=TaskStatus.RUNNING, scenarios=[_scenario("s2")])
    ws = _ws(tasks=[waived_task, running_task])
    assert scenario_binding.blanket_waived_ids(ws) == frozenset({"s1"})


def test_all_scenario_mode_waived_when_every_scenario_bearing_subtask_is_blanket_waived() -> None:
    t1 = Task(
        id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")], scenarios_waived=True
    )
    t2 = Task(
        id="b", status=TaskStatus.RUNNING, scenarios=[_scenario("s2")], scenarios_waived=True
    )
    ws = _ws(tasks=[t1, t2])
    assert scenario_binding.all_scenario_mode(ws, None) == "WAIVED"


def test_all_scenario_mode_runs_when_only_some_subtasks_are_blanket_waived() -> None:
    t1 = Task(
        id="a", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")], scenarios_waived=True
    )
    t2 = Task(id="b", status=TaskStatus.RUNNING, scenarios=[_scenario("s2")])
    ws = _ws(tasks=[t1, t2])
    assert scenario_binding.all_scenario_mode(ws, None) == "RUN"


def test_audit_file_matches_routes_workspaces_private_literal() -> None:
    from alan.routes import workspaces as routes_workspaces_mod

    assert (
        scenario_binding.AUDIT_FILE
        == routes_workspaces_mod._SCENARIO_OVERRIDE_AUDIT_FILE  # noqa: SLF001
    )

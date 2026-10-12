from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workspace as ws_mod
from alan.handover import InjectedTask
from alan.injection import (
    _content_hash,
    _get_parent_depth,
    _kahn_sort,
    _resolve_scenarios,
    _scenarios_for_task,
    _topological_sort,
    _validate_deps,
    plan_injection,
)
from alan.workspace import TaskStatus, TestScenario, create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_inj(
    local_id: str,
    team: str = "research",
    instructions: str | None = None,
    depends_on: list[str] | None = None,
) -> InjectedTask:
    return InjectedTask(
        id=local_id,
        team=team,
        instructions=instructions,
        depends_on=depends_on or [],
    )


def _write_task(ws_path: Path, tasks: list[dict]) -> None:  # type: ignore[type-arg]
    meta_path = ws_path / "meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = tasks
    meta_path.write_text(json.dumps(data), encoding="utf-8")


def test_plan_injection_empty_returns_noop(ws_root: Path) -> None:
    ws = create("empty-inj", "d", root=ws_root)
    plan = plan_injection(ws, ws.task_id, [])
    assert plan.to_add == []
    assert plan.overflow == []


def test_plan_injection_single_task_creates_content_hash_id(ws_root: Path) -> None:
    ws = create("single-inj", "d", root=ws_root)
    inj = _make_inj("subtask-a", team="research", instructions="do X")
    plan = plan_injection(ws, ws.task_id, [inj])
    assert len(plan.to_add) == 1
    task = plan.to_add[0]
    assert task.id.startswith("inj-")
    assert len(task.id) == len("inj-") + 16
    assert task.team == "research"
    assert task.instructions == "do X"
    assert task.injection_depth == 1
    assert task.depends_on == []
    assert task.status == TaskStatus.PENDING


def test_plan_injection_hash_is_stable(ws_root: Path) -> None:
    ws = create("hash-stable", "d", root=ws_root)
    inj = _make_inj("task-x", team="impl", instructions="work")
    plan1 = plan_injection(ws, ws.task_id, [inj])
    plan2 = plan_injection(ws, ws.task_id, [inj])
    assert plan1.to_add[0].id == plan2.to_add[0].id


def test_plan_injection_replay_is_idempotent(ws_root: Path) -> None:
    ws = create("replay-idem", "d", root=ws_root)
    inj = _make_inj("task-r", team="research")
    plan1 = plan_injection(ws, ws.task_id, [inj])
    assert len(plan1.to_add) == 1
    task_id = plan1.to_add[0].id
    _write_task(ws.path, [
        {
            "id": task_id, "status": "pending", "depends_on": [],
            "team": "research", "instructions": None, "injection_depth": 1,
        }
    ])
    ws2 = ws_mod.read(ws.task_id, root=ws_root)
    plan2 = plan_injection(ws2, ws.task_id, [inj])
    assert plan2.to_add == []
    assert plan2.overflow == []


def test_plan_injection_depth_cap_overflows(ws_root: Path) -> None:
    ws = create("depth-cap", "d", root=ws_root)
    _write_task(ws.path, [
        {
            "id": "parent-t", "status": "completed", "depends_on": [],
            "team": "research", "instructions": None, "injection_depth": 2,
        }
    ])
    ws2 = ws_mod.read(ws.task_id, root=ws_root)
    inj = _make_inj("child", team="research")
    plan = plan_injection(ws2, "parent-t", [inj])
    assert plan.to_add == []
    assert len(plan.overflow) == 1
    assert plan.overflow[0].id == "child"


def test_plan_injection_depth_1_parent_creates_depth_2_tasks(ws_root: Path) -> None:
    ws = create("depth-two", "d", root=ws_root)
    _write_task(ws.path, [
        {
            "id": "parent-d1", "status": "completed", "depends_on": [],
            "team": "research", "instructions": None, "injection_depth": 1,
        }
    ])
    ws2 = ws_mod.read(ws.task_id, root=ws_root)
    inj = _make_inj("child-d2", team="research")
    plan = plan_injection(ws2, "parent-d1", [inj])
    assert len(plan.to_add) == 1
    assert plan.to_add[0].injection_depth == 2


def test_plan_injection_cycle_overflows(ws_root: Path) -> None:
    ws = create("cycle-inj", "d", root=ws_root)
    inj_a = _make_inj("a", depends_on=["b"])
    inj_b = _make_inj("b", depends_on=["a"])
    plan = plan_injection(ws, ws.task_id, [inj_a, inj_b])
    assert plan.to_add == []
    assert len(plan.overflow) == 2


def test_plan_injection_unknown_dep_overflows(ws_root: Path) -> None:
    ws = create("unknown-dep", "d", root=ws_root)
    inj = _make_inj("task-u", depends_on=["no-such-task"])
    plan = plan_injection(ws, ws.task_id, [inj])
    assert plan.to_add == []
    assert len(plan.overflow) == 1


def test_plan_injection_dep_chain_resolved(ws_root: Path) -> None:
    ws = create("dep-chain", "d", root=ws_root)
    inj_a = _make_inj("a", team="research")
    inj_b = _make_inj("b", team="impl", depends_on=["a"])
    plan = plan_injection(ws, ws.task_id, [inj_a, inj_b])
    assert len(plan.to_add) == 2
    task_a = next(t for t in plan.to_add if t.team == "research")
    task_b = next(t for t in plan.to_add if t.team == "impl")
    assert task_b.depends_on == [task_a.id]
    assert task_a.depends_on == []


def test_plan_injection_dep_order_independent_of_input_order(ws_root: Path) -> None:
    ws = create("dep-order", "d", root=ws_root)
    inj_b = _make_inj("b", depends_on=["a"])
    inj_a = _make_inj("a")
    plan = plan_injection(ws, ws.task_id, [inj_b, inj_a])
    assert len(plan.to_add) == 2
    task_a_id = next(t.id for t in plan.to_add if not t.depends_on)
    task_b = next(t for t in plan.to_add if t.depends_on)
    task_ids = [t.id for t in plan.to_add]
    assert task_b.depends_on == [task_a_id]
    assert task_ids.index(task_a_id) < task_ids.index(task_b.id)


def test_content_hash_deterministic() -> None:
    h1 = _content_hash("parent", "research", "do X", ["dep-a"], 1)
    h2 = _content_hash("parent", "research", "do X", ["dep-a"], 1)
    assert h1 == h2


def test_content_hash_differs_on_team() -> None:
    h1 = _content_hash("parent", "research", None, [], 1)
    h2 = _content_hash("parent", "impl", None, [], 1)
    assert h1 != h2


def test_content_hash_differs_on_depth() -> None:
    h1 = _content_hash("parent", "research", None, [], 1)
    h2 = _content_hash("parent", "research", None, [], 2)
    assert h1 != h2


def test_content_hash_differs_on_instructions() -> None:
    h1 = _content_hash("parent", "research", "X", [], 1)
    h2 = _content_hash("parent", "research", "Y", [], 1)
    assert h1 != h2


def test_content_hash_none_instructions_same_as_empty_string() -> None:
    h1 = _content_hash("parent", "research", None, [], 1)
    h2 = _content_hash("parent", "research", "", [], 1)
    assert h1 == h2


def test_validate_deps_passes_empty_batch() -> None:
    _validate_deps([])


def test_validate_deps_passes_valid_deps() -> None:
    tasks = [_make_inj("a"), _make_inj("b", depends_on=["a"])]
    _validate_deps(tasks)


def test_validate_deps_raises_on_unknown_dep() -> None:
    tasks = [_make_inj("a", depends_on=["ghost"])]
    with pytest.raises(ValueError, match="unknown dependency"):
        _validate_deps(tasks)


def test_validate_deps_raises_on_duplicate_id() -> None:
    tasks = [_make_inj("a"), _make_inj("a")]
    with pytest.raises(ValueError, match="duplicate"):
        _validate_deps(tasks)


def test_kahn_sort_raises_on_cycle() -> None:
    in_degree = {"a": 1, "b": 1}
    adj = {"a": ["b"], "b": ["a"]}
    task_map = {"a": _make_inj("a"), "b": _make_inj("b")}
    with pytest.raises(ValueError, match="cycle"):
        _kahn_sort(in_degree, adj, task_map)


def test_topological_sort_single() -> None:
    tasks = [_make_inj("only")]
    result = _topological_sort(tasks)
    assert len(result) == 1
    assert result[0].id == "only"


def test_topological_sort_chain() -> None:
    tasks = [_make_inj("b", depends_on=["a"]), _make_inj("a")]
    result = _topological_sort(tasks)
    ids = [t.id for t in result]
    assert ids.index("a") < ids.index("b")


def test_topological_sort_diamond_covers_multi_parent_false_branch() -> None:
    tasks = [
        _make_inj("a"),
        _make_inj("b"),
        _make_inj("c", depends_on=["a", "b"]),
    ]
    result = _topological_sort(tasks)
    ids = [t.id for t in result]
    assert ids.index("a") < ids.index("c")
    assert ids.index("b") < ids.index("c")


def test_get_parent_depth_returns_zero_when_task_missing(ws_root: Path) -> None:
    ws = create("no-parent-t", "d", root=ws_root)
    assert _get_parent_depth(ws, "nonexistent") == 0


def test_get_parent_depth_returns_task_injection_depth(ws_root: Path) -> None:
    ws = create("parent-depth-t", "d", root=ws_root)
    _write_task(ws.path, [
        {
            "id": "t1", "status": "completed", "depends_on": [],
            "team": "r", "instructions": None, "injection_depth": 1,
        }
    ])
    ws2 = ws_mod.read(ws.task_id, root=ws_root)
    assert _get_parent_depth(ws2, "t1") == 1


def test_plan_injection_threads_dod_clauses_and_parent(ws_root: Path) -> None:
    from alan.workspace import TaskDoDClause

    ws = create("inj-dod", "d", root=ws_root)
    inj = InjectedTask(
        id="a",
        team="implementation",
        dod_clauses=[TaskDoDClause(text="x works", covers=["req-1"])],
    )
    plan = plan_injection(ws, ws.task_id, [inj])
    task = plan.to_add[0]
    assert task.parent_task_id == ws.task_id
    assert task.dod_clauses[0].covers == ["req-1"]
    assert task.planning_state.value == "detailed"


def test_dod_clauses_excluded_from_content_hash(ws_root: Path) -> None:
    from alan.workspace import TaskDoDClause

    ws = create("inj-hash", "d", root=ws_root)
    with_dod = InjectedTask(
        id="a", team="implementation", dod_clauses=[TaskDoDClause(text="x")]
    )
    without = InjectedTask(id="a", team="implementation")
    id_with = plan_injection(ws, ws.task_id, [with_dod]).to_add[0].id
    id_without = plan_injection(ws, ws.task_id, [without]).to_add[0].id
    assert id_with == id_without


def test_plan_injection_threads_files_to_task(ws_root: Path) -> None:
    ws = create("inj-files", "d", root=ws_root)
    inj = InjectedTask(id="a", team="implementation", files=["alan/x.py", "alan/y.py"])
    task = plan_injection(ws, ws.task_id, [inj]).to_add[0]
    assert task.files == ["alan/x.py", "alan/y.py"]


def test_plan_injection_files_default_empty(ws_root: Path) -> None:
    ws = create("inj-files-empty", "d", root=ws_root)
    inj = InjectedTask(id="a", team="implementation")
    task = plan_injection(ws, ws.task_id, [inj]).to_add[0]
    assert task.files == []


def test_plan_injection_files_excluded_from_content_hash(ws_root: Path) -> None:
    ws = create("inj-hash-files", "d", root=ws_root)
    with_files = InjectedTask(id="a", team="implementation", files=["alan/x.py"])
    without_files = InjectedTask(id="a", team="implementation")
    id_with = plan_injection(ws, ws.task_id, [with_files]).to_add[0].id
    id_without = plan_injection(ws, ws.task_id, [without_files]).to_add[0].id
    assert id_with == id_without


def test_plan_injection_threads_title(ws_root: Path) -> None:
    ws = create("inj-title", "d", root=ws_root)
    inj = InjectedTask(id="a", team="implementation", title="Add X endpoint")
    task = plan_injection(ws, ws.task_id, [inj]).to_add[0]
    assert task.title == "Add X endpoint"


def test_plan_injection_title_none_when_not_set(ws_root: Path) -> None:
    ws = create("inj-title-none", "d", root=ws_root)
    inj = InjectedTask(id="a", team="implementation")
    task = plan_injection(ws, ws.task_id, [inj]).to_add[0]
    assert task.title is None


def test_plan_injection_title_excluded_from_content_hash(ws_root: Path) -> None:
    ws = create("inj-title-hash", "d", root=ws_root)
    with_title = InjectedTask(id="a", team="implementation", title="Some title")
    without_title = InjectedTask(id="a", team="implementation")
    id_with = plan_injection(ws, ws.task_id, [with_title]).to_add[0].id
    id_without = plan_injection(ws, ws.task_id, [without_title]).to_add[0].id
    assert id_with == id_without


def _make_scenario(sid: str) -> TestScenario:
    return TestScenario(id=sid, description="d", given="g", when="uv run pytest", then="ok")


def test_resolve_scenarios_empty_ids_returns_empty() -> None:
    result = _resolve_scenarios([], parent_scenarios=[_make_scenario("s1")])
    assert result == []


def test_resolve_scenarios_none_parent_returns_none() -> None:
    result = _resolve_scenarios(["s1"], parent_scenarios=None)
    assert result is None


def test_resolve_scenarios_unknown_id_returns_none() -> None:
    result = _resolve_scenarios(["unknown"], parent_scenarios=[_make_scenario("s1")])
    assert result is None


def test_resolve_scenarios_valid_ids_returns_matching() -> None:
    s1 = _make_scenario("s1")
    s2 = _make_scenario("s2")
    result = _resolve_scenarios(["s2", "s1"], parent_scenarios=[s1, s2])
    assert result is not None
    assert [s.id for s in result] == ["s2", "s1"]


def test_plan_injection_threads_scenarios_to_task(ws_root: Path) -> None:
    ws = create("inj-scenarios", "d", root=ws_root)
    parent_scenarios = [_make_scenario("s1"), _make_scenario("s2")]
    inj = InjectedTask(id="a", team="implementation", scenarios=["s1"])
    task = plan_injection(ws, ws.task_id, [inj], parent_scenarios=parent_scenarios).to_add[0]
    assert [s.id for s in task.scenarios] == ["s1"]


def test_plan_injection_unknown_scenario_id_falls_back_to_empty(ws_root: Path) -> None:
    ws = create("inj-scenarios-unk", "d", root=ws_root)
    parent_scenarios = [_make_scenario("s1")]
    inj = InjectedTask(id="a", team="implementation", scenarios=["unknown"])
    task = plan_injection(ws, ws.task_id, [inj], parent_scenarios=parent_scenarios).to_add[0]
    assert task.scenarios == []


def test_plan_injection_threads_scenarios_waived(ws_root: Path) -> None:
    ws = create("inj-sw", "d", root=ws_root)
    inj = InjectedTask(id="a", team="implementation", scenarios_waived=True)
    task = plan_injection(ws, ws.task_id, [inj]).to_add[0]
    assert task.scenarios_waived is True


def test_plan_injection_scenarios_excluded_from_content_hash(ws_root: Path) -> None:
    ws = create("inj-hash-sc", "d", root=ws_root)
    parent_scenarios = [_make_scenario("s1")]
    with_sc = InjectedTask(id="a", team="implementation", scenarios=["s1"])
    without_sc = InjectedTask(id="a", team="implementation")
    id_with = plan_injection(
        ws, ws.task_id, [with_sc], parent_scenarios=parent_scenarios
    ).to_add[0].id
    id_without = plan_injection(ws, ws.task_id, [without_sc]).to_add[0].id
    assert id_with == id_without


def test_scenarios_for_task_returns_resolved_list() -> None:
    parent = [_make_scenario("s1")]
    assert [s.id for s in _scenarios_for_task(["s1"], parent)] == ["s1"]


def test_scenarios_for_task_falls_back_to_empty_on_none() -> None:
    assert _scenarios_for_task(["unknown"], None) == []


def test_plan_injection_scenarios_waived_excluded_from_content_hash(ws_root: Path) -> None:
    ws = create("inj-hash-sw", "d", root=ws_root)
    with_sw = InjectedTask(id="a", team="implementation", scenarios_waived=True)
    without_sw = InjectedTask(id="a", team="implementation")
    id_with = plan_injection(ws, ws.task_id, [with_sw]).to_add[0].id
    id_without = plan_injection(ws, ws.task_id, [without_sw]).to_add[0].id
    assert id_with == id_without

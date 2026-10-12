from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.queue import JobStatus, Queue
from alan.workspace import Task, TaskStatus, Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _write_multi_task_meta
from tests._dispatcher_support_teams import _make_handover, _NoopTeam


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_advance_dag_evaluates_requirements_when_all_completed(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.handover import TestScenario
    from alan.router import RouterDecision
    from alan.teams._scenario_runner import ScenarioResult
    from alan.workspace import Requirement

    run_called: list[str] = []

    def fake_run_scenarios(
        scenarios: list[TestScenario], *, cwd: object, aggregate_budget: object  # noqa: ARG001
    ) -> list[ScenarioResult]:
        run_called.extend(s.id for s in scenarios)
        return [
            ScenarioResult(
                scenario_id=s.id, status="PASS", diagnostic="", stdout_tail="",
                stderr_tail="", returncode=0, elapsed_s=0.01,
            )
            for s in scenarios
        ]

    import alan._dispatcher_reconcile as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", fake_run_scenarios)

    ws = workspace_mod.create(
        "dag-req-eval", "d", root=root,
        requirements=[Requirement(text="r", test_ref="test_foo")]
    )
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    handover = _make_handover(ws.task_id, from_team="implementation")
    handover = handover.model_copy(
        update={"test_scenarios": [TestScenario(
            id="test_foo",
            description="foo",
            given="g",
            when="w",
            then="t",
        )]}
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "a")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PASS"
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED
    assert run_called == ["test_foo"]


def test_advance_dag_skips_requirement_eval_when_no_handover(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "dag-no-handover", "d", root=root,
        requirements=[Requirement(text="r", test_ref="test_foo")]
    )
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "a", handover=None)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PENDING"
    assert reloaded.meta.status is workspace_mod.Status.FAILED


def test_advance_dag_skips_requirement_eval_when_requirements_none(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("dag-none-reqs", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    handover = _make_handover(ws.task_id, from_team="implementation")
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "a", handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is None
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_advance_dag_evaluates_requirements_for_single_task_workspace(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.handover import TestScenario
    from alan.teams._scenario_runner import ScenarioResult
    from alan.workspace import Requirement

    def fake_run_scenarios(
        scenarios: list[TestScenario], *, cwd: object, aggregate_budget: object  # noqa: ARG001
    ) -> list[ScenarioResult]:
        return [
            ScenarioResult(
                scenario_id=s.id, status="PASS", diagnostic="", stdout_tail="",
                stderr_tail="", returncode=0, elapsed_s=0.01,
            )
            for s in scenarios
        ]

    monkeypatch.setattr(dispatcher_mod, "run_scenarios", fake_run_scenarios)

    ws = workspace_mod.create(
        "dag-single-req", "d", root=root,
        requirements=[Requirement(text="r", test_ref="test_bar")]
    )
    handover = _make_handover(ws.task_id, from_team="implementation")
    handover = handover.model_copy(
        update={"test_scenarios": [TestScenario(
            id="test_bar",
            description="bar",
            given="g",
            when="w",
            then="t",
        )]}
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, ws.task_id, handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PASS"
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_advance_dag_handover_scenario_skipped_leaves_requirement_pending(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.handover import TestScenario
    from alan.teams._scenario_runner import ScenarioResult
    from alan.workspace import Requirement

    def fake_run_scenarios(
        scenarios: list[TestScenario], *, cwd: object, aggregate_budget: object  # noqa: ARG001
    ) -> list[ScenarioResult]:
        return [
            ScenarioResult(
                scenario_id=s.id, status="SKIPPED",
                diagnostic="pytest scenario not found (rc=4): ERROR: not found:",
                stdout_tail="", stderr_tail="", returncode=4, elapsed_s=0.1,
            )
            for s in scenarios
        ]

    monkeypatch.setattr(dispatcher_mod, "run_scenarios", fake_run_scenarios)

    ws = workspace_mod.create(
        "dag-skipped-handover", "d", root=root,
        requirements=[Requirement(text="r", test_ref="test_foo")]
    )
    handover = _make_handover(ws.task_id, from_team="implementation")
    handover = handover.model_copy(
        update={"test_scenarios": [TestScenario(
            id="test_foo", description="foo", given="g", when="w", then="t",
        )]}
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, ws.task_id, handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PENDING"


def test_all_task_scenarios_dedupes_by_id(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod

    scenario = {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"}
    ws = workspace_mod.create("scen-ws", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "status": "completed", "depends_on": [], "scenarios": [scenario]},
            {"id": "b", "status": "completed", "depends_on": [], "scenarios": [scenario]},
        ],
    )
    result = dispatcher_mod._all_task_scenarios(ws)  # noqa: SLF001
    assert [s.id for s in result] == ["s1"]


def test_drains_with_unresolvable_test_refs_none_requirements(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod

    ws = workspace_mod.create("drain-none", "d", root=root)
    ws = _write_multi_task_meta(ws, [{"id": "a", "status": "pending", "depends_on": []}])
    assert dispatcher_mod._drains_with_unresolvable_test_refs(ws, "a") is False  # noqa: SLF001


def test_drains_with_unresolvable_test_refs_remaining_tasks_not_drained(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "drain-remaining", "d", root=root,
        requirements=[Requirement(text="r", test_ref="missing-scenario")],
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "status": "pending", "depends_on": []},
            {"id": "b", "status": "pending", "depends_on": []},
        ],
    )
    assert dispatcher_mod._drains_with_unresolvable_test_refs(ws, "a") is False  # noqa: SLF001


def test_drains_with_unresolvable_test_refs_no_pending_refs(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "drain-satisfied", "d", root=root, requirements=[Requirement(text="r")]
    )
    ws = _write_multi_task_meta(ws, [{"id": "a", "status": "pending", "depends_on": []}])
    assert dispatcher_mod._drains_with_unresolvable_test_refs(ws, "a") is False  # noqa: SLF001


def test_drains_with_unresolvable_test_refs_no_matching_scenario_blocks(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "drain-unresolvable", "d", root=root,
        requirements=[Requirement(text="r", test_ref="ghost-scenario")],
    )
    ws = _write_multi_task_meta(ws, [{"id": "a", "status": "pending", "depends_on": []}])
    assert dispatcher_mod._drains_with_unresolvable_test_refs(ws, "a") is True  # noqa: SLF001


def test_drains_with_unresolvable_test_refs_matching_scenario_allows(root: Path) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "drain-resolvable", "d", root=root,
        requirements=[Requirement(text="r", test_ref="scenario-1")],
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "pending", "depends_on": [],
                "scenarios": [
                    {"id": "scenario-1", "description": "d", "given": "g", "when": "w", "then": "t"}
                ],
            },
        ],
    )
    assert dispatcher_mod._drains_with_unresolvable_test_refs(ws, "a") is False  # noqa: SLF001


def test_run_oob_requirement_scenarios_no_scenarios_returns_empty(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("oob-no-scen", "d", root=root)
    ws = _write_multi_task_meta(ws, [{"id": "a", "status": "completed", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d._run_oob_requirement_scenarios(ws) == []  # noqa: SLF001


def test_run_oob_requirement_scenarios_filters_to_pass_and_fail(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.teams._scenario_runner import ScenarioResult

    ws = workspace_mod.create("oob-scen", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    fake_results = [
        ScenarioResult(
            scenario_id="s1", status="PASS", diagnostic="", stdout_tail="",
            stderr_tail="", returncode=0, elapsed_s=0.1,
        ),
        ScenarioResult(
            scenario_id="s2", status="DOCUMENTED", diagnostic="", stdout_tail="",
            stderr_tail="", returncode=None, elapsed_s=0.0,
        ),
    ]
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", lambda *a, **kw: fake_results)
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
    assert result == [("s1", "PASS")]


def test_run_oob_requirement_scenarios_includes_advisory(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.teams._scenario_runner import ScenarioResult

    ws = workspace_mod.create("oob-scen-advisory", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    fake_results = [
        ScenarioResult(
            scenario_id="s1", status="ADVISORY", diagnostic="then predicate not found",
            stdout_tail="", stderr_tail="", returncode=0, elapsed_s=0.1,
        ),
    ]
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", lambda *a, **kw: fake_results)
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
    assert result == [("s1", "ADVISORY")]


def test_requirement_resolves_pass_when_bound_scenario_advisory(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.teams._scenario_runner import ScenarioResult
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "dag-advisory-pass", "d", root=root,
        requirements=[Requirement(text="r", test_ref="s1")],
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    fake_results = [
        ScenarioResult(
            scenario_id="s1", status="ADVISORY", diagnostic="then predicate not found",
            stdout_tail="", stderr_tail="", returncode=0, elapsed_s=0.1,
        ),
    ]
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", lambda *a, **kw: fake_results)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "a", handover=None)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PASS"


def test_run_oob_requirement_scenarios_filters_out_rc4_skipped(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.teams._scenario_runner import ScenarioResult

    ws = workspace_mod.create("oob-scen-rc4", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    fake_results = [
        ScenarioResult(
            scenario_id="s1", status="SKIPPED",
            diagnostic="pytest scenario not found (rc=4): ERROR: not found:",
            stdout_tail="", stderr_tail="", returncode=4, elapsed_s=0.1,
        ),
    ]
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", lambda *a, **kw: fake_results)
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
    assert result == []


def test_requirement_stays_pending_when_bound_scenario_rc4_skipped(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.teams._scenario_runner import ScenarioResult
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "dag-rc4-pending", "d", root=root,
        requirements=[Requirement(text="r", test_ref="s1")],
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    fake_results = [
        ScenarioResult(
            scenario_id="s1", status="SKIPPED",
            diagnostic="pytest scenario not found (rc=4): ERROR: not found:",
            stdout_tail="", stderr_tail="", returncode=4, elapsed_s=0.1,
        ),
    ]
    monkeypatch.setattr(dispatcher_mod, "run_scenarios", lambda *a, **kw: fake_results)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "a", handover=None)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PENDING"


def test_run_oob_requirement_scenarios_exception_returns_empty(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("oob-scen-boom", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )

    def boom(*a: object, **kw: object) -> object:
        raise RuntimeError("scenario runner exploded")

    monkeypatch.setattr(dispatcher_mod, "run_scenarios", boom)
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d._run_oob_requirement_scenarios(ws) == []  # noqa: SLF001


def test_needs_oob_scenario_probe_true_when_all_other_tasks_completed(
    root: Path,
) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "oob-probe-ready", "d", root=root, requirements=[Requirement(text="r")]
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "completed", "depends_on": []},
            {"id": "sub-b", "status": "pending", "depends_on": []},
        ],
    )
    assert dispatcher_mod._needs_oob_scenario_probe(ws, "sub-b") is True  # noqa: SLF001


def test_needs_oob_scenario_probe_false_when_sibling_failed_not_completed(
    root: Path,
) -> None:
    import alan._dispatcher_selfheal as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "oob-probe-gated", "d", root=root, requirements=[Requirement(text="r")]
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "failed", "depends_on": []},
            {"id": "sub-b", "status": "pending", "depends_on": []},
        ],
    )
    assert dispatcher_mod._needs_oob_scenario_probe(ws, "sub-b") is False  # noqa: SLF001


def test_complete_out_of_band_task_skips_probe_when_sibling_failed(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "oob-skip-probe", "d", root=root, requirements=[Requirement(text="r")]
    )
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "failed", "depends_on": []},
            {
                "id": "sub-b", "status": "pending", "depends_on": [],
                "dod_clauses": [{"text": "x", "covers": ["req-x"]}],
            },
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)

    def boom(*a: object, **kw: object) -> object:
        raise AssertionError("must not probe when a sibling task is FAILED, not COMPLETED")

    monkeypatch.setattr(d, "_run_oob_requirement_scenarios", boom)
    d.complete_out_of_band_task(ws.workspace_id, "sub-b", 9)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    task = next(t for t in reloaded.meta.tasks if t.id == "sub-b")
    assert task.status == workspace_mod.TaskStatus.COMPLETED


def test_maybe_evaluate_requirements_uses_precomputed_scenario_results(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "oob-precomputed", "d", root=root,
        requirements=[Requirement(text="r", test_ref="s1")],
    )
    ws = _write_multi_task_meta(ws, [{"id": "sub", "status": "completed", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)

    def boom(*a: object, **kw: object) -> object:
        raise AssertionError("must not recompute when results are precomputed")

    monkeypatch.setattr(d, "_run_oob_requirement_scenarios", boom)
    updated = d._maybe_evaluate_requirements(  # noqa: SLF001
        ws, None, precomputed_scenario_results=[("s1", "PASS")]
    )
    assert updated.meta.requirements is not None
    assert updated.meta.requirements[0].status == "PASS"


def test_complete_out_of_band_task_success_stamps_and_advances(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("oob-success", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "sub-a", "status": "pending", "depends_on": [],
                "dod_clauses": [{"text": "x", "covers": ["req-x"]}],
            },
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.complete_out_of_band_task(ws.workspace_id, "sub-a", 55)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    task = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert task.status == workspace_mod.TaskStatus.COMPLETED
    assert task.dod_clauses[0].evidence == "PR #55"
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_complete_out_of_band_task_enqueues_next_ready_subtask(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("oob-enqueue", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "pending", "depends_on": [], "team": "research"},
            {"id": "sub-b", "status": "pending", "depends_on": ["sub-a"], "team": "research"},
        ],
    )
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d.complete_out_of_band_task(ws.workspace_id, "sub-a", 55)
    jobs = queue.list_jobs(
        task_id="sub-b", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert len(jobs) == 1


def test_complete_out_of_band_task_unknown_subtask_raises(
    queue: Queue, root: Path
) -> None:
    from alan._dispatcher_base import OutOfBandCompletionError

    ws = workspace_mod.create("oob-unknown", "d", root=root)
    ws = _write_multi_task_meta(ws, [{"id": "sub-a", "status": "pending", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)
    with pytest.raises(OutOfBandCompletionError, match="not found"):
        d.complete_out_of_band_task(ws.workspace_id, "ghost", 1)


def test_complete_out_of_band_task_already_completed_raises(
    queue: Queue, root: Path
) -> None:
    from alan._dispatcher_base import OutOfBandCompletionError

    ws = workspace_mod.create("oob-terminal", "d", root=root)
    ws = _write_multi_task_meta(ws, [{"id": "sub-a", "status": "completed", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)
    with pytest.raises(OutOfBandCompletionError, match="already terminal"):
        d.complete_out_of_band_task(ws.workspace_id, "sub-a", 1)


def test_complete_out_of_band_task_already_failed_raises(
    queue: Queue, root: Path
) -> None:
    from alan._dispatcher_base import OutOfBandCompletionError

    ws = workspace_mod.create("oob-failed", "d", root=root)
    ws = _write_multi_task_meta(ws, [{"id": "sub-a", "status": "failed", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)
    with pytest.raises(OutOfBandCompletionError, match="already terminal"):
        d.complete_out_of_band_task(ws.workspace_id, "sub-a", 1)


def test_complete_out_of_band_task_unresolvable_test_ref_raises(
    queue: Queue, root: Path
) -> None:
    from alan._dispatcher_base import OutOfBandCompletionError
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "oob-unresolvable", "d", root=root,
        requirements=[Requirement(text="r", test_ref="ghost-scenario")],
    )
    ws = _write_multi_task_meta(ws, [{"id": "sub-a", "status": "pending", "depends_on": []}])
    d = Dispatcher(queue, {}, workspaces_root=root)
    with pytest.raises(OutOfBandCompletionError, match="test_ref"):
        d.complete_out_of_band_task(ws.workspace_id, "sub-a", 1)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    task = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert task.status == workspace_mod.TaskStatus.PENDING


def test_complete_oob_serializes_with_workspace_lock(
    root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_live = Task(id="live-a", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_oob = Task(id="oob-b", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("oob-serialize", "d", root=root, tasks=[task_live, task_oob])
    q = Queue(db_path=db_path, intra_workspace_cap=2)
    d = Dispatcher(q, {}, workspaces_root=root)

    real_advance = Dispatcher._advance_workspace_dag  # noqa: SLF001
    concurrent_count = [0]
    max_concurrent = [0]
    count_lock = threading.Lock()

    def tracked_advance(
        self: Dispatcher,
        ws_arg: Workspace,
        task_id: str,
        handover: Handover | None = None,
        *,
        evidence: str | None = None,
        precomputed_scenario_results: list[tuple[str, str]] | None = None,
    ) -> None:
        with count_lock:
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
        try:
            time.sleep(0.15)
            real_advance(
                self,
                ws_arg,
                task_id,
                handover,
                evidence=evidence,
                precomputed_scenario_results=precomputed_scenario_results,
            )
        finally:
            with count_lock:
                concurrent_count[0] -= 1

    monkeypatch.setattr(Dispatcher, "_advance_workspace_dag", tracked_advance)

    entry_barrier = threading.Barrier(2, timeout=5.0)
    errors: list[Exception] = []

    def live_worker_advance() -> None:
        try:
            entry_barrier.wait()
            lock = d._get_workspace_lock(ws.workspace_id)  # noqa: SLF001
            with lock:
                fresh = workspace_mod.read(ws.task_id, root=root)
                d._advance_workspace_dag(fresh, "live-a")  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def oob_complete() -> None:
        try:
            entry_barrier.wait()
            d.complete_out_of_band_task(ws.workspace_id, "oob-b", 7)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=live_worker_advance)
    t2 = threading.Thread(target=oob_complete)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "live worker thread did not finish within timeout"
    assert not t2.is_alive(), "oob completion thread did not finish within timeout"
    assert not errors
    assert max_concurrent[0] == 1, (
        "DAG advance overlapped between the live worker and the out-of-band completion"
    )

    final = workspace_mod.read(ws.task_id, root=root)
    statuses = {t.id: t.status for t in final.meta.tasks}
    assert statuses["live-a"] == TaskStatus.COMPLETED
    assert statuses["oob-b"] == TaskStatus.COMPLETED
    q.close()


def test_maybe_evaluate_requirements_filters_out_waived_scenario_via_binding(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as reconcile_mod
    from alan.handover import TestScenario
    from alan.workspace import Requirement, ScenarioBinding

    called: list[str] = []

    def boom(*a: object, **kw: object) -> object:
        called.append("called")
        raise AssertionError("must not run a scenario that a workspace-level binding waived")

    monkeypatch.setattr(reconcile_mod, "run_scenarios", boom)

    ws = workspace_mod.create(
        "waived-req", "d", root=root,
        requirements=[Requirement(text="r", test_ref="s1")],
    )
    ws = workspace_mod.update_scenario_binding(
        ws, "s1", ScenarioBinding(waived=True, reason="known broken")
    )
    handover = _make_handover(ws.task_id, from_team="implementation")
    handover = handover.model_copy(
        update={"test_scenarios": [TestScenario(
            id="s1", description="d", given="g", when="w", then="t",
        )]}
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    updated = d._maybe_evaluate_requirements(ws, handover)  # noqa: SLF001
    assert called == []
    assert updated.meta.requirements is not None
    assert updated.meta.requirements[0].status == "PENDING"


def test_run_oob_requirement_scenarios_filters_out_waived_via_binding(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_reconcile as reconcile_mod
    from alan.workspace import ScenarioBinding

    ws = workspace_mod.create("oob-waived-binding", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "a", "status": "completed", "depends_on": [],
                "scenarios": [
                    {"id": "s1", "description": "d", "given": "g", "when": "w", "then": "t"},
                ],
            },
        ],
    )
    ws = workspace_mod.update_scenario_binding(
        ws, "s1", ScenarioBinding(waived=True, reason="known broken")
    )

    def boom(*a: object, **kw: object) -> object:
        raise AssertionError("must not run a scenario that a workspace-level binding waived")

    monkeypatch.setattr(reconcile_mod, "run_scenarios", boom)
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d._run_oob_requirement_scenarios(ws) == []  # noqa: SLF001

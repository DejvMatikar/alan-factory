from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alan import _dispatcher_scenario_rebind as rebind_mod
from alan import process_runner, scenario_binding
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.handover import Handover, TestScenario
from alan.queue import Queue
from alan.teams._scenario_runner import run_scenario, run_scenarios
from alan.workspace import (
    ScenarioBinding,
    Task,
    TaskDoDClause,
    TaskStatus,
    Workspace,
    update_scenario_binding,
)


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def _stale_and_new_commands(tmp_path: Path) -> tuple[str, str]:
    missing = tmp_path / "missing-proof.txt"
    real = tmp_path / "real-proof.txt"
    real.write_text("proof content", encoding="utf-8")
    return f"cat {missing}", f"cat {real}"


def _scenario(cmd: str, sid: str = "s1") -> TestScenario:
    return TestScenario(id=sid, description="d", given="g", when=cmd, then="")


def test_binding_flips_run_scenario_from_fail_to_pass(tmp_path: Path) -> None:
    stale, new = _stale_and_new_commands(tmp_path)
    stale_result = run_scenario(_scenario(stale), cwd=".", timeout=10.0)
    assert stale_result.status == "FAIL"

    meta = workspace_mod.create("rebind-proxy", "d", root=tmp_path / "ws").meta
    bound_meta = meta.model_copy(
        update={"scenario_bindings": {"s1": ScenarioBinding(when=new, reason="test moved")}}
    )
    bound = scenario_binding.bound(bound_meta, [_scenario(stale)])[0]
    new_result = run_scenario(bound, cwd=".", timeout=10.0)
    assert new_result.status == "PASS"
    assert new_result.returncode == 0


def test_binding_flips_self_heal_scenario_run(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None
    )
    stale, new = _stale_and_new_commands(tmp_path)
    sibling = Task(
        id="t1", team="implementation", status=TaskStatus.COMPLETED, depends_on=[],
        dod_clauses=[TaskDoDClause(text="seam", evidence="PR #498")],
    )
    leaf = Task(
        id="t2", team="implementation", status=TaskStatus.PENDING, depends_on=["t1"],
        dod_clauses=[TaskDoDClause(text="collision", evidence=None)],
        scenarios=[_scenario(stale)],
    )
    ws = workspace_mod.create(
        "rebind-selfheal", "d", root=tmp_path / "ws", tasks=[sibling, leaf]
    )
    ws = update_scenario_binding(ws, "s1", ScenarioBinding(when=new, reason="test moved"))

    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws")
    task = next(t for t in ws.meta.tasks if t.id == "t2")
    results = d._run_self_heal_scenarios(ws, task)  # noqa: SLF001
    assert len(results) == 1
    assert results[0].status == "PASS"


def _requirement_ws(tmp_path: Path, cmd: str, *, decomposed: bool) -> Workspace:
    from alan.workspace import Requirement

    if decomposed:
        task = Task(
            id="a", status=TaskStatus.COMPLETED, depends_on=[], scenarios=[_scenario(cmd)]
        )
        return workspace_mod.create(
            "rebind-reconcile-decomposed", "d", root=tmp_path / "ws", tasks=[task],
            requirements=[Requirement(text="r", test_ref="s1")],
        )
    return workspace_mod.create(
        "rebind-reconcile-atomic", "d", root=tmp_path / "ws",
        requirements=[Requirement(text="r", test_ref="s1")],
    )


@pytest.mark.parametrize("decomposed", [False, True])
def test_binding_flips_reconcile_scenario_run(
    tmp_path: Path, queue: Queue, decomposed: bool
) -> None:
    stale, new = _stale_and_new_commands(tmp_path)
    ws = _requirement_ws(tmp_path, stale, decomposed=decomposed)
    ws = update_scenario_binding(ws, "s1", ScenarioBinding(when=new, reason="test moved"))
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws")

    if decomposed:
        results = d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
        assert results == [("s1", "PASS")]
        return

    handover = Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation",
        from_invocation_id="inv1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
        test_scenarios=[_scenario(stale)],
    )
    updated_ws = d._maybe_evaluate_requirements(ws, handover)  # noqa: SLF001
    requirements = updated_ws.meta.requirements
    assert requirements is not None
    assert requirements[0].status == "PASS"


def test_fix_role_rebind_accept_path_end_to_end_without_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    run_subprocess_mock = MagicMock(
        side_effect=AssertionError("acceptance must not run a subprocess")
    )
    monkeypatch.setattr(process_runner, "run_subprocess", run_subprocess_mock)

    stale, new = _stale_and_new_commands(tmp_path)
    ws = workspace_mod.create("rebind-e2e-accept", "d", root=tmp_path / "ws")
    handover = Handover(
        handover_id="h1",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"```yaml scenario_rebind.yaml\ns1: {new}\n```",
        test_scenarios=[_scenario(stale)],
    )

    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)

    assert outcome.accepted == {"s1": new}
    run_subprocess_mock.assert_not_called()
    persisted = ws2.meta.scenario_bindings["s1"]
    assert persisted.when == new
    assert persisted.written_by == "fix_rebind"

    monkeypatch.undo()
    bound = scenario_binding.bound(ws2.meta, [_scenario(stale)])[0]
    assert bound.when == new
    result = run_scenario(bound, cwd=".", timeout=10.0)
    assert result.status == "PASS"


def test_fix_role_rebind_accept_then_fail_closed_when_bound_command_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as subprocess_mod

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    stale = _stale_and_new_commands(tmp_path)[0]
    corrected_but_still_unresolvable = "uv run --no-active pytest tests/test_missing_xyz.py"
    ws = workspace_mod.create("rebind-e2e-fail-closed", "d", root=tmp_path / "ws")
    handover = Handover(
        handover_id="h1",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=(
            "```yaml scenario_rebind.yaml\n"
            f"s1: {corrected_but_still_unresolvable}\n"
            "```"
        ),
        test_scenarios=[_scenario(stale)],
    )

    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": corrected_but_still_unresolvable}

    effective = scenario_binding.bound(ws2.meta, [_scenario(stale)])
    strict_ids = scenario_binding.fix_bound_ids(ws2.meta, effective)
    assert strict_ids == frozenset({"s1"})

    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda cmd, *, cwd, timeout: subprocess_mod.CompletedProcess(  # noqa: ARG005
            cmd, 4, stdout="", stderr="ERROR: not found: tests/test_missing_xyz.py"
        ),
    )

    lenient_result = run_scenario(effective[0], cwd=".", timeout=10.0)
    assert lenient_result.status == "SKIPPED"

    strict_results = run_scenarios(effective, cwd=".", strict_ids=strict_ids)
    assert strict_results[0].status == "FAIL"

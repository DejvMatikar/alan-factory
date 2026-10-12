from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import process_runner, scenario_binding
from alan.handover import Decision, Handover, save_handover
from alan.teams._scenario_runner import run_scenarios
from alan.workspace import ScenarioBinding, TestScenario, Workspace, create, update_scenario_binding


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _seed_handover(ws: Workspace, scenarios: list[TestScenario]) -> Handover:
    handover = Handover(
        handover_id=f"handover_{ws.task_id}_001",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_plan",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(id="d1", statement="s", rationale="r", confidence=0.5, reversibility="low")
        ],
        full_synthesis="ok",
        test_scenarios=scenarios,
    )
    save_handover(ws, handover)
    return handover


def test_stale_binding_corrected_end_to_end_on_zero_subtask_workspace(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = "tests/test_old_location.py::test_behaviour_x"
    new_path = "tests/test_new_location.py::test_behaviour_x"
    ws = create("H8.1b Repro", "d", root=ws_root)
    assert ws.meta.tasks == []
    stale = TestScenario(
        id="behaviour_x", description="behaviour X still works",
        given="the code exists", when=f"uv run pytest {old_path}", then="1 passed",
    )
    handover = _seed_handover(ws, [stale])

    def fake_run(
        tokens: Sequence[str], *, cwd: str | None = None, timeout: float | None = None,
        env: object = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        joined = " ".join(tokens)
        if old_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=4, stdout="",
                stderr=f"ERROR: not found: {old_path}",
            )
        if new_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=0, stdout="1 passed", stderr=""
            )
        return subprocess.CompletedProcess(
            list(tokens), returncode=1, stdout="", stderr="unexpected command"
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)

    before_base = scenario_binding.base_scenarios(ws, handover)
    before = run_scenarios(scenario_binding.runnable(ws.meta, before_base), cwd=str(ws_root))
    assert before[0].status == "SKIPPED"
    assert before[0].returncode == 4

    ws = update_scenario_binding(
        ws, "behaviour_x",
        ScenarioBinding(when=f"uv run pytest {new_path}", reason="test moved by PR's own reorg"),
    )

    after_base = scenario_binding.base_scenarios(ws, handover)
    after_effective = scenario_binding.bound(ws.meta, after_base)
    assert after_effective[0].when == f"uv run pytest {new_path}"
    after = run_scenarios(scenario_binding.runnable(ws.meta, after_base), cwd=str(ws_root))
    assert after[0].status == "PASS"
    assert after[0].scenario_id == "behaviour_x"


def test_stale_binding_corrected_via_test_ref_only_end_to_end(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = "tests/test_old_location.py::test_behaviour_y"
    new_path = "tests/test_new_location.py::test_behaviour_y"
    ws = create("H8.1b Test Ref Only Repro", "d", root=ws_root)
    stale = TestScenario(
        id="behaviour_y", description="behaviour Y still works",
        given="the code exists", when=f"uv run pytest {old_path}", then="1 passed",
    )
    handover = _seed_handover(ws, [stale])

    def fake_run(
        tokens: Sequence[str], *, cwd: str | None = None, timeout: float | None = None,
        env: object = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        joined = " ".join(tokens)
        if old_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=4, stdout="",
                stderr=f"ERROR: not found: {old_path}",
            )
        if new_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=0, stdout="1 passed", stderr=""
            )
        return subprocess.CompletedProcess(
            list(tokens), returncode=1, stdout="", stderr="unexpected command"
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)

    ws = update_scenario_binding(
        ws, "behaviour_y",
        ScenarioBinding(test_ref=new_path, reason="test moved by PR's own reorg"),
    )

    after_base = scenario_binding.base_scenarios(ws, handover)
    after_effective = scenario_binding.bound(ws.meta, after_base)
    assert after_effective[0].when == ""
    assert after_effective[0].test_ref == new_path
    after = run_scenarios(scenario_binding.runnable(ws.meta, after_base), cwd=str(ws_root))
    assert after[0].status == "PASS"
    assert after[0].scenario_id == "behaviour_y"


def test_waiving_one_scenario_never_masks_a_genuinely_failing_sibling(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Genuine Failure WS", "d", root=ws_root)
    unsatisfiable = TestScenario(
        id="unsatisfiable_by_construction", description="cannot ever pass",
        given="g", when="uv run pytest -k unsatisfiable", then="never emitted",
    )
    broken = TestScenario(
        id="genuinely_broken_behaviour", description="a real regression",
        given="g", when="uv run pytest -k broken", then="1 passed",
    )
    handover = _seed_handover(ws, [unsatisfiable, broken])

    def fake_run(
        tokens: Sequence[str], *, cwd: str | None = None, timeout: float | None = None,
        env: object = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        if "broken" in " ".join(tokens):
            return subprocess.CompletedProcess(
                list(tokens), returncode=1, stdout="1 failed", stderr=""
            )
        return subprocess.CompletedProcess(
            list(tokens), returncode=0, stdout="never emitted", stderr=""
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)

    ws = update_scenario_binding(
        ws, "unsatisfiable_by_construction",
        ScenarioBinding(waived=True, reason="unsatisfiable by construction, see design doc"),
    )

    base = scenario_binding.base_scenarios(ws, handover)
    mode = scenario_binding.scenario_mode(ws, handover)
    assert mode == "RUN"
    runnable = scenario_binding.runnable(ws.meta, base)
    assert [s.id for s in runnable] == ["genuinely_broken_behaviour"]

    results = run_scenarios(runnable, cwd=str(ws_root))
    assert results[0].status == "FAIL"
    assert results[0].scenario_id == "genuinely_broken_behaviour"


def test_repointed_scenario_that_fails_still_blocks(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = "tests/test_old_location.py::test_behaviour_z"
    new_path = "tests/test_new_location.py::test_behaviour_z"
    ws = create("Repoint To Still-Failing Repro", "d", root=ws_root)
    stale = TestScenario(
        id="behaviour_z", description="behaviour Z still works",
        given="the code exists", when=f"uv run pytest {old_path}", then="1 passed",
    )
    handover = _seed_handover(ws, [stale])

    def fake_run(
        tokens: Sequence[str], *, cwd: str | None = None, timeout: float | None = None,
        env: object = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        joined = " ".join(tokens)
        if old_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=4, stdout="",
                stderr=f"ERROR: not found: {old_path}",
            )
        if new_path in joined:
            return subprocess.CompletedProcess(
                list(tokens), returncode=1, stdout="1 failed", stderr=""
            )
        return subprocess.CompletedProcess(
            list(tokens), returncode=1, stdout="", stderr="unexpected command"
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)

    ws = update_scenario_binding(
        ws, "behaviour_z",
        ScenarioBinding(when=f"uv run pytest {new_path}", reason="test moved by PR's own reorg"),
    )

    base = scenario_binding.base_scenarios(ws, handover)
    effective = scenario_binding.bound(ws.meta, base)
    assert effective[0].when == f"uv run pytest {new_path}"
    results = run_scenarios(scenario_binding.runnable(ws.meta, base), cwd=str(ws_root))
    assert results[0].status == "FAIL"
    assert results[0].scenario_id == "behaviour_z"

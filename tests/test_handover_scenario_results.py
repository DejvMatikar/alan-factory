from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alan.handover import Handover
from alan.teams import _verification_scenarios as _scen_mod
from alan.teams._base import build_forward_handover
from alan.teams._scenario_runner import ScenarioResult
from alan.workspace import create


def _result(scenario_id: str, status: str, diagnostic: str = "") -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        status=status,  # type: ignore[arg-type]
        diagnostic=diagnostic,
        stdout_tail="",
        stderr_tail="",
        returncode=0,
        elapsed_s=0.01,
    )


def test_to_scenario_outcomes_empty() -> None:
    assert _scen_mod._to_scenario_outcomes([]) == []  # noqa: SLF001


def test_to_scenario_outcomes_mixed_status() -> None:
    results = [
        _result("s1", "PASS"),
        _result("s2", "FAIL", "exit code 1"),
        _result("s3", "SKIPPED", "aggregate budget exhausted"),
        _result("s4", "DOCUMENTED", "prose when field: documentation-only scenario"),
    ]
    outcomes = _scen_mod._to_scenario_outcomes(results)  # noqa: SLF001
    assert [(o.scenario_id, o.status) for o in outcomes] == [
        ("s1", "PASS"),
        ("s2", "FAIL"),
        ("s3", "SKIPPED"),
        ("s4", "DOCUMENTED"),
    ]
    assert outcomes[1].diagnostic == "exit code 1"


def test_to_scenario_outcomes_truncates_oversized_diagnostic() -> None:
    long_diagnostic = "x" * 5000
    outcomes = _scen_mod._to_scenario_outcomes(  # noqa: SLF001
        [_result("s1", "FAIL", long_diagnostic)]
    )
    assert len(outcomes[0].diagnostic) == _scen_mod._DIAGNOSTIC_MAX  # noqa: SLF001
    assert outcomes[0].diagnostic == long_diagnostic[: _scen_mod._DIAGNOSTIC_MAX]  # noqa: SLF001


def test_to_scenario_outcomes_diagnostic_exactly_at_cap_unchanged() -> None:
    diagnostic = "x" * _scen_mod._DIAGNOSTIC_MAX  # noqa: SLF001
    outcomes = _scen_mod._to_scenario_outcomes([_result("s1", "FAIL", diagnostic)])  # noqa: SLF001
    assert outcomes[0].diagnostic == diagnostic


def _impl_handover_with_scenario_results(task_id: str) -> Handover:
    payload: dict[str, Any] = {
        "handover_id": "handover_impl_001",
        "handover_type": "forward",
        "task_id": task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["pr_merge"],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "verification ran",
        "scenario_results": [{"scenario_id": "s1", "status": "PASS", "diagnostic": ""}],
    }
    return Handover(**payload)


def test_build_forward_handover_never_carries_scenario_results(tmp_path: Path) -> None:
    ws = create("fwd-scenario-results", "d", root=tmp_path / "ws")
    prior = _impl_handover_with_scenario_results(ws.task_id)
    assert prior.scenario_results

    forward = build_forward_handover(
        ws,
        from_team="pr_merge",
        to_team_suggestions=[],
        summary="merged",
        prior=prior,
        invocation_id="inv_002",
    )

    assert forward.scenario_results == []


def test_non_verification_team_handover_scenario_results_empty(tmp_path: Path) -> None:
    ws = create("non-verif-scenario-results", "d", root=tmp_path / "ws")
    handover = build_forward_handover(
        ws,
        from_team="implementation",
        to_team_suggestions=["verification"],
        summary="implementation done",
        prior=None,
        invocation_id="inv_001",
    )
    assert handover.scenario_results == []

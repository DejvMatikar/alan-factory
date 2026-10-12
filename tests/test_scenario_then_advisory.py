from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from alan import process_runner
from alan.handover import ScenarioOutcome, TestScenario
from alan.teams import _changed_e2e
from alan.teams import implementation_fix as _implementation_fix
from alan.teams import implementation_plan as _implementation_plan
from alan.teams import verification as _verification
from alan.teams._scenario_runner import (
    NON_BLOCKING_STATUSES,
    _fail_diagnostic,
    _run_outcome,
    run_scenario,
)


def _scenario(*, when: str = "uv run pytest", then: str = "1 passed") -> TestScenario:
    return TestScenario(
        id="s1", description="desc", given="given", when=when, then=then
    )


def _fake_run(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _run(
        cmd: list[str], *, cwd: str | None, timeout: float  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return _run


def test_run_outcome_rc0_then_present_is_pass() -> None:
    assert _run_outcome(0, "1 passed", "1 passed\n") == ("PASS", "")


def test_run_outcome_rc0_then_empty_is_pass() -> None:
    assert _run_outcome(0, "", "anything at all") == ("PASS", "")


def test_run_outcome_rc0_then_absent_is_advisory_not_fail() -> None:
    status, diag = _run_outcome(0, "1 passed", "0 passed, 1 failed")
    assert status == "ADVISORY"
    assert "1 passed" in diag


def test_run_outcome_nonzero_rc_is_fail_even_with_matching_then() -> None:
    status, diag = _run_outcome(1, "1 passed", "1 passed")
    assert status == "FAIL"
    assert diag == "exit code 1"


def test_run_outcome_rc5_no_tests_collected_is_fail() -> None:
    status, diag = _run_outcome(5, "1 passed", "collected 0 items")
    assert status == "FAIL"
    assert "no tests" in diag


def test_run_scenario_rc2_collection_signature_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_run(returncode=2, stdout="errors during collection")
    )
    result = run_scenario(_scenario(then="1 passed"), cwd=".", timeout=10.0)
    assert result.status == "SKIPPED"


def test_run_scenario_rc4_not_found_signature_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        _fake_run(returncode=4, stderr="ERROR: not found: tests/test_x.py::test_missing"),
    )
    result = run_scenario(_scenario(then="1 passed"), cwd=".", timeout=10.0)
    assert result.status == "SKIPPED"


def test_a_genuine_fail_still_blocks_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_run(returncode=1, stdout="1 failed")
    )
    result = run_scenario(_scenario(then="1 passed"), cwd=".", timeout=10.0)
    assert result.status == "FAIL"
    statuses = ["ADVISORY", "PASS", result.status]
    verdict = "REQUEST_CHANGES" if "FAIL" in statuses else "APPROVE"
    assert verdict == "REQUEST_CHANGES"


def test_advisory_diagnostic_is_byte_identical_to_fail_path() -> None:
    _status, diag = _run_outcome(0, "1 passed", "0 passed, 1 failed")
    assert diag == _fail_diagnostic(0, "1 passed")


def test_run_scenario_end_to_end_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_run(stdout="0 passed, 1 failed")
    )
    result = run_scenario(_scenario(then="1 passed"), cwd=".", timeout=10.0)
    assert result.status == "ADVISORY"
    assert result.returncode == 0


def _playwright_green_tail() -> str:
    return (
        "Running 3 tests using 1 worker\n\n"
        "  3 passed (2.1s)\n\n"
        "To open last HTML report run:\n\n"
        "  npx playwright show-report\n"
    )


def test_changed_e2e_scenario_green_playwright_tail_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/foo.spec.ts",))
    assert scenario is not None
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_run(stdout=_playwright_green_tail())
    )
    result = run_scenario(scenario, cwd=".", timeout=10.0)
    assert result.status == "PASS"


def test_changed_e2e_scenario_with_prose_then_is_advisory_not_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/foo.spec.ts",))
    assert scenario is not None
    reintroduced = scenario.model_copy(
        update={"then": "Every selected spec passes against the stub-claude e2e harness"}
    )
    monkeypatch.setattr(
        process_runner, "run_subprocess", _fake_run(stdout=_playwright_green_tail())
    )
    result = run_scenario(reintroduced, cwd=".", timeout=10.0)
    assert result.status == "ADVISORY"


_MOTIVATING_DELIVERY_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("Exit code 0", "1 passed in 0.42s"),
    ("Exit code 0 and no errors", "5 passed in 1.10s"),
    ("Exit code 0, coverage 100%", "12 passed in 2.31s\ncoverage: 100%"),
    ("Exit code 0 with all tests green", "3 passed, 0 failed in 0.55s"),
    ("Exit code 0 - lint clean", "All matched files use Prettier code style!"),
    ("Exit code 0, typecheck clean", "Found 0 errors in 12 files"),
)


def _run_motivating_replay(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    statuses: list[str] = []
    for then, stdout in _MOTIVATING_DELIVERY_SCENARIOS:
        monkeypatch.setattr(process_runner, "run_subprocess", _fake_run(stdout=stdout))
        result = run_scenario(_scenario(then=then), cwd=".", timeout=10.0)
        statuses.append(result.status)
    return statuses


def test_motivating_delivery_scenarios_all_resolve_advisory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = _run_motivating_replay(monkeypatch)
    assert statuses == ["ADVISORY"] * len(_MOTIVATING_DELIVERY_SCENARIOS)


def test_motivating_delivery_verdict_axis_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = _run_motivating_replay(monkeypatch)
    assert "FAIL" not in statuses
    verdict = "REQUEST_CHANGES" if "FAIL" in statuses else "APPROVE"
    assert verdict == "APPROVE"


def test_advisory_scenario_outcome_round_trips_json() -> None:
    outcome = ScenarioOutcome(scenario_id="s1", status="ADVISORY", diagnostic="mismatch")
    round_tripped = ScenarioOutcome.model_validate_json(outcome.model_dump_json())
    assert round_tripped == outcome


def test_mixed_result_list_counts_one_advisory() -> None:
    outcomes = [
        ScenarioOutcome(scenario_id="a", status="PASS", diagnostic=""),
        ScenarioOutcome(scenario_id="b", status="ADVISORY", diagnostic="mismatch"),
        ScenarioOutcome(scenario_id="c", status="FAIL", diagnostic="exit code 1"),
    ]
    assert sum(1 for o in outcomes if o.status == "ADVISORY") == 1


def test_non_blocking_statuses_named_in_scenario_runner_prompt() -> None:
    for status in NON_BLOCKING_STATUSES:
        assert status in _verification._SCENARIO_RUNNER_PROMPT  # noqa: SLF001


def test_non_blocking_statuses_named_in_judge_prompt() -> None:
    for status in NON_BLOCKING_STATUSES:
        assert status in _verification._JUDGE_PROMPT  # noqa: SLF001


def test_timeout_not_in_non_blocking_statuses() -> None:
    assert "TIMEOUT" not in NON_BLOCKING_STATUSES


def test_scenario_runner_prompt_states_timeout_blocks() -> None:
    assert "TIMEOUT" in _verification._SCENARIO_RUNNER_PROMPT  # noqa: SLF001
    assert "TIMEOUT BLOCKS" in _verification._SCENARIO_RUNNER_PROMPT  # noqa: SLF001


def test_judge_prompt_states_timeout_blocks() -> None:
    assert "TIMEOUT" in _verification._JUDGE_PROMPT  # noqa: SLF001
    assert "BLOCKS exactly like FAIL" in _verification._JUDGE_PROMPT  # noqa: SLF001


def test_scenario_runner_prompt_no_longer_asks_to_restate_outcomes() -> None:
    assert "do NOT restate this run's per-scenario" in _verification._SCENARIO_RUNNER_PROMPT  # noqa: SLF001


def test_judge_prompt_no_longer_asks_to_restate_outcomes() -> None:
    assert "do not restate them in prose" in _verification._JUDGE_PROMPT  # noqa: SLF001


def test_implementation_plan_prompt_carries_then_advisory_clause() -> None:
    prompt = _implementation_plan._SYNTHESIZER_PROMPT  # noqa: SLF001
    assert "advisory only" in prompt
    assert "only the `when` command's exit status gates" in prompt


def test_implementation_fix_prompt_placeholder_is_corrective() -> None:
    assert "advisory only, never blocks" in _implementation_fix._SYSTEM_PROMPT  # noqa: SLF001
    assert (
        "advisory only, never blocks" in _implementation_fix._GENERIC_FIX_STATIC_TAIL  # noqa: SLF001
    )

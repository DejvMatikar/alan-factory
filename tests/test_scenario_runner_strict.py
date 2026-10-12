from __future__ import annotations

from pathlib import Path

from alan.handover import TestScenario
from alan.teams._scenario_runner import run_scenario, run_scenarios


def _scenario(sid: str = "s1", when: str = "uv run pytest tests/test_x.py") -> TestScenario:
    return TestScenario(id=sid, description="d", given="g", when=when, then="")


def test_strict_remaps_skipped_to_fail_with_diagnostic_preserved() -> None:
    result = run_scenario(_scenario(when=""), cwd=".", timeout=1.0, strict=True)
    assert result.status == "FAIL"
    assert result.diagnostic == "when field is empty"


def test_strict_remaps_documented_to_fail_with_diagnostic_preserved() -> None:
    scenario = _scenario(when="the user manually verifies in the browser")
    result = run_scenario(scenario, cwd=".", timeout=1.0, strict=True)
    assert result.status == "FAIL"
    assert "prose when field" in result.diagnostic


def test_strict_does_not_remap_advisory(tmp_path: Path) -> None:
    real = tmp_path / "proof.txt"
    real.write_text("proof", encoding="utf-8")
    scenario = _scenario(when=f"cat {real}")
    scenario = scenario.model_copy(update={"then": "text that never appears"})
    result = run_scenario(scenario, cwd=".", timeout=10.0, strict=True)
    assert result.status == "ADVISORY"


def test_strict_does_not_remap_budget_exhausted() -> None:
    results = run_scenarios(
        [_scenario("s1")], cwd=".", aggregate_budget=0.0, strict_ids=frozenset({"s1"})
    )
    assert results[0].status == "BUDGET_EXHAUSTED"


def test_strict_does_not_remap_pass(tmp_path: Path) -> None:
    real = tmp_path / "proof.txt"
    real.write_text("proof", encoding="utf-8")
    scenario = _scenario(when=f"cat {real}")
    result = run_scenario(scenario, cwd=".", timeout=10.0, strict=True)
    assert result.status == "PASS"


def test_strict_does_not_remap_fail(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    scenario = _scenario(when=f"cat {missing}")
    result = run_scenario(scenario, cwd=".", timeout=10.0, strict=True)
    assert result.status == "FAIL"


def test_strict_does_not_remap_timeout() -> None:
    scenario = _scenario(when="uv run python -c \"__import__('time').sleep(5)\"")
    result = run_scenario(scenario, cwd=".", timeout=0.01, strict=True)
    assert result.status == "TIMEOUT"


def test_non_strict_default_never_remaps() -> None:
    result = run_scenario(_scenario(when=""), cwd=".", timeout=1.0)
    assert result.status == "SKIPPED"


def test_run_scenarios_applies_strict_per_id_only(tmp_path: Path) -> None:
    real = tmp_path / "proof.txt"
    real.write_text("proof", encoding="utf-8")
    strict_scenario = _scenario("strict-id", when="")
    lenient_scenario = _scenario("lenient-id", when="")
    results = run_scenarios(
        [strict_scenario, lenient_scenario],
        cwd=".",
        strict_ids=frozenset({"strict-id"}),
    )
    by_id = {r.scenario_id: r.status for r in results}
    assert by_id["strict-id"] == "FAIL"
    assert by_id["lenient-id"] == "SKIPPED"

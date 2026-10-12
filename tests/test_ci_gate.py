from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_WORKFLOW = _ROOT / ".github" / "workflows" / "tests.yml"
_SONAR_VARIABLES = ("SONAR_TOKEN", "SONAR_ORGANIZATION", "SONAR_PROJECT_KEY")
_CONNECTED_ENV = {
    "SONAR_TOKEN": "value-a",
    "SONAR_ORGANIZATION": "value-b",
    "SONAR_PROJECT_KEY": "value-c",
}
_DETECT_STEP_ID = "sonar-config"
_CONNECTED_GUARD = "steps.sonar-config.outputs.connected == 'true'"
_DIFF_STEP_ID = "diff"


def _load(path: Path) -> dict[str, Any]:
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} does not parse to a YAML mapping"
    return loaded


def _jobs() -> dict[str, Any]:
    jobs: object = _load(_TESTS_WORKFLOW).get("jobs")
    assert isinstance(jobs, dict), "tests.yml defines no 'jobs' mapping"
    return jobs


def _job(job_id: str) -> dict[str, Any]:
    job: object = _jobs().get(job_id)
    assert isinstance(job, dict), f"tests.yml has no '{job_id}' job"
    return job


def _step(job: Mapping[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    steps: object = job.get("steps")
    assert isinstance(steps, list), "job defines no 'steps' list"
    for step in steps:
        assert isinstance(step, dict), "step definition must be a mapping"
        if predicate(step):
            return step
    raise AssertionError("no step matched the predicate")


def _run_of(job_id: str, predicate: Callable[[dict[str, Any]], bool]) -> str:
    run: object = _step(_job(job_id), predicate).get("run")
    assert isinstance(run, str), f"{job_id} step has no 'run' block"
    return run


def _gate_script() -> str:
    return _run_of("gate", lambda step: "run" in step)


def _detect_step() -> dict[str, Any]:
    return _step(_job("sonar"), lambda step: step.get("id") == _DETECT_STEP_ID)


def _detect_script() -> str:
    return _run_of("sonar", lambda step: step.get("id") == _DETECT_STEP_ID)


def _run_bash(script: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    search_path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env={"PATH": search_path, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _needs_json(results: Mapping[str, str]) -> str:
    return json.dumps({job: {"result": result, "outputs": {}} for job, result in results.items()})


def _gate_needs() -> list[str]:
    needs: object = _job("gate").get("needs")
    assert isinstance(needs, list), "gate declares no 'needs' list"
    return [str(need) for need in needs]


def _results(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    results = {job: "success" for job in _gate_needs()}
    results.update(overrides or {})
    return results


def _missing_needs(jobs: Mapping[str, Any], needs: Sequence[str]) -> list[str]:
    return sorted(job_id for job_id in jobs if job_id != "gate" and job_id not in needs)


def _run_gate(needs_json: str | None) -> subprocess.CompletedProcess[str]:
    return _run_bash(_gate_script(), {} if needs_json is None else {"NEEDS": needs_json})


def _run_detect(
    env: Mapping[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], str]:
    output = tmp_path / "github_output"
    completed = _run_bash(_detect_script(), {**env, "GITHUB_OUTPUT": str(output)})
    return completed, output.read_text(encoding="utf-8")


def test_gate_passes_when_every_needed_job_succeeded() -> None:
    assert _run_gate(_needs_json(_results())).returncode == 0


def test_gate_passes_when_only_the_drift_gate_was_skipped() -> None:
    assert _run_gate(_needs_json(_results({"drift-gate": "skipped"}))).returncode == 0


def test_gate_passes_when_sonar_was_skipped() -> None:
    assert _run_gate(_needs_json(_results({"sonar": "skipped"}))).returncode == 0


def test_gate_passes_when_sonar_succeeded() -> None:
    assert _run_gate(_needs_json(_results({"sonar": "success"}))).returncode == 0


def test_gate_passes_when_the_drift_gate_and_sonar_were_both_skipped() -> None:
    results = _results({"drift-gate": "skipped", "sonar": "skipped"})
    assert _run_gate(_needs_json(results)).returncode == 0


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_gate_fails_when_sonar_did_not_finish_cleanly(result: str) -> None:
    completed = _run_gate(_needs_json(_results({"sonar": result})))
    assert completed.returncode != 0
    assert "sonar" in completed.stderr


def test_gate_fails_when_another_job_was_skipped() -> None:
    assert _run_gate(_needs_json(_results({"e2e-frontend": "skipped"}))).returncode != 0


def test_gate_fails_when_a_job_failed() -> None:
    assert _run_gate(_needs_json(_results({"test-backend": "failure"}))).returncode != 0


def test_gate_fails_when_a_job_was_cancelled() -> None:
    assert _run_gate(_needs_json(_results({"lint-imports": "cancelled"}))).returncode != 0


def test_gate_fails_when_the_drift_gate_failed() -> None:
    assert _run_gate(_needs_json(_results({"drift-gate": "failure"}))).returncode != 0


def test_gate_fails_when_the_drift_gate_was_cancelled() -> None:
    assert _run_gate(_needs_json(_results({"drift-gate": "cancelled"}))).returncode != 0


def test_gate_fails_on_an_empty_needs_mapping() -> None:
    assert _run_gate("{}").returncode != 0


def test_gate_fails_on_unparsable_needs() -> None:
    assert _run_gate("not json").returncode != 0


def test_gate_fails_when_needs_is_missing() -> None:
    assert _run_gate(None).returncode != 0


def test_gate_names_the_failing_job() -> None:
    completed = _run_gate(_needs_json(_results({"ccn-gate": "failure"})))
    assert "ccn-gate" in completed.stderr


def test_gate_always_runs_and_its_check_name_is_gate() -> None:
    job = _job("gate")
    assert job.get("if") == "always()"
    assert job.get("name") == "gate"


def test_gate_needs_every_other_job_exactly_once() -> None:
    needs = _gate_needs()
    assert len(needs) == len(set(needs))
    assert _missing_needs(_jobs(), needs) == []
    assert set(needs) <= set(_jobs())
    assert "gate" not in needs


def test_missing_needs_flags_a_job_the_gate_omits() -> None:
    jobs: dict[str, Any] = {"gate": {}, "alpha": {}, "beta": {}, "gamma": {}}
    assert _missing_needs(jobs, ["alpha", "gamma"]) == ["beta"]


def test_sonar_detect_reports_connected_when_all_three_values_are_set(tmp_path: Path) -> None:
    completed, output = _run_detect(_CONNECTED_ENV, tmp_path)
    assert completed.returncode == 0
    assert output == "connected=true\n"
    assert "::notice::" not in completed.stdout


@pytest.mark.parametrize("value", ["", " \t "], ids=["empty", "whitespace"])
@pytest.mark.parametrize("blank", _SONAR_VARIABLES)
def test_sonar_detect_reports_not_connected_when_a_value_is_blank(
    blank: str, value: str, tmp_path: Path
) -> None:
    completed, output = _run_detect({**_CONNECTED_ENV, blank: value}, tmp_path)
    assert completed.returncode == 0
    assert output == "connected=false\n"
    assert "::notice::" in completed.stdout
    assert "::error::" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("missing", _SONAR_VARIABLES)
def test_sonar_detect_reports_not_connected_when_a_value_is_unset(
    missing: str, tmp_path: Path
) -> None:
    env = {name: value for name, value in _CONNECTED_ENV.items() if name != missing}
    completed, output = _run_detect(env, tmp_path)
    assert completed.returncode == 0
    assert output == "connected=false\n"
    assert "::error::" not in completed.stdout + completed.stderr


def test_sonar_detect_reports_not_connected_when_nothing_is_set(tmp_path: Path) -> None:
    completed, output = _run_detect({}, tmp_path)
    assert completed.returncode == 0
    assert output == "connected=false\n"


def test_sonar_detect_never_prints_a_configured_value(tmp_path: Path) -> None:
    completed, _ = _run_detect(_CONNECTED_ENV, tmp_path)
    for value in _CONNECTED_ENV.values():
        assert value not in completed.stdout + completed.stderr


def test_sonar_detect_reads_exactly_the_token_and_the_two_variables() -> None:
    assert _detect_step().get("env") == {
        "SONAR_TOKEN": "${{ secrets.SONAR_TOKEN }}",
        "SONAR_ORGANIZATION": "${{ vars.SONAR_ORGANIZATION }}",
        "SONAR_PROJECT_KEY": "${{ vars.SONAR_PROJECT_KEY }}",
    }


def test_sonar_detect_runs_first_and_unconditionally() -> None:
    steps = _job("sonar")["steps"]
    assert steps[0].get("id") == _DETECT_STEP_ID
    assert "if" not in steps[0]


def test_sonar_job_never_raises_an_error_annotation() -> None:
    dumped = json.dumps(_job("sonar"))
    assert "::error::" not in dumped
    assert "configure SonarCloud" not in dumped


def test_every_sonar_step_after_detection_runs_only_when_connected() -> None:
    steps = _job("sonar")["steps"][1:]
    assert steps
    assert [step for step in steps if _CONNECTED_GUARD not in str(step.get("if"))] == []


def test_sonar_job_still_checks_out_downloads_coverage_and_scans() -> None:
    uses = [str(step.get("uses", "")).split("@")[0] for step in _job("sonar")["steps"]]
    assert uses.count("actions/checkout") == 1
    assert uses.count("actions/download-artifact") == 2
    assert uses.count("SonarSource/sonarqube-scan-action") == 1


def test_sonar_job_only_skips_when_the_run_was_cancelled() -> None:
    assert _job("sonar").get("if") == "${{ !cancelled() }}"


@pytest.mark.parametrize("forbidden", ["event_name", "actor", "fork"])
def test_sonar_job_carries_no_event_actor_or_fork_condition(forbidden: str) -> None:
    assert forbidden not in json.dumps(_job("sonar"))


def test_sonar_job_no_longer_claims_to_be_a_blocking_quality_gate() -> None:
    name = str(_job("sonar").get("name", ""))
    assert "blocking" not in name.lower()
    assert "quality gate" not in name.lower()


def test_sonar_job_never_waits_for_the_quality_gate() -> None:
    assert "qualitygate" not in json.dumps(_job("sonar")).lower()


def test_sonar_scan_reads_the_organization_and_project_key_from_variables() -> None:
    scan = _step(
        _job("sonar"), lambda step: str(step.get("uses", "")).startswith("SonarSource/")
    )
    args = str(scan["with"]["args"])
    assert "${{ vars.SONAR_ORGANIZATION }}" in args
    assert "${{ vars.SONAR_PROJECT_KEY }}" in args


def _interpolation_free_steps() -> dict[str, dict[str, Any]]:
    def diff_step(job_id: str) -> dict[str, Any]:
        return _step(_job(job_id), lambda step: step.get("id") == _DIFF_STEP_ID)

    return {
        "gate": _step(_job("gate"), lambda step: "run" in step),
        "sonar-detect": _detect_step(),
        "diff-e2e-frontend": diff_step("e2e-frontend"),
        "diff-security-audit": diff_step("security-audit"),
    }


@pytest.mark.parametrize("label", sorted(_interpolation_free_steps()))
def test_script_steps_are_interpolation_free_bash(label: str) -> None:
    step = _interpolation_free_steps()[label]
    assert "${{" not in str(step.get("run"))
    assert step.get("shell") == "bash"

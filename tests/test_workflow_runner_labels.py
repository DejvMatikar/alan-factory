from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _ROOT / ".github" / "workflows"
_TESTS_WORKFLOW = _WORKFLOWS_DIR / "tests.yml"

_LINUX_RUNNER = "ubuntu-latest"
_MACOS_RUNNER = "macos-15"
_AUDIT_JOBS = frozenset({"audit-mobile", "audit-desktop"})
_E2E_DERIVE_STEP = "Derive unique e2e port range + root"


def _load_document(path: Path) -> dict[str, Any]:
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} does not parse to a YAML mapping"
    return loaded


def _workflow_paths(directory: Path) -> tuple[Path, ...]:
    return tuple(sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")))


def _discover_jobs(directory: Path = _WORKFLOWS_DIR) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    discovered: list[tuple[str, str, dict[str, Any]]] = []
    for path in _workflow_paths(directory):
        jobs: object = _load_document(path).get("jobs")
        assert isinstance(jobs, dict), f"{path.name} defines no 'jobs' mapping"
        for job_id, job_def in jobs.items():
            assert isinstance(job_def, dict), f"{path.name}:{job_id} must be a mapping"
            discovered.append((path.name, str(job_id), job_def))
    return tuple(discovered)


def _tests_job(job_id: str) -> dict[str, Any]:
    for workflow, discovered_id, job_def in _discover_jobs():
        if workflow == "tests.yml" and discovered_id == job_id:
            return job_def
    raise AssertionError(f"tests.yml has no '{job_id}' job")


def _step(job: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    steps: object = job.get("steps")
    assert isinstance(steps, list), "job defines no 'steps' list"
    for step in steps:
        assert isinstance(step, dict), "step definition must be a mapping"
        if predicate(step):
            return step
    raise AssertionError("no step matched the predicate")


_JOBS = _discover_jobs()
_JOB_IDS = tuple(f"{workflow}:{job_id}" for workflow, job_id, _ in _JOBS)


def test_discovers_workflow_jobs_from_both_workflow_files() -> None:
    assert {workflow for workflow, _, _ in _JOBS} == {"tests.yml", "pip-audit-weekly.yml"}


def test_discover_jobs_finds_both_yml_and_yaml(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("jobs:\n  job-a:\n    runs-on: x\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("jobs:\n  job-b:\n    runs-on: x\n", encoding="utf-8")
    found = {(workflow, job_id) for workflow, job_id, _ in _discover_jobs(tmp_path)}
    assert found == {("a.yml", "job-a"), ("b.yaml", "job-b")}


def test_discover_jobs_rejects_non_mapping_document(tmp_path: Path) -> None:
    (tmp_path / "empty.yml").write_text("", encoding="utf-8")
    with pytest.raises(AssertionError, match="YAML mapping"):
        _discover_jobs(tmp_path)


def test_discover_jobs_rejects_file_without_jobs_mapping(tmp_path: Path) -> None:
    (tmp_path / "no-jobs.yml").write_text("name: x\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="no 'jobs' mapping"):
        _discover_jobs(tmp_path)


@pytest.mark.parametrize("workflow,job_id,job_def", _JOBS, ids=_JOB_IDS)
def test_job_runs_on_its_hosted_runner(
    workflow: str, job_id: str, job_def: dict[str, Any]
) -> None:
    expected = _MACOS_RUNNER if job_id in _AUDIT_JOBS else _LINUX_RUNNER
    assert job_def.get("runs-on") == expected, f"{workflow}:{job_id} must run on {expected}"


def test_both_ui_audit_jobs_exist_on_the_macos_runner() -> None:
    tests_jobs = {job_id: job_def for workflow, job_id, job_def in _JOBS if workflow == "tests.yml"}
    assert _AUDIT_JOBS <= set(tests_jobs)
    assert {tests_jobs[job_id]["runs-on"] for job_id in _AUDIT_JOBS} == {_MACOS_RUNNER}


def test_only_the_ui_audit_jobs_leave_the_linux_runner() -> None:
    off_linux = {job_id for _, job_id, job_def in _JOBS if job_def.get("runs-on") != _LINUX_RUNNER}
    assert off_linux == _AUDIT_JOBS


def test_no_workflow_names_a_self_hosted_label() -> None:
    for path in _workflow_paths(_WORKFLOWS_DIR):
        assert "self-hosted" not in path.read_text(encoding="utf-8"), path.name


def test_the_combined_audit_job_is_gone() -> None:
    assert "mobile-audit-gate" not in _TESTS_WORKFLOW.read_text(encoding="utf-8")
    assert not [job_id for _, job_id, _ in _JOBS if job_id == "mobile-audit-gate"]


def test_e2e_runs_three_playwright_workers() -> None:
    derive = _step(
        _tests_job("e2e-frontend"), lambda step: step.get("name") == _E2E_DERIVE_STEP
    )
    assert 'echo "E2E_WORKERS=3" >> "$GITHUB_ENV"' in str(derive.get("run"))


def test_e2e_timeout_is_forty_five_minutes() -> None:
    assert _tests_job("e2e-frontend").get("timeout-minutes") == 45


def test_backend_timeout_is_twenty_minutes() -> None:
    assert _tests_job("test-backend").get("timeout-minutes") == 20


def test_frontend_unit_tests_run_three_workers() -> None:
    step = _step(
        _tests_job("test-frontend"),
        lambda candidate: candidate.get("run") == "npm run test:coverage",
    )
    assert step.get("env") == {"VITEST_MAX_WORKERS": "3"}


def test_no_workflow_sets_the_owner_uv_cache_dir() -> None:
    for path in _workflow_paths(_WORKFLOWS_DIR):
        assert "UV_CACHE_DIR" not in path.read_text(encoding="utf-8"), path.name


def test_no_workflow_sweeps_stale_listeners_with_lsof() -> None:
    for path in _workflow_paths(_WORKFLOWS_DIR):
        assert "lsof" not in path.read_text(encoding="utf-8"), path.name


def test_no_job_declares_its_own_concurrency_group() -> None:
    lane_jobs = [job_id for _, job_id, job_def in _JOBS if "concurrency" in job_def]
    assert lane_jobs == []

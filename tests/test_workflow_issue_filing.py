from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _ROOT / ".github" / "workflows"

_SHA_PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")
_GH_WRITE_COMMAND = re.compile(r"\bgh\s+(?:issue|label|pr)\b")
_SECRET_REFERENCE = re.compile(r"secrets\.[A-Za-z0-9_]+")
_FILING_ONLY_ACTIONS = ("github-script", "pytest-coverage-comment")
_REMOVED_JOBS = frozenset({"coverage-comment", "main-red-report"})


def _workflow_paths() -> tuple[Path, ...]:
    return tuple(sorted(_WORKFLOWS_DIR.glob("*.yml")) + sorted(_WORKFLOWS_DIR.glob("*.yaml")))


def _load_document(path: Path) -> dict[str, Any]:
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} does not parse to a YAML mapping"
    return loaded


def _jobs(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs: object = document.get("jobs")
    assert isinstance(jobs, dict), "workflow defines no 'jobs' mapping"
    typed: dict[str, dict[str, Any]] = {}
    for job_id, job_def in jobs.items():
        assert isinstance(job_def, dict), f"{job_id} must be a mapping"
        typed[str(job_id)] = job_def
    return typed


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps: object = job.get("steps")
    assert isinstance(steps, list), "job defines no 'steps' list"
    typed: list[dict[str, Any]] = []
    for step in steps:
        assert isinstance(step, dict), "step definition must be a mapping"
        typed.append(step)
    return typed


def _all_steps() -> list[tuple[str, str, dict[str, Any]]]:
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path in _workflow_paths():
        for job_id, job in _jobs(_load_document(path)).items():
            found.extend((path.name, job_id, step) for step in _steps(job))
    return found


def _permission_mappings() -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for path in _workflow_paths():
        document = _load_document(path)
        jobs = [(f"{path.name}:{job_id}", job) for job_id, job in _jobs(document).items()]
        scopes = [(path.name, document), *jobs]
        for label, scope in scopes:
            permissions: object = scope.get("permissions")
            if permissions is not None:
                assert isinstance(permissions, dict), f"{label} permissions must be a mapping"
                found.append((label, permissions))
    return found


def test_the_workflows_under_test_are_discovered() -> None:
    assert {path.name for path in _workflow_paths()} >= {"tests.yml"}


def test_no_permission_scope_grants_write_or_issues() -> None:
    mappings = _permission_mappings()
    assert mappings
    for label, permissions in mappings:
        assert "issues" not in permissions, label
        assert "write" not in permissions.values(), label


def test_no_run_block_shells_out_to_gh_issue_label_or_pr() -> None:
    offenders = [
        f"{workflow}:{job_id}:{step.get('name', '?')}"
        for workflow, job_id, step in _all_steps()
        if isinstance(step.get("run"), str) and _GH_WRITE_COMMAND.search(step["run"])
    ]
    assert offenders == []


def test_no_step_uses_a_local_action() -> None:
    local = [
        f"{workflow}:{job_id}"
        for workflow, job_id, step in _all_steps()
        if str(step.get("uses", "")).startswith("./")
    ]
    assert local == []


def test_no_step_uses_github_script_or_a_coverage_comment_action() -> None:
    offenders = [
        f"{workflow}:{job_id}"
        for workflow, job_id, step in _all_steps()
        if any(name in str(step.get("uses", "")) for name in _FILING_ONLY_ACTIONS)
    ]
    assert offenders == []


def test_the_filing_and_comment_jobs_are_gone() -> None:
    present = {
        job_id for path in _workflow_paths() for job_id in _jobs(_load_document(path))
    }
    assert present.isdisjoint(_REMOVED_JOBS)


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    unpinned = [
        f"{workflow}:{job_id}:{step['uses']}"
        for workflow, job_id, step in _all_steps()
        if "uses" in step and not _SHA_PINNED.fullmatch(str(step["uses"]))
    ]
    assert unpinned == []


def test_the_only_secret_referenced_is_the_sonar_token() -> None:
    referenced = {
        reference
        for path in _workflow_paths()
        for reference in _SECRET_REFERENCE.findall(path.read_text(encoding="utf-8"))
    }
    assert referenced == {"secrets.SONAR_TOKEN"}

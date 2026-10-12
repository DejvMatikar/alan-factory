from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _ROOT / ".github" / "workflows"
_TESTS_WORKFLOW = _WORKFLOWS_DIR / "tests.yml"
_WEEKLY_WORKFLOW = _WORKFLOWS_DIR / "pip-audit-weekly.yml"

_PATHS_FILTER_ACTION = "dorny/paths-filter@"
_DRIFT_JOB = "drift-gate"
_DIFF_JOBS = ("e2e-frontend", "security-audit")
_DIFF_STEP_ID = "diff"
_PULL_REQUEST_ONLY = "github.event_name == 'pull_request'"
_STRESS_STEP_NAME = "Stress changed specs (repeat-each 5, no retries)"
_PIP_AUDIT_STEP_NAME = "pip-audit (locked deps, excluding the local project)"
_DRIFT_CONDITION = (
    "github.event_name == 'pull_request' "
    "&& !contains(github.event.pull_request.labels.*.name, 'drift-ok')"
)
_DRIFT_FAILING_CONDITION = (
    "steps.filter.outputs.loadbearing == 'true' && steps.filter.outputs.docs != 'true'"
)
_CONCURRENCY = {
    "group": (
        "${{ github.workflow }}-"
        "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
    ),
    "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
}
_EXPECTED_TRIGGERS = {
    "pull_request": None,
    "push": {"branches": ["main"]},
    "workflow_dispatch": None,
}


def _load_document(path: Path) -> dict[Any, Any]:
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} does not parse to a YAML mapping"
    return loaded


def _triggers(document: dict[Any, Any]) -> dict[str, Any]:
    for key, value in document.items():
        if key is True or key == "on":
            assert isinstance(value, dict), "workflow 'on' must be a mapping"
            return value
    raise AssertionError("workflow declares no triggers")


def _jobs(document: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    jobs: object = document.get("jobs")
    assert isinstance(jobs, dict), "workflow defines no 'jobs' mapping"
    typed: dict[str, dict[str, Any]] = {}
    for job_id, job_def in jobs.items():
        assert isinstance(job_def, dict), f"{job_id} must be a mapping"
        typed[str(job_id)] = job_def
    return typed


def _tests_jobs() -> dict[str, dict[str, Any]]:
    return _jobs(_load_document(_TESTS_WORKFLOW))


def _steps(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps: object = job.get("steps")
    assert isinstance(steps, list), "job defines no 'steps' list"
    typed: list[dict[str, Any]] = []
    for step in steps:
        assert isinstance(step, dict), "step definition must be a mapping"
        typed.append(step)
    return typed


def _step(job: Mapping[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    for step in _steps(job):
        if predicate(step):
            return step
    raise AssertionError("no step matched the predicate")


def _step_id(job_id: str, step_id: str) -> dict[str, Any]:
    return _step(_tests_jobs()[job_id], lambda step: step.get("id") == step_id)


def _step_named(job_id: str, name: str) -> dict[str, Any]:
    return _step(_tests_jobs()[job_id], lambda step: step.get("name") == name)


def test_tests_workflow_triggers_are_exact() -> None:
    assert _triggers(_load_document(_TESTS_WORKFLOW)) == _EXPECTED_TRIGGERS


def test_neither_workflow_filters_its_triggers_by_path() -> None:
    for path in (_TESTS_WORKFLOW, _WEEKLY_WORKFLOW):
        for name, config in _triggers(_load_document(path)).items():
            if isinstance(config, dict):
                assert "paths" not in config, f"{path.name}:{name}"
                assert "paths-ignore" not in config, f"{path.name}:{name}"


def test_there_is_no_changed_paths_job() -> None:
    assert "changes" not in _tests_jobs()
    assert "needs.changes" not in _TESTS_WORKFLOW.read_text(encoding="utf-8")


def test_no_job_condition_reads_a_step_or_filter_output() -> None:
    for job_id, job in _tests_jobs().items():
        condition = str(job.get("if", ""))
        assert "steps." not in condition, job_id
        assert ".outputs." not in condition, job_id


def test_the_paths_filter_action_is_used_only_by_the_drift_gate() -> None:
    users = {
        job_id
        for job_id, job in _tests_jobs().items()
        for step in _steps(job)
        if str(step.get("uses", "")).startswith(_PATHS_FILTER_ACTION)
    }
    assert users == {_DRIFT_JOB}


def test_drift_gate_runs_only_on_pull_requests_without_the_bypass_label() -> None:
    assert _tests_jobs()[_DRIFT_JOB].get("if") == _DRIFT_CONDITION


def test_drift_gate_keeps_its_filter_step_and_doc_patterns() -> None:
    step = _step_id(_DRIFT_JOB, "filter")
    filters: object = yaml.safe_load(step["with"]["filters"])
    assert isinstance(filters, dict)
    assert filters["docs"] == ["CLAUDE.md", "docs/AGENTIC_CODING_GUIDE.md", "docs/architecture/**"]
    assert "pyproject.toml" in filters["loadbearing"]
    assert ".github/workflows/ci-deploy.yml" not in filters["loadbearing"]


def test_drift_gate_still_fails_when_load_bearing_files_change_without_docs() -> None:
    step = _step(
        _tests_jobs()[_DRIFT_JOB], lambda candidate: candidate.get("if") == _DRIFT_FAILING_CONDITION
    )
    assert str(step["run"]).rstrip().endswith("exit 1")


def test_workflow_token_is_read_only() -> None:
    assert _load_document(_TESTS_WORKFLOW).get("permissions") == {"contents": "read"}


def test_only_the_drift_gate_widens_its_token_scope() -> None:
    scoped = {
        job_id: job["permissions"] for job_id, job in _tests_jobs().items() if "permissions" in job
    }
    assert scoped == {_DRIFT_JOB: {"contents": "read", "pull-requests": "read"}}


def test_in_progress_runs_are_cancelled_for_pull_requests_only() -> None:
    assert _load_document(_TESTS_WORKFLOW).get("concurrency") == _CONCURRENCY


@pytest.mark.parametrize("job_id", _DIFF_JOBS)
def test_diff_step_is_pull_request_only_and_reads_two_commits(job_id: str) -> None:
    job = _tests_jobs()[job_id]
    assert _step_id(job_id, _DIFF_STEP_ID).get("if") == _PULL_REQUEST_ONLY
    assert _steps(job)[0]["with"] == {"fetch-depth": 2}


def test_stress_step_reads_the_changed_specs_from_the_environment() -> None:
    step = _step_named("e2e-frontend", _STRESS_STEP_NAME)
    assert step.get("env") == {"E2E_SPECS_FILES": "${{ steps.diff.outputs.e2e_specs_files }}"}
    assert 'done <<< "$E2E_SPECS_FILES"' in str(step["run"])
    assert "for f in" not in str(step["run"])
    assert "${{" not in str(step["run"])
    assert step.get("if") == (
        "github.event_name == 'pull_request' && steps.diff.outputs.e2e_specs_files != ''"
    )


def test_pip_audit_runs_only_for_pull_requests_touching_dependencies() -> None:
    step = _step_named("security-audit", _PIP_AUDIT_STEP_NAME)
    assert step.get("if") == (
        "github.event_name == 'pull_request' && steps.diff.outputs.deps == 'true'"
    )


def test_weekly_workflow_is_schedule_only_with_read_only_contents() -> None:
    document = _load_document(_WEEKLY_WORKFLOW)
    triggers = _triggers(document)
    assert set(triggers) == {"schedule"}
    assert [entry.keys() for entry in triggers["schedule"]] == [{"cron"}]
    assert document.get("permissions") == {"contents": "read"}
    assert {job["runs-on"] for job in _jobs(document).values()} == {"ubuntu-latest"}


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_AUTHOR_NAME": "Fixture Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return env


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], env=_git_env(), check=True, capture_output=True)


def _commit(repo: Path, files: Mapping[str, str | None]) -> None:
    for relative, content in files.items():
        path = repo / relative
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "fixture")


def _diff_outputs(job_id: str, repo: Path, output_file: Path) -> dict[str, str]:
    script = str(_step_id(job_id, _DIFF_STEP_ID)["run"])
    env = {**_git_env(), "GITHUB_OUTPUT": str(output_file)}
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )
    return _parse_outputs(output_file.read_text(encoding="utf-8"))


def _parse_outputs(text: str) -> dict[str, str]:
    outputs: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        multiline = re.fullmatch(r"([^=<]+)<<(.+)", line)
        if multiline is None:
            key, value = line.split("=", 1)
            outputs[key] = value
            continue
        body: list[str] = []
        for inner in lines:
            if inner == multiline.group(2):
                break
            body.append(inner)
        outputs[multiline.group(1)] = "\n".join(body)
    return outputs


def _repo_with_two_commits(
    tmp_path: Path, base: Mapping[str, str | None], change: Mapping[str, str | None]
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, base)
    _commit(repo, change)
    return repo


@pytest.mark.parametrize("job_id", _DIFF_JOBS)
def test_diff_step_lists_changed_and_deleted_specs_but_not_other_files(
    job_id: str, tmp_path: Path
) -> None:
    repo = _repo_with_two_commits(
        tmp_path,
        {
            "alan/web/e2e/a.spec.ts": "one\n",
            "alan/web/e2e/b.spec.ts": "one\n",
            "README.md": "one\n",
        },
        {
            "alan/web/e2e/a.spec.ts": "two\n",
            "alan/web/e2e/b.spec.ts": None,
            "alan/web/e2e/sub/c.spec.ts": "new\n",
            "alan/web/e2e/helpers.ts": "new\n",
            "docs/x.md": "new\n",
        },
    )
    outputs = _diff_outputs(job_id, repo, tmp_path / "out.txt")
    assert outputs == {
        "e2e_specs_files": (
            "alan/web/e2e/a.spec.ts\nalan/web/e2e/b.spec.ts\nalan/web/e2e/sub/c.spec.ts"
        ),
        "deps": "false",
    }


@pytest.mark.parametrize("job_id", _DIFF_JOBS)
@pytest.mark.parametrize("manifest", ["uv.lock", "pyproject.toml"])
def test_diff_step_flags_a_dependency_change(job_id: str, manifest: str, tmp_path: Path) -> None:
    repo = _repo_with_two_commits(
        tmp_path, {manifest: "one\n", "README.md": "one\n"}, {manifest: "two\n"}
    )
    outputs = _diff_outputs(job_id, repo, tmp_path / "out.txt")
    assert outputs == {"e2e_specs_files": "", "deps": "true"}


@pytest.mark.parametrize("job_id", _DIFF_JOBS)
@pytest.mark.parametrize("name", ["foo bar.spec.ts", "caf\u00e9.spec.ts"])
def test_diff_step_keeps_a_spec_path_with_a_space_or_non_ascii_whole(
    job_id: str, name: str, tmp_path: Path
) -> None:
    spec = f"alan/web/e2e/{name}"
    repo = _repo_with_two_commits(tmp_path, {"README.md": "one\n"}, {spec: "new\n"})
    outputs = _diff_outputs(job_id, repo, tmp_path / "out.txt")
    assert outputs == {"e2e_specs_files": spec, "deps": "false"}


def _run_stress_step(
    tmp_path: Path, listed: Sequence[str], present: Sequence[str]
) -> tuple[str, list[str]]:
    web = tmp_path / "alan" / "web"
    web.mkdir(parents=True)
    for relative in present:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "uv-args.txt"
    stub = bin_dir / "uv"
    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$UV_ARGS_FILE\"\n", encoding="utf-8")
    stub.chmod(0o755)
    summary = tmp_path / "summary.txt"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "E2E_SPECS_FILES": "\n".join(listed),
        "ALAN_E2E_ROOT": str(tmp_path / "e2e"),
        "GITHUB_STEP_SUMMARY": str(summary),
        "UV_ARGS_FILE": str(args_file),
    }
    script = str(_step_named("e2e-frontend", _STRESS_STEP_NAME)["run"])
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=web,
        env=env,
        check=True,
        capture_output=True,
    )
    ran = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else []
    return summary.read_text(encoding="utf-8"), ran


def test_stress_step_counts_and_runs_a_spec_path_containing_a_space(tmp_path: Path) -> None:
    spec = "alan/web/e2e/foo bar.spec.ts"
    summary, ran = _run_stress_step(tmp_path, [spec], [spec])
    assert "changed-spec stress: 1 file(s) x5:" in summary
    assert "--repeat-each=5" in ran
    assert "--retries=0" in ran


def test_stress_step_reports_deletions_only_when_no_listed_spec_exists(tmp_path: Path) -> None:
    summary, ran = _run_stress_step(tmp_path, ["alan/web/e2e/gone.spec.ts"], [])
    assert "changed-spec stress: deletions only, nothing to run" in summary
    assert ran == []


def test_stress_step_skips_above_the_cap(tmp_path: Path) -> None:
    names = [f"alan/web/e2e/s{index}.spec.ts" for index in range(9)]
    summary, ran = _run_stress_step(tmp_path, names, names)
    assert "changed-spec stress SKIPPED: 9 spec files changed (cap 8;" in summary
    assert ran == []


def test_stress_step_leaves_audit_specs_out_of_the_run(tmp_path: Path) -> None:
    kept = "alan/web/e2e/keep.spec.ts"
    audit = "alan/web/e2e/mobile-audit/skip.spec.ts"
    summary, ran = _run_stress_step(tmp_path, [kept, audit], [kept, audit])
    assert "changed-spec stress: 1 file(s) x5: e2e/keep.spec.ts" in summary
    assert "e2e/keep.spec.ts" in ran
    assert "e2e/mobile-audit/skip.spec.ts" not in ran

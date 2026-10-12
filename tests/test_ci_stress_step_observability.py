from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "tests.yml"
_STRESS_STEP_NAME = "Stress changed specs (repeat-each 5, no retries)"


def _load_document(path: Path) -> dict[str, Any]:
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} does not parse to a YAML mapping"
    return loaded


def _stress_step_run_block() -> str:
    document = _load_document(_TESTS_WORKFLOW_PATH)
    jobs: object = document.get("jobs")
    assert isinstance(jobs, dict), "tests.yml defines no 'jobs' mapping"
    e2e_job: object = jobs.get("e2e-frontend")
    assert isinstance(e2e_job, dict), "tests.yml has no 'e2e-frontend' job"
    steps: object = e2e_job.get("steps")
    assert isinstance(steps, list), "e2e-frontend defines no 'steps' list"
    for step in steps:
        assert isinstance(step, dict), "e2e-frontend step definition must be a mapping"
        if step.get("name") == _STRESS_STEP_NAME:
            run_block: object = step.get("run")
            assert isinstance(run_block, str), f"{_STRESS_STEP_NAME!r} has no 'run' block"
            return run_block
    raise AssertionError(f"e2e-frontend has no step named {_STRESS_STEP_NAME!r}")


def _branch_body(run_block: str, marker: str) -> str:
    start = run_block.index(marker)
    return run_block[start:]


def test_deletions_only_branch_echoes_stdout_and_notice() -> None:
    run_block = _stress_step_run_block()
    branch = _branch_body(run_block, 'if [ "$count" -eq 0 ]; then')
    branch = branch[: branch.index('elif [ "$count" -gt 8 ]; then')]
    assert 'msg="changed-spec stress: deletions only, nothing to run"' in branch
    assert 'echo "$msg" >> "$GITHUB_STEP_SUMMARY"' in branch
    assert 'echo "$msg"\n' in branch
    assert 'echo "::notice::$msg"' in branch


def test_cap_skip_branch_echoes_stdout_and_notice() -> None:
    run_block = _stress_step_run_block()
    branch = _branch_body(run_block, 'elif [ "$count" -gt 8 ]; then')
    branch = branch[: branch.index("else\n")]
    assert (
        'msg="changed-spec stress SKIPPED: $count spec files changed '
        '(cap 8; the nightly sweep covers bulk changes)"' in branch
    )
    assert 'echo "$msg" >> "$GITHUB_STEP_SUMMARY"' in branch
    assert 'echo "$msg"\n' in branch
    assert 'echo "::notice::$msg"' in branch


def test_running_branch_echoes_stdout_but_no_notice() -> None:
    run_block = _stress_step_run_block()
    branch = _branch_body(run_block, "else\n")
    branch = branch[: branch.index("fi\n")]
    assert 'msg="changed-spec stress: $count file(s) x5:$specs"' in branch
    assert 'echo "$msg" >> "$GITHUB_STEP_SUMMARY"' in branch
    assert 'echo "$msg"\n' in branch
    assert "::notice::" not in branch


def test_cap_is_unchanged() -> None:
    run_block = _stress_step_run_block()
    assert '[ "$count" -gt 8 ]' in run_block


_PLAYWRIGHT_COMMAND = (
    "uv run python ../../scripts/git_state_gate.py -- "
    "npx playwright test $specs --repeat-each=5 --retries=0"
)


def test_playwright_invocation_is_unchanged() -> None:
    run_block = _stress_step_run_block()
    matching_lines = [
        line.strip() for line in run_block.splitlines() if line.strip() == _PLAYWRIGHT_COMMAND
    ]
    assert matching_lines == [_PLAYWRIGHT_COMMAND]

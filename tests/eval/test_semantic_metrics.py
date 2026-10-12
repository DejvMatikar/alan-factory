from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.eval_metrics import (
    MERGE_SURVIVAL_WINDOW,
    gate_declared,
    merge_survival_rate,
    parse_git_log_output,
    parse_merge_audit_rows,
    requirements_coverage_rate,
)
from alan.workspace import Status, list_workspaces

_AUDIT_LOG = Path.home() / ".alan" / "logs" / "pr_merge_audit.jsonl"
_GIT_LOG_FORMAT = "%aI%x00%s%x00%b%x1E"


@pytest.mark.eval
def test_requirements_coverage_rate() -> None:
    workspaces = list_workspaces()
    completed = [ws for ws in workspaces if ws.meta.status == Status.COMPLETED]
    if not completed:
        print("EVAL_METRIC requirements_coverage_rate=N/A (no completed workspaces)")
        pytest.skip("no completed workspaces on disk")
    reqs_list = [ws.meta.requirements for ws in completed]
    rate = requirements_coverage_rate(reqs_list)
    covered = sum(1 for r in reqs_list if gate_declared(r))
    print(
        f"EVAL_METRIC requirements_coverage_rate={rate:.3f}"
        f" ({covered}/{len(completed)})"
    )


@pytest.mark.eval
def test_merge_survival_rate() -> None:
    if not _AUDIT_LOG.is_file():
        print("EVAL_METRIC merge_survival_rate=N/A (no pr_merge audit log)")
        pytest.skip("pr_merge_audit.jsonl not found")
    audit_text = _AUDIT_LOG.read_text(encoding="utf-8")
    audit_rows = parse_merge_audit_rows(audit_text)
    if not audit_rows:
        print("EVAL_METRIC merge_survival_rate=N/A (no merge_ok rows in audit)")
        pytest.skip("no merge_ok rows in audit log")
    try:
        result = subprocess.run(
            ["git", "log", f"--format={_GIT_LOG_FORMAT}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("EVAL_METRIC merge_survival_rate=N/A (git unavailable)")
        pytest.skip("git not available or timed out")
    if result.returncode != 0:
        print("EVAL_METRIC merge_survival_rate=N/A (git log error)")
        pytest.skip("git log returned non-zero exit code")
    git_records = parse_git_log_output(result.stdout)
    rate = merge_survival_rate(audit_rows, git_records)
    if rate is None:
        print("EVAL_METRIC merge_survival_rate=N/A (no parseable merge rows)")
        pytest.skip("no parseable merge_ok rows")
    print(
        f"EVAL_METRIC merge_survival_rate={rate:.3f}"
        f" (window={MERGE_SURVIVAL_WINDOW.days}d, n={len(audit_rows)})"
    )

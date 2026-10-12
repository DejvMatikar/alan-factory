from __future__ import annotations

from typing import Any

from alan.teams._pr_ready import pending_check_run_names


def test_in_progress_and_queued_check_runs_are_pending() -> None:
    runs: list[dict[str, Any]] = [
        {"name": "backend", "status": "in_progress", "conclusion": None},
        {"name": "e2e", "status": "queued", "conclusion": None},
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "sonar", "status": "completed", "conclusion": "failure"},
    ]
    assert pending_check_run_names(runs) == ("backend", "e2e")


def test_completed_success_and_failure_are_not_pending() -> None:
    runs: list[dict[str, Any]] = [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "sonar", "status": "completed", "conclusion": "failure"},
        {"name": "cancelled", "status": "completed", "conclusion": "cancelled"},
    ]
    assert pending_check_run_names(runs) == ()


def test_empty_check_run_list_has_no_pending_names() -> None:
    assert pending_check_run_names([]) == ()


def test_pending_check_run_without_a_name_reports_an_empty_name() -> None:
    assert pending_check_run_names([{"status": "queued"}]) == ("",)

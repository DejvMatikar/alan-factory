from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from alan.health import (
    _build_task_severity_action,  # pyright: ignore[reportPrivateUsage]
    _surface_to_finding,  # pyright: ignore[reportPrivateUsage]
    compute_health_state,
)
from alan.teams.triage_pregate import ClassifiedSurface


def _make_surface(
    surface_id: str,
    is_blocked: bool,
    blocker_reason: str,
    obs: dict[str, Any] | None = None,
    resolvable: bool = False,
) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=surface_id,
        kind="build_task",
        is_blocked=is_blocked,
        blocker_reason=blocker_reason,
        obs=obs or {},
        resolvable=resolvable,
    )


def test_pr_checks_failing_with_job_running_is_medium_severity() -> None:
    sev, action = _build_task_severity_action({"pr_checks_failing"}, True)
    assert sev == "medium"
    assert action == "dispatch_remediation"


def test_pr_checks_failing_without_job_running_is_critical_severity() -> None:
    sev, action = _build_task_severity_action({"pr_checks_failing"}, False)
    assert sev == "critical"
    assert action == "dispatch_remediation"


def test_pr_checks_failing_missing_job_running_key_is_critical_severity() -> None:
    surface = _make_surface("task-1", True, "pr_checks_failing", {})
    finding = _surface_to_finding(surface)
    assert finding.severity == "critical"
    assert finding.in_flight is False


def test_job_failed_severity_unaffected_by_job_running_true() -> None:
    sev, action = _build_task_severity_action({"job_failed"}, True)
    assert sev == "high"
    assert action == "dispatch_remediation"


def test_job_failed_severity_unaffected_by_job_running_false() -> None:
    sev, action = _build_task_severity_action({"job_failed"}, False)
    assert sev == "high"
    assert action == "dispatch_remediation"


def test_pr_approved_unmerged_severity_unaffected_by_job_running() -> None:
    sev_running, action_running = _build_task_severity_action({"pr_approved_unmerged"}, True)
    sev_idle, action_idle = _build_task_severity_action({"pr_approved_unmerged"}, False)
    assert sev_running == sev_idle == "high"
    assert action_running == action_idle == "merge_pr"


def test_build_task_fallthrough_severity_unaffected_by_job_running() -> None:
    sev_running, action_running = _build_task_severity_action(set(), True)
    sev_idle, action_idle = _build_task_severity_action(set(), False)
    assert sev_running == sev_idle == "medium"
    assert action_running == action_idle == "deep_link"


def test_resolvable_build_task_stays_medium_complete_regardless_of_job_running() -> None:
    running = _make_surface(
        "task-resolvable-running", False, "ws_stuck", {"job_running": True}, resolvable=True
    )
    idle = _make_surface(
        "task-resolvable-idle", False, "ws_stuck", {"job_running": False}, resolvable=True
    )
    finding_running = _surface_to_finding(running)
    finding_idle = _surface_to_finding(idle)
    assert finding_running.severity == finding_idle.severity == "medium"
    assert finding_running.action == finding_idle.action == "complete_workspace"


def test_dispatch_path_medium_severity_and_in_flight_marker_agree_when_true() -> None:
    surface = _make_surface(
        "task-agree-true", True, "pr_checks_failing", {"job_running": True}
    )
    finding = _surface_to_finding(surface)
    assert finding.action == "dispatch_remediation"
    assert finding.severity == "medium"
    assert finding.in_flight is True


def test_dispatch_path_medium_severity_and_in_flight_marker_agree_when_false() -> None:
    surface = _make_surface(
        "task-agree-false", True, "pr_checks_failing", {"job_running": False}
    )
    finding = _surface_to_finding(surface)
    assert finding.action == "dispatch_remediation"
    assert finding.severity == "critical"
    assert finding.in_flight is False


def test_blocked_count_and_verdict_unchanged_across_job_running_states() -> None:
    running = _make_surface(
        "task-count-running", True, "pr_checks_failing", {"job_running": True}
    )
    idle = _make_surface("task-count-idle", True, "pr_checks_failing", {"job_running": False})
    state_running = compute_health_state([running], datetime.now(UTC))
    state_idle = compute_health_state([idle], datetime.now(UTC))
    assert state_running.verdict == state_idle.verdict == "degraded"
    assert state_running.blocked_count == state_idle.blocked_count == 1


def test_description_includes_running_clause_when_job_running_true() -> None:
    surface = _make_surface(
        "task-desc-running", True, "pr_checks_failing", {"job_running": True}
    )
    finding = _surface_to_finding(surface)
    assert "work is running" in finding.description


def test_description_omits_running_clause_when_job_running_false() -> None:
    surface = _make_surface(
        "task-desc-idle", True, "pr_checks_failing", {"job_running": False}
    )
    finding = _surface_to_finding(surface)
    assert "work is running" not in finding.description


def test_description_omits_running_clause_when_job_running_key_absent() -> None:
    surface = _make_surface("task-desc-absent", True, "pr_checks_failing", {})
    finding = _surface_to_finding(surface)
    assert "work is running" not in finding.description

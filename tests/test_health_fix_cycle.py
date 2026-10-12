from __future__ import annotations

from alan.health import (
    _ACTION_DISPATCH,  # pyright: ignore[reportPrivateUsage]
    _build_task_severity_action,  # pyright: ignore[reportPrivateUsage]
    _describe_build,  # pyright: ignore[reportPrivateUsage]
    _fix_cycle_running_tag,  # pyright: ignore[reportPrivateUsage]
    _surface_to_finding,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams.triage_pregate import ClassifiedSurface


def test_pr_checks_failing_in_flight_below_escalate_threshold_is_medium() -> None:
    severity, action = _build_task_severity_action({"pr_checks_failing"}, True, False)
    assert severity == "medium"
    assert action == _ACTION_DISPATCH


def test_pr_checks_failing_in_flight_at_escalate_threshold_is_critical() -> None:
    severity, action = _build_task_severity_action({"pr_checks_failing"}, True, True)
    assert severity == "critical"
    assert action == _ACTION_DISPATCH


def test_pr_checks_failing_not_in_flight_is_critical_regardless_of_escalate() -> None:
    severity, _ = _build_task_severity_action({"pr_checks_failing"}, False, False)
    assert severity == "critical"


def test_pr_checks_failing_not_in_flight_stays_critical_even_when_escalated() -> None:
    severity, _ = _build_task_severity_action({"pr_checks_failing"}, False, True)
    assert severity == "critical"


def test_fix_cycle_escalate_defaults_to_false() -> None:
    severity, _ = _build_task_severity_action({"pr_checks_failing"}, True)
    assert severity == "medium"


def test_job_failed_severity_unaffected_by_fix_cycle_escalate() -> None:
    severity, action = _build_task_severity_action({"job_failed"}, True, True)
    assert severity == "high"
    assert action == _ACTION_DISPATCH


def test_fix_cycle_running_tag_names_the_count_when_running() -> None:
    tag = _fix_cycle_running_tag({"job_running": True, "fix_cycle_count": 3})
    assert tag == " — work is running (3 fix cycles)"


def test_fix_cycle_running_tag_omits_count_when_zero() -> None:
    tag = _fix_cycle_running_tag({"job_running": True, "fix_cycle_count": 0})
    assert tag == " — work is running"


def test_fix_cycle_running_tag_empty_when_not_running() -> None:
    tag = _fix_cycle_running_tag({"job_running": False, "fix_cycle_count": 5})
    assert tag == ""


def test_fix_cycle_running_tag_tolerates_non_int_count() -> None:
    tag = _fix_cycle_running_tag({"job_running": True, "fix_cycle_count": "oops"})
    assert tag == " — work is running"


def test_describe_build_includes_fix_cycle_count_in_running_tag() -> None:
    description = _describe_build(
        "ws-1", {"pr_checks_failing"}, {"job_running": True, "fix_cycle_count": 3}
    )
    assert "3 fix cycles" in description


def test_surface_to_finding_build_task_carries_critical_at_escalate_threshold() -> None:
    surface = ClassifiedSurface(
        surface_id="ws-1",
        kind="build_task",
        is_blocked=True,
        blocker_reason="pr_checks_failing",
        obs={
            "job_running": True,
            "fix_cycle_count": 3,
            "fix_cycle_escalate": True,
            "pr_number": 55,
        },
    )
    finding = _surface_to_finding(surface)
    assert finding.severity == "critical"
    assert "3 fix cycles" in finding.description

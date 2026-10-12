from __future__ import annotations

from datetime import UTC, datetime

from alan.health import compute_health_state
from alan.teams.triage_pregate import ClassifiedSurface


def _surface(kind: str, reason: str) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"{kind}:babysit",
        kind=kind,
        is_blocked=True,
        blocker_reason=reason,
        obs={"name": "babysit", "reason": reason},
    )


def test_driver_loop_unhealthy_severity_is_high() -> None:
    state = compute_health_state([_surface("driver_loop_unhealthy", "overdue")], datetime.now(UTC))
    assert state.findings[0].severity == "high"


def test_driver_loop_attention_severity_is_medium() -> None:
    state = compute_health_state(
        [_surface("driver_loop_attention", "ran_with_denials")], datetime.now(UTC)
    )
    assert state.findings[0].severity == "medium"


def test_driver_loop_unhealthy_description_names_loop_and_reason() -> None:
    surfaces = [_surface("driver_loop_unhealthy", "backing_off")]
    state = compute_health_state(surfaces, datetime.now(UTC))
    description = state.findings[0].description
    assert "babysit" in description
    assert "backing_off" in description


def test_driver_loop_attention_description_names_loop_and_reason() -> None:
    state = compute_health_state(
        [_surface("driver_loop_attention", "interactive_lock")], datetime.now(UTC)
    )
    description = state.findings[0].description
    assert "babysit" in description
    assert "interactive_lock" in description


def test_driver_loop_findings_mark_state_degraded() -> None:
    state = compute_health_state([_surface("driver_loop_unhealthy", "overdue")], datetime.now(UTC))
    assert state.verdict == "degraded"

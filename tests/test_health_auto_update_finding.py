from __future__ import annotations

from datetime import UTC, datetime

from alan import health
from alan.teams.triage_pregate import ClassifiedSurface

_CHECKED_AT = "2026-01-01T00:00:00+00:00"


def _surface(blocker_reason: str, **extra_obs: object) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"auto_update:{blocker_reason}",
        kind="auto_update",
        is_blocked=True,
        blocker_reason=blocker_reason,
        obs={"reason": blocker_reason, "checked_at": _CHECKED_AT, **extra_obs},
    )


def test_a_blocked_reason_finding_defaults_to_high_severity_and_deep_link() -> None:
    state = health.compute_health_state([_surface("dirty_tree")], datetime.now(UTC))
    finding = state.findings[0]
    assert finding.kind == "auto_update"
    assert finding.severity == "high"
    assert finding.action == "deep_link"


def test_a_red_main_finding_stays_high_severity() -> None:
    state = health.compute_health_state([_surface("main_red")], datetime.now(UTC))
    assert state.findings[0].severity == "high"


def test_a_rolled_back_finding_stays_high_severity() -> None:
    state = health.compute_health_state([_surface("rolled_back")], datetime.now(UTC))
    assert state.findings[0].severity == "high"


def test_a_rollback_failed_finding_escalates_to_critical_severity() -> None:
    state = health.compute_health_state([_surface("rollback_failed")], datetime.now(UTC))
    finding = state.findings[0]
    assert finding.severity == "critical"
    assert finding.action == "deep_link"


def test_a_stale_finding_stays_high_severity() -> None:
    state = health.compute_health_state([_surface("stale")], datetime.now(UTC))
    assert state.findings[0].severity == "high"


def test_the_description_mentions_the_reason_and_when_it_was_checked() -> None:
    state = health.compute_health_state([_surface("dirty_tree")], datetime.now(UTC))
    description = state.findings[0].description
    assert "dirty_tree" in description
    assert _CHECKED_AT in description


def test_the_description_falls_back_to_the_target_sha_when_checked_at_is_absent() -> None:
    surface = ClassifiedSurface(
        surface_id="auto_update:rolled_back",
        kind="auto_update",
        is_blocked=True,
        blocker_reason="rolled_back",
        obs={"reason": "rolled_back", "target_sha": "a" * 40},
    )
    state = health.compute_health_state([surface], datetime.now(UTC))
    description = state.findings[0].description
    assert "checked" not in description
    assert "(target aaaaaaaaaaaa)" in description

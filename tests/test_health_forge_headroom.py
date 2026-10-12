from __future__ import annotations

from datetime import UTC, datetime

from alan.health import (
    _describe,  # pyright: ignore[reportPrivateUsage]
    _severity_action,  # pyright: ignore[reportPrivateUsage]
    compute_health_state,
)
from alan.teams.triage_pregate import ClassifiedSurface


def test_severity_action_forge_low_headroom_is_medium_deep_link() -> None:
    sev, action = _severity_action("forge_degraded", {"forge_low_headroom"})
    assert sev == "medium"
    assert action == "deep_link"


def test_severity_action_forge_rate_limited_stays_high_deep_link() -> None:
    sev, action = _severity_action("forge_degraded", {"forge_rate_limited"})
    assert sev == "high"
    assert action == "deep_link"


def test_describe_forge_low_headroom_reports_remaining_and_limit() -> None:
    desc = _describe(
        "forge_degraded", "forge_degraded", {"forge_low_headroom"},
        {"remaining": 42, "limit": 5000},
    )
    assert "42" in desc
    assert "5000" in desc


def test_describe_forge_low_headroom_without_numbers_uses_generic_copy() -> None:
    desc = _describe("forge_degraded", "forge_degraded", {"forge_low_headroom"}, {})
    assert desc == "GitHub API quota headroom is low"


def test_describe_forge_degraded_paused_copy_unaffected_by_headroom_branch() -> None:
    desc = _describe("forge_degraded", "forge_degraded", {"forge_rate_limited"}, {})
    assert desc == "GitHub API degraded (rate limited) — dispatch paused"


def test_forge_low_headroom_surface_produces_medium_resolvable_finding() -> None:
    surface = ClassifiedSurface(
        "forge_degraded", "forge_degraded", False, "forge_low_headroom",
        {"remaining": 100, "limit": 5000}, resolvable=True,
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0
    assert len(state.resolvable_findings) == 1
    finding = state.resolvable_findings[0]
    assert finding.severity == "medium"
    assert finding.action == "deep_link"
    assert "100" in finding.description
    assert "5000" in finding.description


def test_forge_rate_limited_surface_still_blocks_and_is_not_resolvable() -> None:
    surface = ClassifiedSurface(
        "forge_degraded", "forge_degraded", True, "forge_rate_limited",
        {"reset_at": datetime.now(UTC).timestamp() + 600.0},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert state.blocked_count == 1
    assert state.resolvable_findings == []

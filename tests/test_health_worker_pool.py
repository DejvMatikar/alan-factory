from __future__ import annotations

from datetime import UTC, datetime

from alan.health import (
    HealthFinding,
    _build_finding,
    _describe_worker_pool,
    _finding_to_dict,
    compute_health_state,
)
from alan.teams.triage_pregate import ClassifiedSurface


def test_describe_worker_pool_names_dispatch_loop_threads_not_workers() -> None:
    desc = _describe_worker_pool({"alive": 1, "configured": 4})
    assert "Dispatch loop threads: 1/4 live" in desc
    assert "workers alive" not in desc


def test_describe_worker_pool_notes_no_running_jobs_or_live_processes() -> None:
    desc = _describe_worker_pool({"alive": 0, "configured": 2})
    assert "no running jobs or live worker processes found" in desc


def _make_finding(**overrides: object) -> HealthFinding:
    base: dict[str, object] = {
        "finding_id": "worker_pool",
        "kind": "worker_pool",
        "severity": "critical",
        "description": "d",
        "workspace_id": None,
        "pr_number": None,
        "blocker_flags": [],
        "action": "deep_link",
    }
    base.update(overrides)
    return HealthFinding(**base)  # type: ignore[arg-type]


def test_finding_to_dict_round_trips_observed_at() -> None:
    finding = _make_finding(observed_at="2026-08-30T00:00:00+00:00")
    rebuilt = _build_finding(_finding_to_dict(finding))
    assert rebuilt.observed_at == "2026-08-30T00:00:00+00:00"


def test_build_finding_legacy_dict_without_observed_at_parses_to_none() -> None:
    finding = _build_finding({
        "finding_id": "worker_pool",
        "kind": "worker_pool",
        "severity": "critical",
        "description": "d",
        "action": "deep_link",
    })
    assert finding.observed_at is None


def test_worker_pool_finding_carries_observed_at_from_surface_obs() -> None:
    surface = ClassifiedSurface(
        "worker_pool", "worker_pool", True, "worker_deficit:2",
        {"alive": 0, "configured": 2, "observed_at": "2026-08-30T00:00:00+00:00"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.findings[0].observed_at == "2026-08-30T00:00:00+00:00"


def test_worker_pool_finding_observed_at_none_when_obs_missing_key() -> None:
    surface = ClassifiedSurface("worker_pool", "worker_pool", True, "worker_deficit:2", {})
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.findings[0].observed_at is None


def test_worker_pool_verdict_aggregation_is_unchanged_by_observed_at() -> None:
    surface = ClassifiedSurface(
        "worker_pool", "worker_pool", True, "worker_deficit:1",
        {"alive": 1, "configured": 2, "observed_at": "2026-08-30T00:00:00+00:00"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert len(state.findings) == 1
    assert state.findings[0].severity == "critical"

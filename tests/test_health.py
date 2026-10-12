from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import alan.workspace as workspace_mod
from alan.health import (
    _COMPLETED_FINDING_IDS_KEY,  # pyright: ignore[reportPrivateUsage]
    _STALE_SECONDS,
    HEALTH_META_KEY,
    SWEEP_CADENCE_SECONDS,
    HealthState,
    _build_task_severity_action,  # pyright: ignore[reportPrivateUsage]
    _load_finding,
    _load_resolvable_findings,  # pyright: ignore[reportPrivateUsage]
    _parse_blocker_flags,
    _prune_stale_completed,  # pyright: ignore[reportPrivateUsage]
    _severity_action,
    _split_surfaces,  # pyright: ignore[reportPrivateUsage]
    _surface_to_finding,  # pyright: ignore[reportPrivateUsage]
    add_completed_finding,
    compute_health_state,
    is_stale,
    load_completed_finding_ids,
    load_health_state,
    resolve_digest_deadline,
    save_health_state,
)
from alan.queue import Queue
from alan.teams.triage_pregate import ClassifiedSurface


def _make_surface(
    surface_id: str,
    kind: str,
    is_blocked: bool,
    blocker_reason: str = "",
    obs: dict[str, Any] | None = None,
) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=surface_id,
        kind=kind,
        is_blocked=is_blocked,
        blocker_reason=blocker_reason,
        obs=obs or {},
    )


def _make_health_state(verdict: str = "healthy", blocked: int = 0) -> HealthState:
    return HealthState(
        verdict=verdict,  # type: ignore[arg-type]
        blocked_count=blocked,
        in_flight_count=0,
        findings=[],
        computed_at=datetime.now(UTC).isoformat(),
    )


def test_sweep_cadence_seconds_is_1800() -> None:
    assert SWEEP_CADENCE_SECONDS == 1800.0


def test_stale_seconds_is_twice_cadence() -> None:
    assert _STALE_SECONDS == SWEEP_CADENCE_SECONDS * 2


def test_compute_health_state_empty_healthy() -> None:
    surfaces: list[ClassifiedSurface] = []
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0
    assert state.in_flight_count == 0
    assert state.findings == []


def test_compute_health_state_all_unblocked_healthy() -> None:
    surfaces = [
        _make_surface("s1", "build_task", False),
        _make_surface("main_ci", "main_ci", False),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0


def test_compute_health_state_blocked_degraded() -> None:
    surfaces = [
        _make_surface("s1", "build_task", True, "job_failed", {"pr_number": None}),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.verdict == "degraded"
    assert state.blocked_count == 1
    assert len(state.findings) == 1


def test_compute_health_state_in_flight_count() -> None:
    surfaces = [
        _make_surface("s1", "build_task", True, "job_failed", {"job_running": True}),
        _make_surface("s2", "build_task", True, "ws_stuck", {"job_running": False}),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.blocked_count == 2
    assert state.in_flight_count == 1


def test_compute_health_state_computed_at_is_iso() -> None:
    now = datetime.now(UTC)
    state = compute_health_state([], now)
    parsed = datetime.fromisoformat(state.computed_at)
    assert parsed.tzinfo is not None


def test_severity_action_build_pr_checks_failing() -> None:
    sev, action = _build_task_severity_action({"pr_checks_failing"}, False)
    assert sev == "critical"
    assert action == "dispatch_remediation"


def test_severity_action_build_job_failed() -> None:
    sev, action = _build_task_severity_action({"job_failed"}, False)
    assert sev == "high"
    assert action == "dispatch_remediation"


def test_severity_action_build_pr_approved_unmerged() -> None:
    sev, action = _build_task_severity_action({"pr_approved_unmerged"}, False)
    assert sev == "high"
    assert action == "merge_pr"


def test_severity_action_build_ws_stuck_only() -> None:
    sev, action = _build_task_severity_action({"ws_stuck"}, False)
    assert sev == "medium"
    assert action == "deep_link"


def test_severity_action_main_ci() -> None:
    sev, action = _severity_action("main_ci", {"ci_failure"})
    assert sev == "critical"
    assert action == "dispatch_remediation"


def test_severity_action_deploy() -> None:
    sev, action = _severity_action("deploy", {"deploy_failure"})
    assert sev == "critical"
    assert action == "dispatch_remediation"


def test_severity_action_recurring_errors() -> None:
    sev, action = _severity_action("recurring_errors", {"recurring_errors:2"})
    assert sev == "high"
    assert action == "dispatch_remediation"


def test_finding_for_build_task_pr_checks() -> None:
    surface = _make_surface(
        "task-123", "build_task", True, "pr_checks_failing",
        {"pr_number": 42, "job_running": False},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.finding_id == "task-123"
    assert f.kind == "build_task"
    assert f.severity == "critical"
    assert f.action == "dispatch_remediation"
    assert f.workspace_id == "task-123"
    assert f.pr_number == 42
    assert "pr_checks_failing" in f.blocker_flags


def test_finding_for_build_task_ws_stuck() -> None:
    surface = _make_surface("task-456", "build_task", True, "ws_stuck", {})
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.action == "deep_link"
    assert f.severity == "medium"
    assert "awaiting human input" in f.description


def test_finding_for_build_task_ghost_with_subtasks_suppressed() -> None:
    surface = _make_surface(
        "task-ghost-multi", "build_task", True, "ws_stuck",
        {"has_subtasks": True},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.resolvable_findings == []
    f = state.findings[0]
    assert f.action == "deep_link"
    assert f.severity == "medium"
    assert "looks finished" in f.description
    assert "multiple subtasks" in f.description
    assert "Recovery:" in f.description


def test_finding_for_build_task_has_subtasks_yields_to_higher_priority_flags() -> None:
    surface = _make_surface(
        "task-ghost-multi-2", "build_task", True, "job_failed",
        {"has_subtasks": True},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert "has a failed job" in f.description
    assert "multiple subtasks" not in f.description


def test_finding_for_main_ci() -> None:
    surface = _make_surface("main_ci", "main_ci", True, "ci_failure", {"conclusion": "FAILURE"})
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "critical"
    assert "FAILURE" in f.description
    assert f.workspace_id is None
    assert f.pr_number is None


def test_finding_for_deploy() -> None:
    surface = _make_surface("deploy", "deploy", True, "deploy_failure", {"conclusion": "FAILURE"})
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "critical"
    assert f.action == "dispatch_remediation"
    assert "deploy is failing" in f.description
    assert "FAILURE" in f.description
    assert f.workspace_id is None


def test_finding_for_main_ci_includes_repo() -> None:
    surface = _make_surface(
        "main_ci:o/r", "main_ci", True, "ci_failure",
        {"conclusion": "FAILURE", "repo": "o/r"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.repo == "o/r"


def test_finding_for_deploy_includes_repo() -> None:
    surface = _make_surface(
        "deploy:o/r", "deploy", True, "deploy_failure",
        {"conclusion": "FAILURE", "repo": "o/r"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.repo == "o/r"


def test_finding_repo_none_when_obs_lacks_repo() -> None:
    surface = _make_surface("s1", "build_task", True, "ws_stuck", {})
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.repo is None


def test_repo_round_trips_through_save_load(queue: Queue) -> None:
    surfaces = [_make_surface("main_ci:o/r", "main_ci", True, "ci_failure", {"repo": "o/r"})]
    state = compute_health_state(surfaces, datetime.now(UTC))
    save_health_state(queue, state)
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].repo == "o/r"


def test_build_finding_defaults_repo_to_none_when_missing(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps({
        "verdict": "degraded", "blocked_count": 1, "in_flight_count": 0,
        "findings": [{"finding_id": "s1", "kind": "build_task"}],
        "computed_at": datetime.now(UTC).isoformat(),
    }))
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].repo is None


def test_finding_engine_key_derived_from_workspace(ws_root: Path) -> None:
    ws = workspace_mod.create(
        "Health Engine WS", "d", root=ws_root, engine_key="sprocket-co/sprocket"
    )
    surface = _make_surface(ws.task_id, "build_task", True, "ws_stuck", {})
    state = compute_health_state([surface], datetime.now(UTC), workspaces_root=ws_root)
    f = state.findings[0]
    assert f.engine_key == "sprocket-co/sprocket"


def test_finding_engine_key_none_when_workspace_missing(tmp_path: Path) -> None:
    surface = _make_surface("nonexistent-ws", "build_task", True, "ws_stuck", {})
    state = compute_health_state([surface], datetime.now(UTC), workspaces_root=tmp_path)
    f = state.findings[0]
    assert f.engine_key is None


def test_finding_engine_key_none_when_no_workspace_id() -> None:
    surface = _make_surface(
        "main_ci:o/r", "main_ci", True, "ci_failure", {"conclusion": "FAILURE", "repo": "o/r"}
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.engine_key is None


def test_engine_key_round_trips_through_save_load(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create(
        "Health Engine RT", "d", root=ws_root, engine_key="sprocket-co/sprocket"
    )
    surfaces = [_make_surface(ws.task_id, "build_task", True, "ws_stuck", {})]
    state = compute_health_state(surfaces, datetime.now(UTC), workspaces_root=ws_root)
    save_health_state(queue, state)
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].engine_key == "sprocket-co/sprocket"


def test_build_finding_defaults_engine_key_to_none_when_missing(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps({
        "verdict": "degraded", "blocked_count": 1, "in_flight_count": 0,
        "findings": [{"finding_id": "s1", "kind": "build_task"}],
        "computed_at": datetime.now(UTC).isoformat(),
    }))
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].engine_key is None


def test_finding_for_recurring_errors() -> None:
    surface = _make_surface(
        "recurring_errors", "recurring_errors", True, "recurring_errors:3",
        {"recurring_signatures": ["sig1", "sig2", "sig3"]},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "high"
    assert "3" in f.description


def test_finding_pr_approved_unmerged_description() -> None:
    surface = _make_surface(
        "task-789", "build_task", True, "pr_approved_unmerged",
        {"pr_number": 55},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert "approved" in f.description
    assert "PR #55" in f.description


def test_parse_blocker_flags_empty() -> None:
    assert _parse_blocker_flags("") == []


def test_parse_blocker_flags_single() -> None:
    assert _parse_blocker_flags("job_failed") == ["job_failed"]


def test_parse_blocker_flags_multi() -> None:
    result = _parse_blocker_flags("job_failed,ws_stuck")
    assert "job_failed" in result
    assert "ws_stuck" in result


def test_save_load_round_trip(queue: Queue) -> None:
    now = datetime.now(UTC)
    surfaces = [
        _make_surface("s1", "build_task", True, "job_failed", {"pr_number": 10}),
    ]
    state = compute_health_state(surfaces, now)
    save_health_state(queue, state)
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.verdict == "degraded"
    assert loaded.blocked_count == 1
    assert len(loaded.findings) == 1
    assert loaded.findings[0].finding_id == "s1"
    assert loaded.findings[0].pr_number == 10


def test_load_health_state_none_when_missing(queue: Queue) -> None:
    result = load_health_state(queue)
    assert result is None


def test_load_health_state_tolerates_corrupt_json(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, "not-valid-json")
    result = load_health_state(queue)
    assert result is None


def test_load_health_state_tolerates_non_dict_json(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps([1, 2, 3]))
    result = load_health_state(queue)
    assert result is None


def test_load_health_state_tolerates_missing_fields(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps({"verdict": "healthy"}))
    result = load_health_state(queue)
    assert result is not None
    assert result.verdict == "healthy"
    assert result.findings == []


def test_load_health_state_skips_malformed_findings(queue: Queue) -> None:
    data = {
        "verdict": "degraded",
        "blocked_count": 1,
        "in_flight_count": 0,
        "findings": [None, "bad", {"finding_id": "ok", "kind": "main_ci"}],
        "computed_at": datetime.now(UTC).isoformat(),
    }
    queue.set_meta(HEALTH_META_KEY, json.dumps(data))
    result = load_health_state(queue)
    assert result is not None
    assert len(result.findings) == 1
    assert result.findings[0].finding_id == "ok"


def test_is_stale_fresh_state_false(queue: Queue) -> None:
    state = _make_health_state()
    assert not is_stale(state)


def test_is_stale_old_state_true(queue: Queue) -> None:
    old_ts = (datetime.now(UTC) - timedelta(seconds=_STALE_SECONDS + 10)).isoformat()
    state = HealthState(
        verdict="healthy",
        blocked_count=0,
        in_flight_count=0,
        findings=[],
        computed_at=old_ts,
    )
    assert is_stale(state)


def test_is_stale_empty_computed_at_true() -> None:
    state = HealthState(
        verdict="healthy",
        blocked_count=0,
        in_flight_count=0,
        findings=[],
        computed_at="",
    )
    assert is_stale(state)


def test_is_stale_no_timezone_true() -> None:
    state = HealthState(
        verdict="healthy",
        blocked_count=0,
        in_flight_count=0,
        findings=[],
        computed_at="2026-06-02T10:00:00",
    )
    assert is_stale(state)


def test_compute_health_state_no_in_flight_for_nonbuild() -> None:
    surfaces = [
        _make_surface("main_ci", "main_ci", True, "ci_failure", {"job_running": True}),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.in_flight_count == 0


def test_finding_no_pr_when_no_pr_number() -> None:
    surface = _make_surface("task-no-pr", "build_task", True, "ws_stuck", {"pr_number": None})
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.pr_number is None


def test_finding_description_recurring_singular() -> None:
    surface = _make_surface(
        "recurring_errors", "recurring_errors", True, "recurring_errors:1",
        {"recurring_signatures": ["sig1"]},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert "pattern" in f.description
    assert "patterns" not in f.description


def test_finding_description_recurring_plural() -> None:
    surface = _make_surface(
        "recurring_errors", "recurring_errors", True, "recurring_errors:2",
        {"recurring_signatures": ["a", "b"]},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert "patterns" in f.description


def test_finding_description_recurring_lists_signatures() -> None:
    surface = _make_surface(
        "recurring_errors", "recurring_errors", True, "recurring_errors:2",
        {"recurring_signatures": [
            "external_gate_query_failed", "external_gate_rerun_failed",
        ]},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert "external_gate_query_failed" in f.description
    assert "external_gate_rerun_failed" in f.description


def test_describe_recurring_empty_signatures_omits_list() -> None:
    from alan.health import _describe_recurring
    desc = _describe_recurring({"recurring_signatures": []})
    assert desc == "Recurring errors detected (0 patterns)"
    assert ":" not in desc


def test_describe_recurring_ignores_non_string_signatures() -> None:
    from alan.health import _describe_recurring
    desc = _describe_recurring({"recurring_signatures": ["sig_a", 42, None]})
    assert desc == "Recurring errors detected (1 pattern): sig_a"


def test_pr_checks_failing_beats_other_flags() -> None:
    sev, action = _build_task_severity_action(
        {"pr_checks_failing", "ws_stuck", "job_failed"}, False
    )
    assert sev == "critical"
    assert action == "dispatch_remediation"


def test_severity_action_build_empty_flags() -> None:
    sev, action = _build_task_severity_action(set(), False)
    assert action == "deep_link"


def test_load_finding_exception_returns_none_direct() -> None:
    result = _load_finding({"pr_number": "not-an-int", "finding_id": "s1", "workspace_id": None})
    assert result is None


def test_load_finding_exception_in_try_returns_none(queue: Queue) -> None:
    data = {
        "verdict": "degraded",
        "blocked_count": 1,
        "in_flight_count": 0,
        "findings": [{"pr_number": "not-an-int", "finding_id": "s1", "workspace_id": None}],
        "computed_at": datetime.now(UTC).isoformat(),
    }
    queue.set_meta(HEALTH_META_KEY, json.dumps(data))
    result = load_health_state(queue)
    assert result is not None
    assert len(result.findings) == 0


def test_load_health_state_exception_in_inner_try(queue: Queue) -> None:
    data = {
        "verdict": "degraded",
        "blocked_count": "not-an-int",
        "in_flight_count": 0,
        "findings": [],
        "computed_at": datetime.now(UTC).isoformat(),
    }
    queue.set_meta(HEALTH_META_KEY, json.dumps(data))
    result = load_health_state(queue)
    assert result is None


def test_in_flight_field_set_on_finding_when_running(queue: Queue) -> None:
    surfaces = [
        _make_surface("s1", "build_task", True, "job_failed", {"job_running": True}),
        _make_surface("s2", "build_task", True, "ws_stuck", {"job_running": False}),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.findings[0].in_flight is True
    assert state.findings[1].in_flight is False


def test_in_flight_counted_in_in_flight_count() -> None:
    surfaces = [
        _make_surface("s1", "build_task", True, "job_failed", {"job_running": True}),
        _make_surface("s2", "build_task", True, "ws_stuck", {"job_running": False}),
    ]
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.in_flight_count == 1


def test_in_flight_round_trips_through_save_load(queue: Queue) -> None:
    surfaces = [_make_surface("s1", "build_task", True, "job_failed", {"job_running": True})]
    state = compute_health_state(surfaces, datetime.now(UTC))
    save_health_state(queue, state)
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].in_flight is True


def test_build_finding_defaults_in_flight_to_false_when_missing(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps({
        "verdict": "degraded", "blocked_count": 1, "in_flight_count": 0,
        "findings": [{"finding_id": "s1", "kind": "build_task"}],
        "computed_at": datetime.now(UTC).isoformat(),
    }))
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.findings[0].in_flight is False


def test_compute_health_state_merged_pr_build_task_not_counted() -> None:
    surface = _make_surface(
        "task-merged", "build_task", False, "ws_stuck",
        {"pr_state": "MERGED", "pr_number": 99},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0
    assert state.findings == []


def test_compute_health_state_closed_pr_build_task_not_counted() -> None:
    surface = _make_surface(
        "task-closed", "build_task", False, "job_failed",
        {"pr_state": "CLOSED", "pr_number": 100},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0
    assert state.findings == []


def test_compute_health_state_no_pr_ws_stuck_still_counted() -> None:
    surface = _make_surface(
        "task-no-pr-stuck", "build_task", True, "ws_stuck",
        {"pr_state": None, "pr_number": None},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert state.blocked_count == 1


def test_compute_health_state_mixed_merged_and_stuck() -> None:
    merged = _make_surface("task-merged", "build_task", False, "ws_stuck",
                           {"pr_state": "MERGED"})
    stuck = _make_surface("task-stuck", "build_task", True, "ws_stuck",
                          {"pr_state": None})
    state = compute_health_state([merged, stuck], datetime.now(UTC))
    assert state.blocked_count == 1
    assert state.findings[0].finding_id == "task-stuck"
    assert not any(f.finding_id == "task-merged" for f in state.findings)


def test_compute_health_state_non_build_findings_unaffected_by_merged_pr() -> None:
    merged = _make_surface("task-merged", "build_task", False, "ws_stuck",
                           {"pr_state": "MERGED"})
    main_ci = _make_surface("main_ci", "main_ci", True, "ci_failure", {"conclusion": "FAILURE"})
    recurring = _make_surface(
        "recurring_errors", "recurring_errors", True, "recurring_errors:2",
        {"recurring_signatures": ["sig1", "sig2"]},
    )
    state = compute_health_state([merged, main_ci, recurring], datetime.now(UTC))
    assert state.blocked_count == 2
    assert not any(f.finding_id == "task-merged" for f in state.findings)
    assert any(f.finding_id == "main_ci" for f in state.findings)
    assert any(f.finding_id == "recurring_errors" for f in state.findings)


def _make_resolvable_surface(surface_id: str) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=surface_id,
        kind="build_task",
        is_blocked=False,
        blocker_reason="ws_stuck",
        obs={},
        resolvable=True,
    )


def test_split_surfaces_empty() -> None:
    blocked, resolvable = _split_surfaces([])
    assert blocked == []
    assert resolvable == []


def test_split_surfaces_all_blocked() -> None:
    surfaces = [_make_surface("s1", "build_task", True)]
    blocked, resolvable = _split_surfaces(surfaces)
    assert len(blocked) == 1
    assert resolvable == []


def test_split_surfaces_all_resolvable() -> None:
    surfaces = [_make_resolvable_surface("s1")]
    blocked, resolvable = _split_surfaces(surfaces)
    assert blocked == []
    assert len(resolvable) == 1


def test_split_surfaces_resolvable_and_blocked_goes_to_blocked() -> None:
    surface = ClassifiedSurface(
        surface_id="s1", kind="build_task", is_blocked=True,
        blocker_reason="ws_stuck", obs={}, resolvable=True,
    )
    blocked, resolvable = _split_surfaces([surface])
    assert len(blocked) == 1
    assert resolvable == []


def test_split_surfaces_mixed() -> None:
    surfaces = [
        _make_surface("s1", "build_task", True),
        _make_surface("s2", "build_task", False),
        _make_resolvable_surface("s3"),
    ]
    blocked, resolvable = _split_surfaces(surfaces)
    assert len(blocked) == 1
    assert blocked[0].surface_id == "s1"
    assert len(resolvable) == 1
    assert resolvable[0].surface_id == "s3"


def test_surface_to_finding_resolvable_action_and_severity() -> None:
    surface = _make_resolvable_surface("task-done")
    finding = _surface_to_finding(surface)
    assert finding.severity == "medium"
    assert finding.action == "complete_workspace"
    assert "task-done" in finding.description
    assert "merged" in finding.description


def test_compute_health_state_resolvable_not_in_findings() -> None:
    surface = _make_resolvable_surface("task-ghost")
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"
    assert state.blocked_count == 0
    assert not any(f.finding_id == "task-ghost" for f in state.findings)
    assert any(f.finding_id == "task-ghost" for f in state.resolvable_findings)


def test_compute_health_state_resolvable_and_blocked_mixed() -> None:
    blocked_s = _make_surface("s-blocked", "build_task", True, "ws_stuck")
    resolvable_s = _make_resolvable_surface("s-ghost")
    state = compute_health_state([blocked_s, resolvable_s], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert state.blocked_count == 1
    assert len(state.resolvable_findings) == 1
    assert state.resolvable_findings[0].finding_id == "s-ghost"


def test_resolvable_findings_round_trip(queue: Queue) -> None:
    surface = _make_resolvable_surface("task-ghost")
    state = compute_health_state([surface], datetime.now(UTC))
    save_health_state(queue, state)
    loaded = load_health_state(queue)
    assert loaded is not None
    assert len(loaded.resolvable_findings) == 1
    assert loaded.resolvable_findings[0].finding_id == "task-ghost"
    assert loaded.resolvable_findings[0].action == "complete_workspace"


def test_load_resolvable_findings_missing_key(queue: Queue) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps({
        "verdict": "healthy", "blocked_count": 0, "in_flight_count": 0,
        "findings": [], "computed_at": datetime.now(UTC).isoformat(),
    }))
    loaded = load_health_state(queue)
    assert loaded is not None
    assert loaded.resolvable_findings == []


def test_load_resolvable_findings_non_list_ignored() -> None:
    d: dict[str, Any] = {"resolvable_findings": "not-a-list"}
    assert _load_resolvable_findings(d) == []


def test_add_completed_finding_stores_id(queue: Queue) -> None:
    add_completed_finding(queue, "task-done")
    stored = json.loads(queue.get_meta(_COMPLETED_FINDING_IDS_KEY) or "[]")
    assert "task-done" in stored


def test_add_completed_finding_is_idempotent(queue: Queue) -> None:
    add_completed_finding(queue, "task-done")
    add_completed_finding(queue, "task-done")
    stored = json.loads(queue.get_meta(_COMPLETED_FINDING_IDS_KEY) or "[]")
    assert stored.count("task-done") == 1


def test_add_completed_finding_accumulates_multiple(queue: Queue) -> None:
    add_completed_finding(queue, "task-a")
    add_completed_finding(queue, "task-b")
    ids = load_completed_finding_ids(queue)
    assert "task-a" in ids
    assert "task-b" in ids


def test_add_completed_finding_tolerates_corrupt_existing(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, "bad-json")
    add_completed_finding(queue, "task-new")
    ids = load_completed_finding_ids(queue)
    assert "task-new" in ids


def test_add_completed_finding_tolerates_non_list_existing(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps({"key": "val"}))
    add_completed_finding(queue, "task-new")
    ids = load_completed_finding_ids(queue)
    assert "task-new" in ids


def test_load_completed_finding_ids_empty_when_missing(queue: Queue) -> None:
    assert load_completed_finding_ids(queue) == set()


def test_load_completed_finding_ids_corrupt_json(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, "not-json")
    assert load_completed_finding_ids(queue) == set()


def test_load_completed_finding_ids_non_list(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps({"key": "val"}))
    assert load_completed_finding_ids(queue) == set()


def test_prune_stale_completed_noop_when_missing(queue: Queue) -> None:
    _prune_stale_completed(queue, {"task-a"})
    assert queue.get_meta(_COMPLETED_FINDING_IDS_KEY) is None


def test_prune_stale_completed_removes_stale_ids(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps(["task-a", "task-b"]))
    _prune_stale_completed(queue, {"task-a"})
    stored = json.loads(queue.get_meta(_COMPLETED_FINDING_IDS_KEY) or "[]")
    assert "task-a" in stored
    assert "task-b" not in stored


def test_prune_stale_completed_tolerates_corrupt_json(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, "not-valid-json")
    _prune_stale_completed(queue, {"task-a"})
    assert queue.get_meta(_COMPLETED_FINDING_IDS_KEY) == "not-valid-json"


def test_prune_stale_completed_tolerates_non_list_json(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps({"key": "val"}))
    _prune_stale_completed(queue, {"task-a"})
    assert json.loads(queue.get_meta(_COMPLETED_FINDING_IDS_KEY) or "null") == {"key": "val"}


def test_save_health_state_prunes_completed_not_in_resolvable(queue: Queue) -> None:
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps(["task-gone", "task-active"]))
    surface = _make_resolvable_surface("task-active")
    state = compute_health_state([surface], datetime.now(UTC))
    save_health_state(queue, state)
    stored = json.loads(queue.get_meta(_COMPLETED_FINDING_IDS_KEY) or "[]")
    assert "task-active" in stored
    assert "task-gone" not in stored


def test_save_health_state_prune_completed_noop_when_absent(queue: Queue) -> None:
    save_health_state(queue, _make_health_state("healthy", 0))
    assert queue.get_meta(_COMPLETED_FINDING_IDS_KEY) is None


def _make_fr(*, floor_alert: bool = False) -> Any:
    from alan.finisher_rate import FinisherRate, FunnelCounts, LeakReasons
    rate = 0.3 if floor_alert else 0.8
    return FinisherRate(
        rate=rate,
        counts=FunnelCounts(10, 9, 8, 7, 6, int(rate * 10), autonomous=4),
        leak_reasons=LeakReasons(crash_reasons={}, convergence_kinds={}),
        low_data=False,
        window_days=30,
        sample_size=10,
        floor_alert=floor_alert,
        autonomous_rate=0.4,
    )


def test_save_load_finisher_rate_round_trip(queue: Queue) -> None:
    from alan.health import _FINISHER_RATE_META_KEY, load_finisher_rate, save_finisher_rate
    fr = _make_fr()
    save_finisher_rate(queue, fr)
    raw = queue.get_meta(_FINISHER_RATE_META_KEY)
    assert raw is not None
    loaded = load_finisher_rate(queue)
    assert loaded is not None
    assert loaded.window_days == fr.window_days
    assert loaded.sample_size == fr.sample_size
    assert loaded.floor_alert == fr.floor_alert
    assert loaded.autonomous_rate == fr.autonomous_rate
    assert loaded.counts.autonomous == fr.counts.autonomous


def test_load_finisher_rate_none_when_missing(queue: Queue) -> None:
    from alan.health import load_finisher_rate
    assert load_finisher_rate(queue) is None


def test_load_finisher_rate_tolerates_corrupt_json(queue: Queue) -> None:
    from alan.health import _FINISHER_RATE_META_KEY, load_finisher_rate
    queue.set_meta(_FINISHER_RATE_META_KEY, "not-json{{")
    assert load_finisher_rate(queue) is None


def test_load_finisher_rate_tolerates_non_dict_json(queue: Queue) -> None:
    from alan.health import _FINISHER_RATE_META_KEY, load_finisher_rate
    queue.set_meta(_FINISHER_RATE_META_KEY, "[1, 2, 3]")
    assert load_finisher_rate(queue) is None


def test_load_finisher_rate_tolerates_fr_from_dict_exception(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import _FINISHER_RATE_META_KEY, load_finisher_rate

    def _boom(d: Any) -> Any:
        raise ValueError("parse failed")

    monkeypatch.setattr("alan.health.fr_from_dict", _boom)
    queue.set_meta(_FINISHER_RATE_META_KEY, '{"rate": 0.5}')
    assert load_finisher_rate(queue) is None


def test_severity_action_finisher_regression_medium_deep_link() -> None:
    sev, action = _severity_action("finisher_regression", set())
    assert sev == "medium"
    assert action == "deep_link"


def test_describe_finisher_regression_shows_rate() -> None:
    from alan.health import _describe_finisher_regression
    obs = {"rate": 0.3, "floor": 0.5, "sample_size": 12}
    desc = _describe_finisher_regression(obs)
    assert "30.0%" in desc
    assert "50.0%" in desc
    assert "12" in desc


def test_describe_finisher_regression_none_rate() -> None:
    from alan.health import _describe_finisher_regression
    obs = {"rate": None, "floor": 0.5, "sample_size": 5}
    desc = _describe_finisher_regression(obs)
    assert "unknown" in desc


def test_describe_finisher_regression_uses_floor_default_when_missing() -> None:
    from alan.finisher_rate import FINISHER_FLOOR
    from alan.health import _describe_finisher_regression
    obs = {"rate": 0.2, "sample_size": 10}
    desc = _describe_finisher_regression(obs)
    assert f"{FINISHER_FLOOR:.1%}" in desc


def test_describe_main_ci_helper_formats_conclusion() -> None:
    from alan.health import _describe_main_ci
    desc = _describe_main_ci({"conclusion": "failure"})
    assert "failure" in desc


def test_describe_main_ci_helper_unknown_fallback() -> None:
    from alan.health import _describe_main_ci
    desc = _describe_main_ci({})
    assert "unknown" in desc


def test_resolve_digest_deadline_defaults_to_utc_9(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DIGEST_TZ", raising=False)
    monkeypatch.delenv("ALAN_DIGEST_HOUR", raising=False)
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    deadline = resolve_digest_deadline(now)
    assert deadline == datetime(2026, 7, 5, 9, 0, tzinfo=UTC)


def test_resolve_digest_deadline_respects_custom_tz_and_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DIGEST_TZ", "America/New_York")
    monkeypatch.setenv("ALAN_DIGEST_HOUR", "7")
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    deadline = resolve_digest_deadline(now)
    assert deadline.hour == 7
    assert deadline.tzinfo is not None
    assert deadline.utcoffset() is not None and deadline.utcoffset() != timedelta(0)


def test_resolve_digest_deadline_falls_back_to_utc_on_unknown_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DIGEST_TZ", "Not/A_Real_Zone")
    monkeypatch.delenv("ALAN_DIGEST_HOUR", raising=False)
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    deadline = resolve_digest_deadline(now)
    assert deadline == datetime(2026, 7, 5, 9, 0, tzinfo=UTC)


def test_resolve_digest_deadline_falls_back_to_9_on_non_numeric_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DIGEST_TZ", raising=False)
    monkeypatch.setenv("ALAN_DIGEST_HOUR", "not-a-number")
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    assert resolve_digest_deadline(now).hour == 9


def test_resolve_digest_deadline_falls_back_to_9_on_out_of_range_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DIGEST_TZ", raising=False)
    monkeypatch.setenv("ALAN_DIGEST_HOUR", "24")
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    assert resolve_digest_deadline(now).hour == 9


def test_severity_action_behavioral_regression_medium_deep_link() -> None:
    sev, action = _severity_action("planner_behavioral_regression", set())
    assert sev == "medium"
    assert action == "deep_link"


def test_describe_behavioral_regression_shows_score_and_baseline() -> None:
    from alan.health import _describe_behavioral_regression

    desc = _describe_behavioral_regression({"score": 0.62, "baseline": 0.83})
    assert "0.620" in desc
    assert "0.830" in desc


def test_describe_behavioral_regression_unknown_when_missing() -> None:
    from alan.health import _describe_behavioral_regression

    desc = _describe_behavioral_regression({})
    assert "unknown" in desc


def test_behavioral_regression_surface_none_when_not_regressed() -> None:
    from alan.health import behavioral_regression_surface
    from alan.learning_metrics import LearningMetrics

    metrics = LearningMetrics(
        first_pass_approval_rate=None, mean_fix_cycles_to_merge=None, sample_size=0
    )
    assert behavioral_regression_surface(metrics) is None


def test_behavioral_regression_surface_builds_when_regressed() -> None:
    from alan.health import behavioral_regression_surface
    from alan.learning_metrics import LearningMetrics

    metrics = LearningMetrics(
        first_pass_approval_rate=None,
        mean_fix_cycles_to_merge=None,
        sample_size=0,
        planner_behavioral_score=0.5,
        planner_behavioral_baseline=0.8,
        planner_behavioral_regression=True,
    )
    surface = behavioral_regression_surface(metrics)
    assert surface is not None
    assert surface.kind == "planner_behavioral_regression"
    assert surface.is_blocked is True
    assert surface.obs == {"score": 0.5, "baseline": 0.8}


def test_compute_health_state_includes_behavioral_regression_finding() -> None:
    from alan.health import behavioral_regression_surface
    from alan.learning_metrics import LearningMetrics

    metrics = LearningMetrics(
        first_pass_approval_rate=None,
        mean_fix_cycles_to_merge=None,
        sample_size=0,
        planner_behavioral_score=0.5,
        planner_behavioral_baseline=0.8,
        planner_behavioral_regression=True,
    )
    surface = behavioral_regression_surface(metrics)
    assert surface is not None
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert len(state.findings) == 1
    finding = state.findings[0]
    assert finding.finding_id == "planner_behavioral_regression"
    assert finding.severity == "medium"
    assert finding.action == "deep_link"
    assert finding.workspace_id is None
    assert finding.pr_number is None
    assert "0.500" in finding.description
    assert "0.800" in finding.description


def test_severity_action_retention_near_cap_high_deep_link() -> None:
    sev, action = _severity_action("retention_near_cap", set())
    assert sev == "high"
    assert action == "deep_link"


def test_describe_retention_near_cap_shows_used_and_cap() -> None:
    from alan.health import _describe_retention_near_cap

    desc = _describe_retention_near_cap(
        {"title": "Failure Triage Sweep", "used": 901, "cap": 999}
    )
    assert "Failure Triage Sweep" in desc
    assert "901" in desc
    assert "999" in desc


def test_describe_retention_near_cap_falls_back_to_task_id() -> None:
    from alan.health import _describe_retention_near_cap

    desc = _describe_retention_near_cap({"task_id": "task-abc", "used": 5, "cap": 10})
    assert "task-abc" in desc


def test_finding_for_retention_near_cap_sets_workspace_id_from_task_id() -> None:
    surface = _make_surface(
        "retention_near_cap:task-abc",
        "retention_near_cap",
        True,
        "retention_near_cap",
        {"task_id": "task-abc", "team": "triage", "title": "Failure Triage Sweep",
         "used": 901, "cap": 999},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "high"
    assert f.action == "deep_link"
    assert f.workspace_id == "task-abc"
    assert "Failure Triage Sweep" in f.description


def test_finding_for_retention_near_cap_no_workspace_id_when_task_id_missing() -> None:
    surface = _make_surface(
        "retention_near_cap:x", "retention_near_cap", True, "retention_near_cap", {}
    )
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.findings[0].workspace_id is None


def test_severity_action_config_quarantine_high_deep_link() -> None:
    sev, action = _severity_action("config_quarantine", set())
    assert sev == "high"
    assert action == "deep_link"


def test_describe_config_quarantine_names_path_and_error() -> None:
    from alan.health import _describe_config_quarantine

    desc = _describe_config_quarantine(
        {"path": "config/engines.d/widget.yaml", "error": "boom"}
    )
    assert "config/engines.d/widget.yaml" in desc
    assert "boom" in desc


def test_describe_config_quarantine_falls_back_when_missing() -> None:
    from alan.health import _describe_config_quarantine

    desc = _describe_config_quarantine({})
    assert "unknown path" in desc
    assert "unknown error" in desc


def test_finding_for_config_quarantine() -> None:
    surface = _make_surface(
        "config_quarantine:acme/widget",
        "config_quarantine",
        True,
        "config_quarantine",
        {"path": "config/engines.d/widget.yaml", "error": "boom", "repo": "acme/widget"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "high"
    assert f.action == "deep_link"
    assert "config/engines.d/widget.yaml" in f.description
    assert "boom" in f.description
    assert f.workspace_id is None
    assert f.pr_number is None
    assert f.repo == "acme/widget"


def test_severity_action_credential_expiry_high_deep_link() -> None:
    sev, action = _severity_action("credential_expiry", set())
    assert sev == "high"
    assert action == "deep_link"


def test_describe_credential_expiry_names_credential_and_date() -> None:
    from alan.health import _describe_credential_expiry

    desc = _describe_credential_expiry({"name": "engine-a:transport", "expires_at": "2099-01-01"})
    assert "engine-a:transport" in desc
    assert "2099-01-01" in desc


def test_describe_credential_expiry_falls_back_when_missing() -> None:
    from alan.health import _describe_credential_expiry

    desc = _describe_credential_expiry({})
    assert "unknown credential" in desc
    assert "an unrecorded date" in desc


def test_finding_for_credential_expiry() -> None:
    surface = _make_surface(
        "credential_expiry:engine-a:transport",
        "credential_expiry",
        True,
        "credential_expiry",
        {"name": "engine-a:transport", "expires_at": "2099-01-01", "engine_key": "engine-a"},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    f = state.findings[0]
    assert f.severity == "high"
    assert f.action == "deep_link"
    assert "engine-a:transport" in f.description
    assert "2099-01-01" in f.description
    assert f.workspace_id is None
    assert f.pr_number is None
    assert f.engine_key == "engine-a"


def test_finding_engine_key_prefers_obs_engine_key_over_workspace_lookup(
    ws_root: Path,
) -> None:
    ws = workspace_mod.create(
        "Health Engine Obs Override", "d", root=ws_root, engine_key="sprocket-co/sprocket"
    )
    surface = _make_surface(
        ws.task_id, "credential_expiry", True, "credential_expiry", {"engine_key": "acme/other"}
    )
    state = compute_health_state([surface], datetime.now(UTC), workspaces_root=ws_root)
    assert state.findings[0].engine_key == "acme/other"


def test_resolve_digest_deadline_empty_env_vars_use_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DIGEST_TZ", "")
    monkeypatch.setenv("ALAN_DIGEST_HOUR", "")
    now = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)
    deadline = resolve_digest_deadline(now)
    assert deadline == datetime(2026, 7, 5, 9, 0, tzinfo=UTC)

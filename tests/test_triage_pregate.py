from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, RepoIdentity
from alan.handover import Handover, save_handover
from alan.queue import Queue
from alan.teams.triage_pregate import (
    _GHOST_WS_STATUSES,
    _SURFACE_STATE_META_KEY,
    ClassifiedSurface,
    SurfaceRecord,
    _all_linked_prs_merged,
    _build_blocker_reason,
    _build_pr_number_index,
    _classify_build_task,
    _classify_deploy_run,
    _classify_main_ci_run,
    _collect_linked_pr_numbers,
    _compute_blocked_record,
    _error_action_signatures,
    _evidence_prs_from_tasks,
    _has_failing_checks,
    _has_multiple_subtasks,
    _is_build_task,
    _is_failure_line,
    _job_flags,
    _match_prs_to_tasks_full,
    _maybe_mark_resolvable,
    _parse_tally_row,
    _pr_obs_dict,
    _suppress_ghost_for_subtasks,
    _tally_error_line,
    _unknown_deploy,
    _unknown_main_ci,
    decide,
    gather_build_tasks,
    gather_ci_surfaces_for,
    gather_recurring_errors,
    is_merged_ghost,
    load_surface_state,
    save_surface_state,
    surface_fingerprint,
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Status
from alan.workspace import create as create_workspace


def _now() -> datetime:
    return datetime.now(UTC)


def _obs(tag: str = "x") -> dict[str, Any]:
    return {"tag": tag, "val": 1}


def _blocked(sid: str = "s1", obs: dict[str, Any] | None = None) -> ClassifiedSurface:
    return ClassifiedSurface(sid, "build_task", True, "ws_stuck", obs or _obs(sid))


def _healthy(sid: str = "s1", obs: dict[str, Any] | None = None) -> ClassifiedSurface:
    return ClassifiedSurface(sid, "build_task", False, "", obs or _obs(sid))


def _record(
    fp: str,
    first_seen: str,
    last_seen: str,
    last_diagnosed_fp: str | None = None,
) -> SurfaceRecord:
    return SurfaceRecord(
        fingerprint=fp,
        first_seen=first_seen,
        last_seen=last_seen,
        last_diagnosed_fp=last_diagnosed_fp,
    )


def _seed_handover(ws: Any, *, from_team: str, pr_number: int | None = None) -> None:
    h = Handover(
        handover_id=f"hid-{from_team}",
        handover_chain_id=f"hid-{from_team}",
        handover_type="forward",
        task_id=ws.task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=_now(),
        full_synthesis="stub",
        pr_number=pr_number,
    )
    save_handover(ws, h)


def test_surface_fingerprint_same_obs() -> None:
    fp1 = surface_fingerprint({"a": 1, "b": 2})
    fp2 = surface_fingerprint({"a": 1, "b": 2})
    assert fp1 == fp2
    assert len(fp1) == 16


def test_surface_fingerprint_different_obs() -> None:
    assert surface_fingerprint({"a": 1}) != surface_fingerprint({"a": 2})


def test_surface_fingerprint_sort_keys() -> None:
    assert surface_fingerprint({"b": 2, "a": 1}) == surface_fingerprint({"a": 1, "b": 2})


def test_load_surface_state_empty(queue: Queue) -> None:
    assert load_surface_state(queue) == {}


def test_load_surface_state_invalid_json(queue: Queue) -> None:
    queue.set_meta(_SURFACE_STATE_META_KEY, "not-json")
    assert load_surface_state(queue) == {}


def test_load_surface_state_wrong_structure(queue: Queue) -> None:
    queue.set_meta(_SURFACE_STATE_META_KEY, json.dumps({"s1": {"wrong": "keys"}}))
    assert load_surface_state(queue) == {}


def test_save_load_round_trip(queue: Queue) -> None:
    now_str = _now().isoformat()
    state = {
        "s1": SurfaceRecord(
            fingerprint="abc", first_seen=now_str, last_seen=now_str, last_diagnosed_fp=None
        ),
        "s2": SurfaceRecord(
            fingerprint="def", first_seen=now_str, last_seen=now_str, last_diagnosed_fp="def"
        ),
    }
    save_surface_state(queue, state)
    loaded = load_surface_state(queue)
    assert loaded["s1"] == state["s1"]
    assert loaded["s2"] == state["s2"]


def test_compute_blocked_record_no_prior() -> None:
    now_str = _now().isoformat()
    rec = _compute_blocked_record("fp1", None, now_str)
    assert rec.fingerprint == "fp1"
    assert rec.first_seen == now_str
    assert rec.last_diagnosed_fp is None


def test_compute_blocked_record_new_fingerprint() -> None:
    now_str = _now().isoformat()
    prior = _record("old_fp", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", "old_fp")
    rec = _compute_blocked_record("new_fp", prior, now_str)
    assert rec.fingerprint == "new_fp"
    assert rec.first_seen == now_str
    assert rec.last_diagnosed_fp == "old_fp"


def test_compute_blocked_record_same_fingerprint_preserves_first_seen() -> None:
    old_first = "2026-01-01T00:00:00+00:00"
    now_str = _now().isoformat()
    prior = _record("fp1", old_first, "2026-01-01T01:00:00+00:00", "fp1")
    rec = _compute_blocked_record("fp1", prior, now_str)
    assert rec.first_seen == old_first
    assert rec.last_seen == now_str
    assert rec.last_diagnosed_fp == "fp1"


def test_decide_healthy_no_prior() -> None:
    now = _now()
    eligible, new_state = decide([_healthy("s1")], {}, now)
    assert eligible == []
    assert "s1" not in new_state


def test_decide_healthy_with_prior_carries_record() -> None:
    now = _now()
    prior_rec = _record("old_fp", now.isoformat(), now.isoformat(), "old_fp")
    eligible, new_state = decide([_healthy("s1")], {"s1": prior_rec}, now)
    assert eligible == []
    assert "s1" in new_state
    assert new_state["s1"].fingerprint == "old_fp"
    assert new_state["s1"].last_diagnosed_fp == "old_fp"


def test_decide_blocked_new_no_prior_is_eligible() -> None:
    now = _now()
    surface = _blocked("s1", {"v": 1})
    eligible, new_state = decide([surface], {}, now)
    assert eligible == [surface]
    assert "s1" in new_state
    assert new_state["s1"].last_diagnosed_fp is None


def test_decide_blocked_same_fp_not_stale_not_suppressed_is_eligible() -> None:
    now = _now()
    obs = {"v": 1}
    surface = _blocked("s1", obs)
    fp = surface_fingerprint(obs)
    first_seen = (now - timedelta(minutes=30)).isoformat()
    prior_rec = _record(fp, first_seen, first_seen)
    eligible, new_state = decide([surface], {"s1": prior_rec}, now)
    assert eligible == [surface]
    assert new_state["s1"].first_seen == first_seen


def test_decide_blocked_same_fp_stale_not_eligible() -> None:
    now = _now()
    obs = {"v": 1}
    surface = _blocked("s1", obs)
    fp = surface_fingerprint(obs)
    first_seen = (now - timedelta(hours=2)).isoformat()
    prior_rec = _record(fp, first_seen, first_seen)
    eligible, new_state = decide([surface], {"s1": prior_rec}, now)
    assert eligible == []
    assert "s1" in new_state


def test_decide_blocked_same_fp_suppressed_not_eligible() -> None:
    now = _now()
    obs = {"v": 1}
    surface = _blocked("s1", obs)
    fp = surface_fingerprint(obs)
    first_seen = (now - timedelta(minutes=10)).isoformat()
    prior_rec = _record(fp, first_seen, first_seen, last_diagnosed_fp=fp)
    eligible, _ = decide([surface], {"s1": prior_rec}, now)
    assert eligible == []


def test_decide_blocked_fp_obs_suppressed_when_last_diagnosed_matches() -> None:
    now = _now()
    obs = {"rate": 0.3, "floor": 0.5, "sample_size": 15, "window_days": 30}
    fp_obs = {"rate": 0.3, "floor": 0.5}
    fp = surface_fingerprint(fp_obs)
    surface = ClassifiedSurface(
        "finisher_regression", "finisher_regression", True, "finisher_rate_below_floor",
        obs=obs, fp_obs=fp_obs,
    )
    first_seen = (now - timedelta(minutes=10)).isoformat()
    prior_rec = _record(fp, first_seen, first_seen, last_diagnosed_fp=fp)
    eligible, _ = decide([surface], {"finisher_regression": prior_rec}, now)
    assert eligible == []


def test_decide_blocked_fp_obs_not_suppressed_when_obs_only_parts_change() -> None:
    now = _now()
    obs1 = {"rate": 0.3, "floor": 0.5, "sample_size": 15, "window_days": 30}
    obs2 = {"rate": 0.3, "floor": 0.5, "sample_size": 20, "window_days": 30}
    fp_obs = {"rate": 0.3, "floor": 0.5}
    fp = surface_fingerprint(fp_obs)
    first_seen = (now - timedelta(minutes=10)).isoformat()
    prior_rec = _record(fp, first_seen, first_seen, last_diagnosed_fp=fp)
    surface2 = ClassifiedSurface(
        "finisher_regression", "finisher_regression", True, "finisher_rate_below_floor",
        obs=obs2, fp_obs=fp_obs,
    )
    eligible, _ = decide([surface2], {"finisher_regression": prior_rec}, now)
    assert eligible == []
    _ = obs1


def test_decide_blocked_different_fp_rearms() -> None:
    now = _now()
    old_obs = {"v": 1}
    new_obs = {"v": 2}
    surface = _blocked("s1", new_obs)
    old_fp = surface_fingerprint(old_obs)
    first_seen = (now - timedelta(hours=2)).isoformat()
    prior_rec = _record(old_fp, first_seen, first_seen, last_diagnosed_fp=old_fp)
    eligible, new_state = decide([surface], {"s1": prior_rec}, now)
    assert eligible == [surface]
    assert new_state["s1"].first_seen == now.isoformat()


def test_decide_blocked_malformed_first_seen_resets_clock() -> None:
    now = _now()
    obs = {"v": 1}
    surface = _blocked("s1", obs)
    fp = surface_fingerprint(obs)
    prior_rec = _record(fp, "not-a-timestamp", "not-a-timestamp")
    eligible, new_state = decide([surface], {"s1": prior_rec}, now)
    assert eligible == [surface]
    assert new_state["s1"].first_seen == now.isoformat()


def test_decide_blocked_naive_first_seen_resets_clock() -> None:
    now = _now()
    obs = {"v": 1}
    surface = _blocked("s1", obs)
    fp = surface_fingerprint(obs)
    naive = datetime.now().isoformat()  # noqa: DTZ005
    prior_rec = _record(fp, naive, naive)
    eligible, new_state = decide([surface], {"s1": prior_rec}, now)
    assert eligible == [surface]
    assert new_state["s1"].first_seen == now.isoformat()


def test_decide_multiple_surfaces_mixed() -> None:
    now = _now()
    s_healthy = _healthy("sh")
    s_blocked_new = _blocked("sb1", {"v": 1})
    s_blocked_suppressed_obs = {"v": 2}
    fp_suppressed = surface_fingerprint(s_blocked_suppressed_obs)
    s_blocked_suppressed = _blocked("sb2", s_blocked_suppressed_obs)
    prior_rec = _record(fp_suppressed, now.isoformat(), now.isoformat(), fp_suppressed)
    eligible, new_state = decide(
        [s_healthy, s_blocked_new, s_blocked_suppressed],
        {
            "sh": _record("old", now.isoformat(), now.isoformat()),
            "sb2": prior_rec,
        },
        now,
    )
    assert eligible == [s_blocked_new]
    assert "sh" in new_state
    assert "sb1" in new_state
    assert "sb2" in new_state


def test_is_build_task_triage_team(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root, team="triage")
    assert _is_build_task(ws) is False


def test_is_build_task_implementation_team(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root, team="implementation")
    assert _is_build_task(ws) is True


def test_is_build_task_implementation_fix_team(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root, team="implementation_fix")
    assert _is_build_task(ws) is True


def test_is_build_task_handover_from_implementation(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    _seed_handover(ws, from_team="implementation")
    assert _is_build_task(ws) is True


def test_is_build_task_handover_from_implementation_fix(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    _seed_handover(ws, from_team="implementation_fix")
    assert _is_build_task(ws) is True


def test_is_build_task_handover_with_pr_number(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    _seed_handover(ws, from_team="verification", pr_number=42)
    assert _is_build_task(ws) is True


def test_is_build_task_no_impl_signal(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    _seed_handover(ws, from_team="plan")
    assert _is_build_task(ws) is False


def test_is_build_task_no_handovers(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    assert _is_build_task(ws) is False


def test_is_build_task_list_handovers_raises(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create_workspace("t", "d", root=ws_root)

    def _boom(w: Any) -> Any:
        raise RuntimeError("disk error")

    monkeypatch.setattr("alan.teams.triage_pregate.list_handovers", _boom)
    assert _is_build_task(ws) is False


def test_has_failing_checks_none() -> None:
    assert _has_failing_checks(None) is False


def test_has_failing_checks_empty_rollup() -> None:
    assert _has_failing_checks({"statusCheckRollup": []}) is False


def test_has_failing_checks_failure() -> None:
    assert _has_failing_checks({"statusCheckRollup": [{"conclusion": "FAILURE"}]}) is True


def test_has_failing_checks_timed_out() -> None:
    assert _has_failing_checks({"statusCheckRollup": [{"conclusion": "TIMED_OUT"}]}) is True


def test_has_failing_checks_action_required() -> None:
    assert _has_failing_checks({"statusCheckRollup": [{"conclusion": "ACTION_REQUIRED"}]}) is True


def test_has_failing_checks_only_success() -> None:
    assert _has_failing_checks({"statusCheckRollup": [{"conclusion": "SUCCESS"}]}) is False


def test_has_failing_checks_none_rollup_value() -> None:
    assert _has_failing_checks({"statusCheckRollup": None}) is False


def test_pr_obs_dict_none() -> None:
    d = _pr_obs_dict(None)
    assert d["number"] is None
    assert d["state"] is None
    assert d["failing_checks"] is False


def test_pr_obs_dict_with_pr_info() -> None:
    pr = {
        "number": 10, "state": "OPEN", "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    d = _pr_obs_dict(pr)
    assert d["number"] == 10
    assert d["state"] == "OPEN"
    assert d["review"] == "APPROVED"
    assert d["merge_state"] == "CLEAN"
    assert d["failing_checks"] is False


def test_job_flags_no_jobs(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    job_failed, job_running = _job_flags(queue, ws.task_id)
    assert job_failed is False
    assert job_running is False


def test_job_flags_running(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    queue.claim()
    job_failed, job_running = _job_flags(queue, ws.task_id)
    assert job_running is True
    assert job_failed is False


def test_job_flags_failed_no_running(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    queue.fail(job.id, "oops")
    job_failed, job_running = _job_flags(queue, ws.task_id)
    assert job_failed is True
    assert job_running is False


def test_job_flags_failed_with_running_not_flagged(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    for _ in range(2):
        inv_id = workflow_mod.seed_pending_workflow(
            ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
        )
        queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job1 = queue.claim()
    assert job1 is not None
    queue.fail(job1.id, "err")
    queue.claim()
    job_failed, job_running = _job_flags(queue, ws.task_id)
    assert job_running is True
    assert job_failed is False


def test_build_blocker_reason_all_false() -> None:
    assert _build_blocker_reason(False, False, False, False) == ""


def test_build_blocker_reason_all_true() -> None:
    r = _build_blocker_reason(True, True, True, True)
    assert "ws_stuck" in r
    assert "job_failed" in r
    assert "pr_checks_failing" in r
    assert "pr_approved_unmerged" in r


def test_build_blocker_reason_ws_only() -> None:
    assert _build_blocker_reason(True, False, False, False) == "ws_stuck"


def test_build_blocker_reason_job_only() -> None:
    assert _build_blocker_reason(False, True, False, False) == "job_failed"


def test_build_blocker_reason_pr_checks_only() -> None:
    assert _build_blocker_reason(False, False, True, False) == "pr_checks_failing"


def test_build_blocker_reason_pr_unmerged_only() -> None:
    assert _build_blocker_reason(False, False, False, True) == "pr_approved_unmerged"


def test_classify_build_task_ws_blocked(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is True
    assert "ws_stuck" in surface.blocker_reason


def test_classify_build_task_ws_escalated(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is True


def test_classify_build_task_job_failed(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    queue.fail(job.id, "err")
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is True
    assert "job_failed" in surface.blocker_reason


def test_classify_build_task_pr_checks_failing(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    pr_info = {
        "number": 1, "state": "OPEN", "reviewDecision": None,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True
    assert "pr_checks_failing" in surface.blocker_reason


def test_classify_build_task_pr_approved_unmerged(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    pr_info = {
        "number": 2, "state": "OPEN", "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True
    assert "pr_approved_unmerged" in surface.blocker_reason


def test_classify_build_task_pr_approved_unmerged_suppressed_by_running(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    queue.claim()
    pr_info = {
        "number": 3, "state": "OPEN", "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is False


def test_classify_build_task_healthy(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is False
    assert surface.kind == "build_task"
    assert surface.surface_id == ws.task_id


def test_classify_build_task_merged_pr_not_blocked(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    pr_info = {
        "number": 10, "state": "MERGED", "reviewDecision": "APPROVED",
        "mergeStateStatus": "MERGED", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True


def test_classify_build_task_closed_pr_not_blocked(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.FAILED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    pr_info = {
        "number": 11, "state": "CLOSED", "reviewDecision": None,
        "mergeStateStatus": "CLOSED", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True


def test_classify_build_task_merged_pr_job_failed_not_blocked(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    queue.fail(job.id, "err")
    pr_info = {
        "number": 12, "state": "MERGED", "reviewDecision": "APPROVED",
        "mergeStateStatus": "MERGED", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True


def test_classify_build_task_closed_pr_checks_failing_not_blocked(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    pr_info = {
        "number": 13, "state": "CLOSED", "reviewDecision": None,
        "mergeStateStatus": "CLOSED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True


def test_classify_build_task_none_pr_state_not_terminal(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is True
    assert surface.obs["pr_state"] is None


def test_classify_build_task_open_pr_ws_stuck_blocked(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.FAILED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    pr_info = {
        "number": 14, "state": "OPEN", "reviewDecision": None,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True


def test_classify_build_task_merged_pr_records_stale_reason(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    pr_info = {
        "number": 15, "state": "MERGED", "reviewDecision": "APPROVED",
        "mergeStateStatus": "MERGED", "statusCheckRollup": [],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is True
    assert surface.obs["pr_state"] == "MERGED"
    assert "ws_stuck" in surface.blocker_reason


def test_match_prs_to_tasks_full_prefix_collision() -> None:
    prs = [
        {
            "number": 100, "state": "OPEN", "headRefName": "teams/foo2-inv001",
            "reviewDecision": None, "mergeStateStatus": None, "statusCheckRollup": [],
        },
        {
            "number": 101, "state": "OPEN", "headRefName": "teams/foo-inv001",
            "reviewDecision": None, "mergeStateStatus": None, "statusCheckRollup": [],
        },
    ]
    result = _match_prs_to_tasks_full(prs, ["foo", "foo2"])
    assert result["foo"]["number"] == 101
    assert result["foo2"]["number"] == 100


def test_match_prs_to_tasks_full_no_matches() -> None:
    prs = [{"number": 1, "state": "OPEN", "headRefName": "teams/other", "statusCheckRollup": []}]
    assert _match_prs_to_tasks_full(prs, ["my-task"]) == {}


def test_classify_main_ci_run_failure() -> None:
    run = {"databaseId": 1, "status": "completed", "conclusion": "failure", "headSha": "abc"}
    s = _classify_main_ci_run(run, "main_ci:o/r", "o/r")
    assert s.is_blocked is True
    assert s.blocker_reason == "ci_failure"
    assert s.obs["run_id"] == 1
    assert s.obs["repo"] == "o/r"
    assert s.surface_id == "main_ci:o/r"
    assert s.kind == "main_ci"


def test_classify_main_ci_run_success() -> None:
    run = {"databaseId": 2, "status": "completed", "conclusion": "success", "headSha": "def"}
    s = _classify_main_ci_run(run, "main_ci:o/r", "o/r")
    assert s.is_blocked is False
    assert s.blocker_reason == ""


def test_classify_main_ci_run_timed_out() -> None:
    run = {"databaseId": 3, "status": "completed", "conclusion": "timed_out", "headSha": "g"}
    s = _classify_main_ci_run(run, "main_ci:o/r", "o/r")
    assert s.is_blocked is True
    assert s.blocker_reason == "ci_failure"


def test_classify_main_ci_run_action_required() -> None:
    run = {"databaseId": 4, "status": "completed", "conclusion": "action_required", "headSha": "h"}
    s = _classify_main_ci_run(run, "main_ci:o/r", "o/r")
    assert s.is_blocked is True
    assert s.blocker_reason == "ci_failure"


def test_classify_main_ci_run_missing_conclusion() -> None:
    run = {"databaseId": 5, "status": "in_progress", "headSha": "i"}
    s = _classify_main_ci_run(run, "main_ci:o/r", "o/r")
    assert s.is_blocked is False
    assert s.blocker_reason == ""


def test_unknown_main_ci() -> None:
    s = _unknown_main_ci("main_ci:o/r")
    assert s.surface_id == "main_ci:o/r"
    assert s.kind == "main_ci"
    assert s.is_blocked is False
    assert s.obs == {}


def test_classify_deploy_run_failure() -> None:
    run = {"databaseId": 1, "status": "completed", "conclusion": "failure", "headSha": "abc"}
    s = _classify_deploy_run(run, "deploy:o/r", "o/r")
    assert s.kind == "deploy"
    assert s.is_blocked is True
    assert s.blocker_reason == "deploy_failure"
    assert s.obs["run_id"] == 1
    assert s.obs["repo"] == "o/r"
    assert s.surface_id == "deploy:o/r"


def test_classify_deploy_run_skipped_not_blocked() -> None:
    run = {"databaseId": 2, "status": "completed", "conclusion": "skipped", "headSha": "d"}
    s = _classify_deploy_run(run, "deploy:o/r", "o/r")
    assert s.is_blocked is False
    assert s.blocker_reason == ""


def test_classify_deploy_run_success() -> None:
    run = {"databaseId": 3, "status": "completed", "conclusion": "success", "headSha": "e"}
    s = _classify_deploy_run(run, "deploy:o/r", "o/r")
    assert s.is_blocked is False


def test_unknown_deploy() -> None:
    s = _unknown_deploy("deploy:o/r")
    assert s.surface_id == "deploy:o/r"
    assert s.kind == "deploy"
    assert s.is_blocked is False
    assert s.obs == {}


def test_gather_ci_surfaces_for_no_ci_returns_empty() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    assert gather_ci_surfaces_for(cfg) == []


def test_is_failure_line_matches() -> None:
    assert _is_failure_line("verify_failed")
    assert _is_failure_line("push_error")
    assert _is_failure_line("http_401")
    assert _is_failure_line("subprocess_timeout")
    assert _is_failure_line("status_5xx_seen")


def test_is_failure_line_no_match() -> None:
    assert not _is_failure_line("review_ok")
    assert not _is_failure_line("push_ok")


def test_parse_tally_row_empty() -> None:
    assert _parse_tally_row("") is None
    assert _parse_tally_row("   ") is None


def test_parse_tally_row_invalid_json() -> None:
    assert _parse_tally_row("not-json") is None


def test_parse_tally_row_non_dict_json() -> None:
    assert _parse_tally_row("[1, 2, 3]") is None


def test_parse_tally_row_no_timestamp_field() -> None:
    assert _parse_tally_row(json.dumps({"action": "verify_failed"})) is None


def test_parse_tally_row_non_string_timestamp() -> None:
    row = json.dumps({"action": "verify_failed", "timestamp_utc": 123})
    assert _parse_tally_row(row) is None


def test_parse_tally_row_invalid_timestamp_format() -> None:
    row = json.dumps({"action": "verify_failed", "timestamp_utc": "not-a-date"})
    assert _parse_tally_row(row) is None


def test_parse_tally_row_no_timezone() -> None:
    row = json.dumps({"action": "verify_failed", "timestamp_utc": "2026-01-01T00:00:00"})
    assert _parse_tally_row(row) is None


def test_parse_tally_row_valid() -> None:
    ts = _now().isoformat()
    result = _parse_tally_row(json.dumps({"action": "verify_failed", "timestamp_utc": ts}))
    assert result is not None
    action, dt = result
    assert action == "verify_failed"
    assert dt.tzinfo is not None


def test_tally_error_line_stale_not_counted() -> None:
    cutoff = datetime.now(UTC)
    sigs: dict[str, int] = {}
    stale_ts = (cutoff - timedelta(hours=1)).isoformat()
    _tally_error_line(
        json.dumps({"action": "verify_failed", "timestamp_utc": stale_ts}), cutoff, sigs
    )
    assert sigs == {}


def test_tally_error_line_counted() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    sigs: dict[str, int] = {}
    fresh_ts = _now().isoformat()
    _tally_error_line(
        json.dumps({"action": "verify_failed", "timestamp_utc": fresh_ts}), cutoff, sigs
    )
    assert sigs == {"verify_failed": 1}


def test_tally_error_line_non_failure_keyword_not_counted() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    sigs: dict[str, int] = {}
    fresh_ts = _now().isoformat()
    _tally_error_line(
        json.dumps({"action": "review_ok", "timestamp_utc": fresh_ts}), cutoff, sigs
    )
    assert sigs == {}


def test_tally_error_line_invalid_line_returns_early() -> None:
    sigs: dict[str, int] = {}
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _tally_error_line("not-json", cutoff, sigs)
    assert sigs == {}


def test_error_action_signatures_missing_dir(tmp_path: Path) -> None:
    sigs = _error_action_signatures(tmp_path / "no-such-dir", 2)
    assert sigs == {}


def test_error_action_signatures_excludes_stale(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    stale_ts = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    row = json.dumps({"action": "verify_failed", "timestamp_utc": stale_ts})
    (logs / "verification_audit.jsonl").write_text(row + "\n", encoding="utf-8")
    sigs = _error_action_signatures(logs, 2)
    assert sigs == {}


def test_error_action_signatures_counts_fresh(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    fresh_ts = _now().isoformat()
    row = json.dumps({"action": "verify_failed", "timestamp_utc": fresh_ts})
    (logs / "verification_audit.jsonl").write_text(row + "\n", encoding="utf-8")
    sigs = _error_action_signatures(logs, 2)
    assert sigs["verify_failed"] == 1


def test_error_action_signatures_file_read_exception(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "verification_audit.jsonl").mkdir()
    sigs = _error_action_signatures(logs, 2)
    assert sigs == {}


def test_gather_recurring_errors_empty_dir(tmp_path: Path) -> None:
    s = gather_recurring_errors(tmp_path / "no-logs", 2)
    assert s.is_blocked is False
    assert s.surface_id == "recurring_errors"
    assert s.kind == "recurring_errors"


def test_gather_recurring_errors_one_occurrence(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    fresh_ts = _now().isoformat()
    row = json.dumps({"action": "verify_failed", "timestamp_utc": fresh_ts})
    (logs / "verification_audit.jsonl").write_text(row + "\n", encoding="utf-8")
    s = gather_recurring_errors(logs, 2)
    assert s.is_blocked is False


def test_gather_recurring_errors_two_occurrences(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    fresh_ts = _now().isoformat()
    row = json.dumps({"action": "verify_failed", "timestamp_utc": fresh_ts})
    for fname in ("verification_audit.jsonl", "impl_audit.jsonl"):
        (logs / fname).write_text(row + "\n", encoding="utf-8")
    s = gather_recurring_errors(logs, 2)
    assert s.is_blocked is True
    assert "verify_failed" in s.obs["recurring_signatures"]
    assert "recurring_errors" in s.blocker_reason


def test_gather_build_tasks_list_workspaces_raises(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*, root: Any = None) -> Any:
        raise RuntimeError("disk error")

    monkeypatch.setattr("alan.teams.triage_pregate.list_workspaces", _boom)
    assert gather_build_tasks(ws_root, queue) == []


def test_gather_build_tasks_no_build_tasks(queue: Queue, ws_root: Path) -> None:
    create_workspace("plain", "d", root=ws_root)
    assert gather_build_tasks(ws_root, queue) == []


def test_gather_build_tasks_impl_team_ws_blocked(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [])
    ws = create_workspace("impl-ws", "d", root=ws_root, team="implementation")
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is True
    assert "ws_stuck" in result[0].blocker_reason


def test_gather_build_tasks_archived_ws_excluded(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [])
    ws = create_workspace("archived-impl", "d", root=ws_root, team="implementation")
    workspace_mod.transition(ws, Status.ARCHIVED)
    assert gather_build_tasks(ws_root, queue) == []


def test_gather_build_tasks_archived_with_stale_failed_job_excluded(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [])
    ws = create_workspace("archived-failed", "d", root=ws_root, team="implementation")
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    queue.fail(job.id, "stale failure")
    workspace_mod.transition(ws, Status.ARCHIVED)
    assert gather_build_tasks(ws_root, queue) == []


def test_gather_build_tasks_completed_with_stale_failed_job_excluded(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [])
    ws = create_workspace("completed-failed", "d", root=ws_root, team="implementation")
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    queue.fail(job.id, "stale failure")
    workspace_mod.transition(ws, Status.COMPLETED)
    assert gather_build_tasks(ws_root, queue) == []


def test_gather_build_tasks_impl_team_ws_healthy(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [])
    create_workspace("impl-ws2", "d", root=ws_root, team="implementation")
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is False


def test_gather_build_tasks_pr_approved_unmerged_blocked(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr_info = {
        "number": 5, "state": "OPEN", "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    ws = create_workspace("impl-pr", "d", root=ws_root, team="implementation")
    pr_with_ref = {**pr_info, "headRefName": f"teams/{ws.task_id}"}
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_open_pr_rollups", lambda: [])
    monkeypatch.setattr(
        "alan.teams.triage_pregate._fetch_raw_prs", lambda: [pr_with_ref]
    )
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is True
    assert "pr_approved_unmerged" in result[0].blocker_reason


def test_gather_build_tasks_pr_approved_unmerged_not_blocked_when_running(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr_info = {
        "number": 6, "state": "OPEN", "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    ws = create_workspace("impl-pr2", "d", root=ws_root, team="implementation")
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    queue.claim()
    pr_with_ref = {**pr_info, "headRefName": f"teams/{ws.task_id}"}
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_open_pr_rollups", lambda: [])
    monkeypatch.setattr(
        "alan.teams.triage_pregate._fetch_raw_prs", lambda: [pr_with_ref]
    )
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is False


def test_classify_build_task_partial_no_running_subtask_blocked(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t-partial-idle", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PARTIAL)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is True
    assert "ws_stuck" in surface.blocker_reason


def test_classify_build_task_partial_with_running_subtask_not_blocked(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t-partial-active", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PARTIAL)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue("subtask-id-1", "implementation", workspace_id=ws.task_id,
                  triggered_by_invocation_id=inv_id)
    queue.claim()
    surface = _classify_build_task(ws, queue, None)
    assert surface.is_blocked is False
    assert surface.obs["job_running"] is True


def test_classify_build_task_partial_with_running_subtask_open_pr_not_blocked(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t-partial-open-pr", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PARTIAL)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue("subtask-id-2", "implementation", workspace_id=ws.task_id,
                  triggered_by_invocation_id=inv_id)
    queue.claim()
    pr_info = {
        "number": 99, "state": "OPEN", "reviewDecision": None,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.is_blocked is False


def test_ghost_ws_statuses_contains_escalated_and_failed() -> None:
    assert Status.ESCALATED in _GHOST_WS_STATUSES
    assert Status.FAILED in _GHOST_WS_STATUSES
    assert Status.AWAITING_INPUT not in _GHOST_WS_STATUSES


def test_build_pr_number_index_empty() -> None:
    assert _build_pr_number_index([]) == {}


def test_build_pr_number_index_maps_number_to_pr() -> None:
    pr: dict[str, Any] = {"number": 42, "state": "MERGED"}
    result = _build_pr_number_index([pr])
    assert result == {42: pr}


def test_build_pr_number_index_skips_missing_number() -> None:
    result = _build_pr_number_index([{"state": "OPEN"}])
    assert result == {}


def test_evidence_prs_from_tasks_empty() -> None:
    assert _evidence_prs_from_tasks([]) == set()


def test_evidence_prs_from_tasks_no_matches() -> None:
    from alan.workspace import Task, TaskDoDClause, TaskStatus

    task = Task(
        id="t1", status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="c", evidence="no pr here")],
    )
    assert _evidence_prs_from_tasks([task]) == set()


def test_evidence_prs_from_tasks_single_pr() -> None:
    from alan.workspace import Task, TaskDoDClause, TaskStatus

    task = Task(
        id="t1", status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="c", evidence="PR #42")],
    )
    assert _evidence_prs_from_tasks([task]) == {42}


def test_evidence_prs_from_tasks_two_refs_in_one_clause() -> None:
    from alan.workspace import Task, TaskDoDClause, TaskStatus

    task = Task(
        id="t1", status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="c", evidence="PR #1, PR #2 done")],
    )
    assert _evidence_prs_from_tasks([task]) == {1, 2}


def test_collect_linked_pr_numbers_from_handover(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=_now(),
        full_synthesis="stub", pr_number=42,
    )
    linked = _collect_linked_pr_numbers(ws, [h])
    assert 42 in linked


def test_collect_linked_pr_numbers_handover_no_pr(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=_now(),
        full_synthesis="stub", pr_number=None,
    )
    linked = _collect_linked_pr_numbers(ws, [h])
    assert linked == set()


def test_collect_linked_pr_numbers_from_dod_evidence(ws_root: Path) -> None:
    from alan.workspace import Task, TaskDoDClause, TaskStatus
    ws = create_workspace("t", "d", root=ws_root)
    task = Task(
        id="t1",
        status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="Finish feature", evidence="PR #77")],
    )
    ws = workspace_mod.inject_tasks(ws, [task], [])
    linked = _collect_linked_pr_numbers(ws, [])
    assert 77 in linked


def test_collect_linked_pr_numbers_no_dod_clauses_skipped(ws_root: Path) -> None:
    from alan.workspace import Task, TaskStatus
    ws = create_workspace("t", "d", root=ws_root)
    task = Task(id="t1", status=TaskStatus.PENDING, dod_clauses=[])
    ws = workspace_mod.inject_tasks(ws, [task], [])
    linked = _collect_linked_pr_numbers(ws, [])
    assert linked == set()


def test_all_linked_prs_merged_empty_set() -> None:
    assert _all_linked_prs_merged(set(), {}) is True


def test_all_linked_prs_merged_in_index_merged() -> None:
    index = {42: {"number": 42, "state": "MERGED"}}
    assert _all_linked_prs_merged({42}, index) is True


def test_all_linked_prs_merged_in_index_not_merged() -> None:
    index = {42: {"number": 42, "state": "OPEN"}}
    assert _all_linked_prs_merged({42}, index) is False


def test_is_merged_ghost_wrong_status(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    assert is_merged_ghost(ws, queue, {}) is False


def test_is_merged_ghost_job_running(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    queue.claim()
    assert is_merged_ghost(ws, queue, {}) is False


def test_is_merged_ghost_list_handovers_raises(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.FAILED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)

    def _raises(_ws: Any) -> Any:
        raise RuntimeError("bad")

    monkeypatch.setattr("alan.teams.triage_pregate.list_handovers", _raises)
    assert is_merged_ghost(ws, queue, {}) is False


def test_is_merged_ghost_no_linked_prs(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    assert is_merged_ghost(ws, queue, {}) is False


def test_is_merged_ghost_true(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=42)
    index = {42: {"number": 42, "state": "MERGED"}}
    assert is_merged_ghost(ws, queue, index) is True


def test_is_merged_ghost_false_pr_not_merged(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=42)
    index = {42: {"number": 42, "state": "OPEN"}}
    assert is_merged_ghost(ws, queue, index) is False


def test_maybe_mark_resolvable_not_a_ghost(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    surface = _blocked(ws.task_id)
    result = _maybe_mark_resolvable(surface, ws, queue, {})
    assert result is surface


def test_maybe_mark_resolvable_is_ghost(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=99)
    index = {99: {"number": 99, "state": "MERGED"}}
    surface = ClassifiedSurface(ws.task_id, "build_task", True, "ws_stuck", {})
    result = _maybe_mark_resolvable(surface, ws, queue, index)
    assert result.is_blocked is False
    assert result.resolvable is True
    assert result.surface_id == ws.task_id


def _multi_task_workspace(ws_root: Path, task_id: str = "impl-ghost-multi") -> Any:
    return create_workspace(
        task_id, "d", root=ws_root, team="implementation",
        tasks=[
            workspace_mod.Task(id="sub-a", status=workspace_mod.TaskStatus.READY),
            workspace_mod.Task(id="sub-b", status=workspace_mod.TaskStatus.READY),
        ],
    )


def test_has_multiple_subtasks_false_for_no_decomposition(
    queue: Queue, ws_root: Path  # noqa: ARG001
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    assert _has_multiple_subtasks(ws) is False


def test_has_multiple_subtasks_true_for_two_tasks(ws_root: Path) -> None:
    ws = _multi_task_workspace(ws_root)
    assert _has_multiple_subtasks(ws) is True


def test_suppress_ghost_for_subtasks_sets_resolvable_false_keeps_blocked() -> None:
    surface = ClassifiedSurface(
        "s1", "build_task", True, "ws_stuck", {"pr_number": 99},
    )
    result = _suppress_ghost_for_subtasks(surface)
    assert result.resolvable is False
    assert result.is_blocked is True
    assert result.blocker_reason == "ws_stuck"
    assert result.obs["has_subtasks"] is True
    assert result.obs["pr_number"] == 99


def test_suppress_ghost_for_subtasks_pins_fp_obs_to_pre_augmentation_obs() -> None:
    original_obs = {"pr_number": 99}
    surface = ClassifiedSurface("s1", "build_task", True, "ws_stuck", original_obs)
    result = _suppress_ghost_for_subtasks(surface)
    assert result.fp_obs is not None
    assert result.fp_obs == original_obs
    assert "has_subtasks" not in result.fp_obs
    assert surface_fingerprint(result.fp_obs) == surface_fingerprint(original_obs)
    assert surface_fingerprint(result.obs) != surface_fingerprint(original_obs)


def test_suppress_ghost_for_subtasks_uses_existing_fp_obs_if_set() -> None:
    pinned_obs = {"pr_number": 99}
    surface = ClassifiedSurface(
        "s1", "build_task", True, "ws_stuck", {"pr_number": 99, "extra": "x"},
        fp_obs=pinned_obs,
    )
    result = _suppress_ghost_for_subtasks(surface)
    assert result.fp_obs == pinned_obs


def test_maybe_mark_resolvable_suppressed_for_multi_subtask_ghost(
    queue: Queue, ws_root: Path
) -> None:
    ws = _multi_task_workspace(ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=99)
    index = {99: {"number": 99, "state": "MERGED"}}
    surface = ClassifiedSurface(
        ws.task_id, "build_task", True, "ws_stuck", {"pr_number": 99},
    )
    result = _maybe_mark_resolvable(surface, ws, queue, index)
    assert result.resolvable is False
    assert result.is_blocked is True
    assert result.obs["has_subtasks"] is True
    assert result.obs["pr_number"] == 99


def test_maybe_mark_resolvable_single_subtask_ghost_still_resolvable(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace(
        "impl-ghost-single", "d", root=ws_root, team="implementation",
        tasks=[workspace_mod.Task(id="sub-a", status=workspace_mod.TaskStatus.READY)],
    )
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=99)
    index = {99: {"number": 99, "state": "MERGED"}}
    surface = ClassifiedSurface(ws.task_id, "build_task", True, "ws_stuck", {})
    result = _maybe_mark_resolvable(surface, ws, queue, index)
    assert result.is_blocked is False
    assert result.resolvable is True


def test_gather_build_tasks_ghost_marked_resolvable(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create_workspace("impl-ghost", "d", root=ws_root, team="implementation")
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=7)
    pr = {
        "number": 7, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [pr])
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is False
    assert result[0].resolvable is True


def test_gather_build_tasks_ghost_with_multiple_subtasks_stays_blocked(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_task_workspace(ws_root, "impl-ghost-multi2")
    workspace_mod.transition(ws, Status.ESCALATED)
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    _seed_handover(ws, from_team="implementation", pr_number=7)
    pr = {
        "number": 7, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr("alan.teams.triage_pregate._fetch_raw_prs", lambda: [pr])
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is True
    assert result[0].resolvable is False
    assert result[0].obs.get("has_subtasks") is True

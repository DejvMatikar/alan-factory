from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import handover as handover_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.handover import Handover, save_handover
from alan.health import SWEEP_CADENCE_SECONDS as _CADENCE_SECONDS
from alan.queue import JobStatus, Queue
from alan.teams._base import ResumeUnsupportedError
from alan.teams.triage import (
    _CADENCE_META_KEY,
    _MERGE_GATE_AUDIT_FILE,
    _MERGE_GATE_RECOVERY_META_KEY,
    _TEAM_NAME,
    ROLE_SPECS,
    TriageTeam,
)
from alan.teams.triage_pregate import ClassifiedSurface
from alan.workspace import Workspace
from alan.workspace import create as create_workspace
from tests.conftest import spawns_real_claude


def _noop_emit(event: Any) -> None:
    del event


def _seed_triage_ws(ws_root: Path) -> tuple[Workspace, str]:
    ws = create_workspace(
        "Failure Triage Sweep", "d", root=ws_root, team=_TEAM_NAME
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team=_TEAM_NAME,
        tasks=list(ROLE_SPECS),
    )
    return ws, inv_id


def _seed_audit_files(logs_dir: Path) -> None:
    now_str = datetime.now(UTC).isoformat()
    stale_str = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    in_row = json.dumps(
        {"action": "verify_failed", "timestamp_utc": now_str, "detail": "HTTP 401"},
    )
    out_row = json.dumps(
        {"action": "verify_failed", "timestamp_utc": stale_str, "detail": "stale error"},
    )
    for filename in ("verification_audit.jsonl", "impl_audit.jsonl"):
        (logs_dir / filename).write_text(f"{out_row}\n{in_row}\n", encoding="utf-8")


def _setup_logs_and_team(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
) -> tuple[TriageTeam, Workspace, str, Path]:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)
    return TriageTeam(queue=queue, workspaces_root=ws_root), ws, inv_id, logs_dir


def test_triage_team_name_and_supports_resume(queue: Queue, ws_root: Path) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    assert team.name == "triage"
    assert team.supports_resume is False


def test_triage_plan_workflow_returns_single_sensor_spec(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("test", "d", root=ws_root)
    specs = team.plan_workflow(ws, None)
    assert specs == list(ROLE_SPECS)
    assert len(specs) == 1
    assert specs[0].model == "none"
    assert specs[0].depends_on == []


def test_triage_reject_resume_inv_id(queue: Queue, ws_root: Path) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws, inv_id = _seed_triage_ws(ws_root)
    with pytest.raises(ResumeUnsupportedError):
        team.run(ws, None, _noop_emit, inv_id=inv_id, resume_inv_id="some-resume-id")


def test_triage_run_makes_zero_llm_calls(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as subprocess_mod
    real_popen = subprocess_mod.Popen

    def _guarded_popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if spawns_real_claude(cmd):
            raise AssertionError(f"unexpected claude CLI subprocess spawn: {cmd!r}")
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess_mod, "Popen", _guarded_popen)

    team, ws, inv_id, _logs_dir = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == "triage"
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.status == "success"
    assert [t.id for t in doc.tasks] == [ROLE_SPECS[0].id]
    assert doc.tasks[0].model == "none"


def test_triage_run_calls_reconcile_incidents_with_health_state(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)
    calls: list[tuple[Any, Any, Any, Any]] = []

    def _reconcile(q: Queue, root: Path | None, health_state: Any, now: Any) -> None:
        calls.append((q, root, health_state, now))

    team = TriageTeam(queue=queue, workspaces_root=ws_root, reconcile_incidents=_reconcile)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert len(calls) == 1
    assert calls[0][0] is queue
    assert calls[0][1] == ws_root
    assert calls[0][2] is not None


def test_triage_run_swallows_reconcile_incidents_errors(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)

    def _reconcile(q: Queue, root: Path | None, health_state: Any, now: Any) -> None:
        del q, root, health_state, now
        raise RuntimeError("boom")

    team = TriageTeam(queue=queue, workspaces_root=ws_root, reconcile_incidents=_reconcile)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == "triage"


def test_triage_run_writes_sweep_complete_audit(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, ws, inv_id, logs_dir = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == "triage"
    assert handover.task_id == ws.task_id
    audit_file = logs_dir / "triage_audit.jsonl"
    assert audit_file.exists()
    entry = json.loads(audit_file.read_text(encoding="utf-8").splitlines()[0])
    assert entry["action"] == "sweep_complete"
    assert entry["task_id"] == ws.task_id
    assert entry["inv_id"] == inv_id
    assert "surface_count" in entry
    assert "blocked_count" in entry


def test_triage_run_schedules_next_sweep_enqueues_job(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    before = time.time()
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "triage"
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + _CADENCE_SECONDS
    new_inv_ids = workflow_mod.list_invocations(ws)
    new_inv_id = next(i for i in new_inv_ids if i != inv_id)
    new_doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, new_inv_id))
    assert new_doc.source_task_id == ws.task_id


def test_triage_run_schedules_next_sweep_even_if_sensor_raises(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    def _raise(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("sensor failure")

    monkeypatch.setattr("alan.teams.triage.gather_all", _raise)
    with pytest.raises(RuntimeError, match="sensor failure"):
        team.run(ws, None, _noop_emit, inv_id=inv_id)
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "triage"


def test_triage_reschedule_ignores_cadence_meta_key(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("Failure Triage Sweep", "d", root=ws_root)
    queue.set_meta(_CADENCE_META_KEY, str(time.time()))
    before = time.time()
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + _CADENCE_SECONDS


def test_triage_skip_reschedule_when_pending_exists(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws, inv_id = _seed_triage_ws(ws_root)
    queue.enqueue(ws.task_id, _TEAM_NAME, triggered_by_invocation_id=inv_id)
    before_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert before_count == 1
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    after_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert after_count == before_count


def test_triage_skip_reschedule_when_deferred_pending_exists(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws, inv_id = _seed_triage_ws(ws_root)
    queue.enqueue(
        ws.task_id,
        _TEAM_NAME,
        triggered_by_invocation_id=inv_id,
        eligible_at=time.time() + _CADENCE_SECONDS,
    )
    before_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert before_count == 1
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    after_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert after_count == before_count


def test_triage_skip_reschedule_workspace_gone(
    queue: Queue, ws_root: Path
) -> None:
    import shutil

    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("Failure Triage Sweep", "d", root=ws_root)
    shutil.rmtree(ws.path)
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert jobs == []


def test_triage_reschedule_updates_meta_key(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("Failure Triage Sweep", "d", root=ws_root)
    before = time.time()
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    after = time.time()
    val = queue.get_meta(_CADENCE_META_KEY)
    assert val is not None
    assert before <= float(val) <= after


def test_triage_schedule_swallows_exception(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("t", "d", root=ws_root)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("queue died")

    monkeypatch.setattr(queue, "enqueue", _boom)
    team._schedule_next_sweep(ws)  # noqa: SLF001 — must not raise
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "triage_audit.jsonl").read_text().splitlines()
    ]
    failure_rows = [r for r in rows if r["action"] == "seed_triage_failed"]
    assert len(failure_rows) == 1
    assert failure_rows[0]["source"] == "reschedule"
    assert "queue died" in failure_rows[0]["error"]


def test_triage_reschedule_prunes_exhausted_slots_and_audits(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("t", "d", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_MAX_INV_SLOT", 3)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(3):
        workflow_mod.create_invocation_dir(ws)
    team._schedule_next_sweep(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    inv_dirs = sorted(p.name for p in (ws.path / "invocations").iterdir())
    assert inv_dirs == ["inv_001", "inv_003"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "triage_audit.jsonl").read_text().splitlines()
    ]
    pruned_rows = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["task_id"] == ws.task_id
    assert pruned_rows[0]["removed"] == 2


def _sweep_handover(ws: Workspace, handover_suffix: str) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_{handover_suffix}",
        handover_type="forward",
        task_id=ws.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="[SENSOR_SWEEP] surfaces=1 blocked=0",
    )


def test_triage_reschedule_prunes_exhausted_handovers_and_audits(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("t", "d", root=ws_root)
    monkeypatch.setattr(handover_mod, "_MAX_SEQ", 2)
    save_handover(ws, _sweep_handover(ws, "a"))
    save_handover(ws, _sweep_handover(ws, "b"))
    team._schedule_next_sweep(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert list((ws.path / "handovers").iterdir()) == []
    saved = save_handover(ws, _sweep_handover(ws, "c"))
    assert saved.name.endswith("_001.yaml")
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "triage_audit.jsonl").read_text().splitlines()
    ]
    pruned_rows = [r for r in rows if r["action"] == "handover_slots_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["task_id"] == ws.task_id
    assert pruned_rows[0]["removed"] == 2


def test_triage_reschedule_prunes_triage_revisit_counts_and_audits(
    queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("t", "d", root=ws_root)
    ws = workspace_mod.increment_revisit_count(ws, _TEAM_NAME, "chain-1")
    ws = workspace_mod.increment_revisit_count(ws, "research", "chain-1")
    meta_path = ws.path / "meta.json"
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    raw["revisit_counts"]["triage|legacy"] = 3
    meta_path.write_text(json.dumps(raw), encoding="utf-8")
    team._schedule_next_sweep(ws)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.revisit_counts == {
        f"{ws.task_id}|{ws.task_id}|research|chain-1": 1
    }
    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "triage_audit.jsonl").read_text().splitlines()
    ]
    pruned_rows = [r for r in rows if r["action"] == "revisit_keys_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["task_id"] == ws.task_id
    assert pruned_rows[0]["removed"] == 2


def test_triage_handover_iteration_increments(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    h1 = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert h1.iteration == 1

    inv_id2 = workflow_mod.seed_pending_workflow(
        ws,
        team=_TEAM_NAME,
        tasks=list(ROLE_SPECS),
    )
    h2 = team.run(ws, h1, _noop_emit, inv_id=inv_id2)
    assert h2.iteration == 2
    assert h2.revisit_of == h1.handover_id


def test_triage_cadence_invalid_skips_when_pending_exists(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws, inv_id = _seed_triage_ws(ws_root)
    queue.enqueue(ws.task_id, _TEAM_NAME, triggered_by_invocation_id=inv_id)
    before_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert before_count == 1
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    after_count = len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING))
    assert after_count == before_count


def test_triage_reschedule_seeds_sensor_workflow_shape(
    queue: Queue, ws_root: Path
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("Failure Triage Sweep", "d", root=ws_root)
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    inv_ids = workflow_mod.list_invocations(ws)
    assert len(inv_ids) == 1
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_ids[0]))
    assert [t.id for t in doc.tasks] == [ROLE_SPECS[0].id]


def test_triage_cadence_is_1800_seconds() -> None:
    assert _CADENCE_SECONDS == 1800.0


def test_triage_mark_diagnosed_sid_not_in_state_is_noop() -> None:
    from alan.teams.triage import _mark_diagnosed
    from alan.teams.triage_pregate import SurfaceRecord

    now_str = datetime.now(UTC).isoformat()
    new_state = {"other": SurfaceRecord("fp", now_str, now_str, None)}
    _mark_diagnosed(new_state, {"missing_sid": "fp"})
    assert "missing_sid" not in new_state
    assert "other" in new_state


def test_save_surface_state_safe_swallows_exception(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.triage import _save_surface_state_safe

    def _boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("save failed")

    monkeypatch.setattr("alan.teams.triage.save_surface_state", _boom)
    _save_surface_state_safe(queue, {})


def test_triage_run_persists_surface_state(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams.triage_pregate import SurfaceRecord, load_surface_state

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    now_str = datetime.now(UTC).isoformat()
    stored_record = SurfaceRecord(
        fingerprint="existing_fp",
        first_seen=now_str,
        last_seen=now_str,
        last_diagnosed_fp=None,
    )

    def _no_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, stored, now
        return [], {"some_surface": stored_record}

    monkeypatch.setattr("alan.teams.triage.decide", _no_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    loaded = load_surface_state(queue)
    assert "some_surface" in loaded
    assert loaded["some_surface"].fingerprint == "existing_fp"


def test_triage_run_marks_diagnosed_when_eligible(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams.triage_pregate import (
        ClassifiedSurface,
        SurfaceRecord,
        load_surface_state,
        surface_fingerprint,
    )

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    obs = {"v": 1}
    fp = surface_fingerprint(obs)
    now_str = datetime.now(UTC).isoformat()
    eligible_surface = ClassifiedSurface("s1", "build_task", True, "ws_stuck", obs)
    record = SurfaceRecord(
        fingerprint=fp, first_seen=now_str, last_seen=now_str, last_diagnosed_fp=None
    )

    def _one_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, stored, now
        return [eligible_surface], {"s1": record}

    monkeypatch.setattr("alan.teams.triage.decide", _one_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    loaded = load_surface_state(queue)
    assert "s1" in loaded
    assert loaded["s1"].last_diagnosed_fp == fp


def test_triage_run_marks_diagnosed_using_fp_obs_for_fingerprint(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams.triage_pregate import (
        ClassifiedSurface,
        SurfaceRecord,
        load_surface_state,
        surface_fingerprint,
    )

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    obs = {"rate": 0.3, "floor": 0.5, "sample_size": 15, "window_days": 30}
    fp_obs = {"rate": 0.3, "floor": 0.5}
    fp = surface_fingerprint(fp_obs)
    now_str = datetime.now(UTC).isoformat()
    eligible_surface = ClassifiedSurface(
        "finisher_regression", "finisher_regression", True, "finisher_rate_below_floor",
        obs=obs, fp_obs=fp_obs,
    )
    record = SurfaceRecord(
        fingerprint=fp, first_seen=now_str, last_seen=now_str, last_diagnosed_fp=None
    )

    def _one_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, stored, now
        return [eligible_surface], {"finisher_regression": record}

    monkeypatch.setattr("alan.teams.triage.decide", _one_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    loaded = load_surface_state(queue)
    assert "finisher_regression" in loaded
    assert loaded["finisher_regression"].last_diagnosed_fp == fp


def test_triage_run_saves_health_state(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.health import load_health_state

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    def _no_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, now
        return [], {}

    monkeypatch.setattr("alan.teams.triage.decide", _no_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    state = load_health_state(queue)
    assert state is not None
    assert state.verdict in ("healthy", "degraded")


def test_triage_run_health_state_healthy_when_no_blocked(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.health import load_health_state

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    def _no_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, stored, now
        return [], {}

    def _empty_gather(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr("alan.teams.triage.gather_all", _empty_gather)
    monkeypatch.setattr("alan.teams.triage.decide", _no_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    state = load_health_state(queue)
    assert state is not None
    assert state.verdict == "healthy"
    assert state.blocked_count == 0


def test_triage_health_state_save_exception_swallowed(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)

    def _boom(q: Any, h: Any) -> None:
        raise RuntimeError("save failed")

    monkeypatch.setattr("alan.teams.triage.save_health_state", _boom)

    def _no_eligible(surfaces: Any, stored: Any, now: Any) -> Any:
        del surfaces, stored, now
        return [], {}

    monkeypatch.setattr("alan.teams.triage.decide", _no_eligible)
    team.run(ws, None, _noop_emit, inv_id=inv_id)


def test_triage_sweep_recurs_beyond_second_cycle(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wall = [time.time()]

    def _fake_time() -> float:
        return wall[0]

    monkeypatch.setattr(time, "time", _fake_time)

    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = create_workspace("Failure Triage Sweep", "d", root=ws_root)

    for cycle in range(4):
        assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == [], (
            f"cycle {cycle}: expected no pending jobs at start"
        )

        team._do_schedule_next_sweep(ws)  # noqa: SLF001

        jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
        assert len(jobs) == 1, f"cycle {cycle}: expected 1 pending job after schedule"
        job = jobs[0]
        assert job.eligible_at is not None, f"cycle {cycle}: job must be deferred"
        assert job.eligible_at > wall[0], f"cycle {cycle}: eligible_at must be in the future"

        assert queue.claim() is None, f"cycle {cycle}: job must not be claimable yet"

        wall[0] = job.eligible_at + 1.0

        claimed = queue.claim()
        assert claimed is not None, f"cycle {cycle}: job must be claimable after time advance"
        assert claimed.id == job.id

        queue.complete(claimed.id)


def _make_fr(*, floor_alert: bool = False) -> Any:
    from alan.finisher_rate import FinisherRate, FunnelCounts, LeakReasons
    rate = 0.3 if floor_alert else 0.8
    return FinisherRate(
        rate=rate,
        counts=FunnelCounts(10, 9, 8, 7, 6, int(rate * 10)),
        leak_reasons=LeakReasons(crash_reasons={}, convergence_kinds={}),
        low_data=False,
        window_days=30,
        sample_size=10,
        floor_alert=floor_alert,
    )


def test_read_audit_text_returns_content(tmp_path: Path) -> None:
    from alan.teams.triage import _read_audit_text
    f = tmp_path / "test.jsonl"
    f.write_text('{"a": 1}\n', encoding="utf-8")
    assert _read_audit_text(f) == '{"a": 1}\n'


def test_read_audit_text_returns_empty_on_oserror(tmp_path: Path) -> None:
    from alan.teams.triage import _read_audit_text
    assert _read_audit_text(tmp_path / "nonexistent.jsonl") == ""


def test_read_audit_text_tails_large_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import triage
    from alan.teams.triage import _read_audit_text

    monkeypatch.setattr(triage, "_FR_AUDIT_TAIL_BYTES", 9)
    f = tmp_path / "big.jsonl"
    f.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    result = _read_audit_text(f)
    assert '{"b": 2}' in result
    assert '{"a": 1}' not in result


def test_workspace_meta_dicts_returns_serializable_dicts(
    ws_root: Path,
) -> None:
    from alan.teams.triage import _workspace_meta_dicts
    metas = _workspace_meta_dicts(ws_root)
    assert isinstance(metas, list)
    for m in metas:
        assert isinstance(m, dict)


def test_workspace_meta_dicts_tolerates_list_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams.triage import _workspace_meta_dicts

    def _boom(root: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("alan.teams.triage.workspace_mod.list_workspaces", _boom)
    assert _workspace_meta_dicts(None) == []


def test_compute_finisher_rate_returns_result_and_saves(
    queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    from alan.health import load_finisher_rate
    from alan.teams.triage import _compute_finisher_rate
    result = _compute_finisher_rate(queue, ws_root, tmp_path / "logs")
    assert isinstance(result.low_data, bool)
    loaded = load_finisher_rate(queue)
    assert loaded is not None
    assert loaded.window_days == result.window_days


def test_finisher_regression_surface_shape() -> None:
    from alan.finisher_rate import FINISHER_FLOOR
    from alan.teams.triage import _finisher_regression_surface
    fr = _make_fr(floor_alert=True)
    surface = _finisher_regression_surface(fr)
    assert surface.surface_id == "finisher_regression"
    assert surface.kind == "finisher_regression"
    assert surface.is_blocked is True
    assert surface.blocker_reason == "finisher_rate_below_floor"
    assert surface.obs["rate"] == fr.rate
    assert surface.obs["floor"] == FINISHER_FLOOR
    assert surface.obs["sample_size"] == fr.sample_size
    assert surface.obs["window_days"] == fr.window_days
    assert surface.fp_obs == {
        "rate": round(fr.rate, 2) if fr.rate is not None else None,
        "floor": FINISHER_FLOOR,
    }


def test_triage_run_injects_finisher_regression_surface_when_floor_alert(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.gather_all", lambda *a, **kw: [])
    monkeypatch.setattr("alan.teams.triage.decide", lambda s, st, n: ([], {}))
    fr = _make_fr(floor_alert=True)
    monkeypatch.setattr("alan.teams.triage._compute_finisher_rate", lambda q, _wr, _ad: fr)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    state = load_health_state(queue)
    assert state is not None
    assert any(f.kind == "finisher_regression" for f in state.findings)


def test_triage_run_no_finisher_surface_when_not_floor_alert(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.gather_all", lambda *a, **kw: [])
    monkeypatch.setattr("alan.teams.triage.decide", lambda s, st, n: ([], {}))
    fr = _make_fr(floor_alert=False)
    monkeypatch.setattr("alan.teams.triage._compute_finisher_rate", lambda q, _wr, _ad: fr)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    state = load_health_state(queue)
    assert state is None or not any(f.kind == "finisher_regression" for f in state.findings)


def test_triage_run_no_finisher_surface_when_compute_returns_none(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.gather_all", lambda *a, **kw: [])
    monkeypatch.setattr("alan.teams.triage.decide", lambda s, st, n: ([], {}))
    monkeypatch.setattr("alan.teams.triage._compute_finisher_rate", lambda q, _wr, _ad: None)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    state = load_health_state(queue)
    assert state is None or not any(f.kind == "finisher_regression" for f in state.findings)


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(
    returncode: int = 0,
    stdout: str = "",
    raises: BaseException | None = None,
) -> Any:
    def _impl(*_args: Any, **_kwargs: Any) -> _FakeProc:
        if raises is not None:
            raise raises
        return _FakeProc(returncode=returncode, stdout=stdout)
    return _impl


def test_read_git_log_text_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams.triage import _read_git_log_text

    monkeypatch.setattr(
        "alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout="commit log\n")
    )
    assert _read_git_log_text() == "commit log\n"


def test_read_git_log_text_nonzero_rc(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams.triage import _read_git_log_text

    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=1, stdout=""))
    assert _read_git_log_text() == ""


def test_read_git_log_text_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams.triage import _read_git_log_text

    monkeypatch.setattr(
        "alan.teams.triage.subprocess.run",
        _fake_run(raises=FileNotFoundError("git not found")),
    )
    assert _read_git_log_text() == ""


def test_derive_learning_metrics_returns_metrics_for_empty_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.learning_metrics import LearningMetrics
    from alan.teams.triage import _derive_learning_metrics

    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout=""))
    metrics = _derive_learning_metrics(tmp_path)
    assert isinstance(metrics, LearningMetrics)
    assert metrics.first_pass_approval_rate is None
    assert metrics.mean_fix_cycles_to_merge is None
    assert metrics.sample_size == 0


def test_compute_learning_metrics_safe_persists_blob(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_learning_metrics
    from alan.teams.triage import _compute_learning_metrics_safe

    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout=""))
    now = datetime.now(UTC)
    _compute_learning_metrics_safe(queue, tmp_path, now)
    blob = load_learning_metrics(queue)
    assert blob is not None
    assert "first_pass_approval_rate" in blob
    assert "mean_fix_cycles_to_merge" in blob
    assert "sample_size" in blob
    assert blob["computed_at"] == now.isoformat()


def test_compute_learning_metrics_safe_swallows_exception(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_learning_metrics
    from alan.teams.triage import _compute_learning_metrics_safe

    def _boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("derive failed")

    monkeypatch.setattr("alan.teams.triage._derive_learning_metrics", _boom)
    _compute_learning_metrics_safe(queue, tmp_path, datetime.now(UTC))
    assert load_learning_metrics(queue) is None


def test_triage_run_persists_learning_metrics_v1(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.health import load_learning_metrics
    from alan.learning_metrics import first_pass_approval_rate, mean_fix_cycles_to_merge
    from alan.trajectory import TrajectoryRecord

    team, ws, inv_id, _logs_dir = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout=""))
    fixed_records: list[TrajectoryRecord] = []
    monkeypatch.setattr("alan.teams.triage.derive_trajectories", lambda *a: fixed_records)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    blob = load_learning_metrics(queue)
    assert blob is not None
    assert blob["first_pass_approval_rate"] == first_pass_approval_rate(fixed_records)
    assert blob["mean_fix_cycles_to_merge"] == mean_fix_cycles_to_merge(fixed_records)
    assert "computed_at" in blob


def _blob_from_meta(queue: Queue) -> dict[str, Any]:
    raw = queue.get_meta(_MERGE_GATE_RECOVERY_META_KEY)
    assert raw is not None
    result: dict[str, Any] = json.loads(raw)
    return result


def test_compute_merge_gate_recovery_safe_persists_blob(
    queue: Queue, tmp_path: Path
) -> None:
    from alan.teams.triage import _compute_merge_gate_recovery_safe

    now = datetime.now(UTC)
    ts = now.isoformat()
    rows = [
        {"block_class": "genuine", "recovered": False, "timestamp_utc": ts},
        {"block_class": "behind_plus_transient", "recovered": True, "timestamp_utc": ts},
    ]
    (tmp_path / _MERGE_GATE_AUDIT_FILE).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    _compute_merge_gate_recovery_safe(queue, tmp_path, now)
    blob = _blob_from_meta(queue)
    assert blob["rates"] == {
        "genuine": {"recovered": 0, "total": 1, "lifetime_total": 1},
        "behind_plus_transient": {"recovered": 1, "total": 1, "lifetime_total": 1},
    }
    assert blob["computed_at"] == now.isoformat()


def test_compute_merge_gate_recovery_safe_no_audit_file_saves_none_rates(
    queue: Queue, tmp_path: Path
) -> None:
    from alan.teams.triage import _compute_merge_gate_recovery_safe

    now = datetime.now(UTC)
    _compute_merge_gate_recovery_safe(queue, tmp_path, now)
    blob = _blob_from_meta(queue)
    assert blob["rates"] is None
    assert blob["computed_at"] == now.isoformat()


def test_compute_merge_gate_recovery_safe_swallows_exception(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.triage import _compute_merge_gate_recovery_safe

    def _boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("reduce failed")

    monkeypatch.setattr("alan.teams.triage.merge_gate_recovery_rates", _boom)
    _compute_merge_gate_recovery_safe(queue, tmp_path, datetime.now(UTC))
    assert queue.get_meta(_MERGE_GATE_RECOVERY_META_KEY) is None


def test_triage_run_persists_merge_gate_recovery_v1(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team, ws, inv_id, logs_dir = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout=""))
    ts = datetime.now(UTC).isoformat()
    rows = [
        {"block_class": "sonar_hotspot", "recovered": True, "timestamp_utc": ts},
        {"block_class": "sonar_hotspot", "recovered": False, "timestamp_utc": ts},
    ]
    (logs_dir / _MERGE_GATE_AUDIT_FILE).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    blob = _blob_from_meta(queue)
    assert blob["rates"] == {"sonar_hotspot": {"recovered": 1, "total": 2, "lifetime_total": 2}}
    assert "computed_at" in blob


def test_triage_run_never_scans_merge_gate_audit_text_for_learning_metrics(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.learning_metrics import merge_gate_recovery_rates as _real_reducer

    calls: list[str] = []

    def _spy(text: str, **kw: Any) -> Any:
        calls.append(text)
        return _real_reducer(text, **kw)

    team, ws, inv_id, logs_dir = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    monkeypatch.setattr("alan.teams.triage.subprocess.run", _fake_run(returncode=0, stdout=""))
    monkeypatch.setattr("alan.teams.triage.merge_gate_recovery_rates", _spy)
    (logs_dir / _MERGE_GATE_AUDIT_FILE).write_text(
        json.dumps({"block_class": "genuine", "recovered": False}) + "\n", encoding="utf-8"
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert len(calls) == 1


def _sweep_row(*, timestamp_utc: str, surface_count: int, blocked_count: int) -> str:
    return json.dumps({
        "action": "sweep_complete",
        "timestamp_utc": timestamp_utc,
        "task_id": "t",
        "inv_id": "inv_001",
        "surface_count": surface_count,
        "blocked_count": blocked_count,
    })


def test_read_sweeper_status_no_file_returns_all_none(tmp_path: Path) -> None:
    from alan.teams.triage import read_sweeper_status

    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": None,
        "surface_count": None,
        "blocked_count": None,
        "recent_actions": [],
    }


def test_read_sweeper_status_empty_file_returns_all_none(tmp_path: Path) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    (tmp_path / _AUDIT_FILE).write_text("", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": None,
        "surface_count": None,
        "blocked_count": None,
        "recent_actions": [],
    }


def test_read_sweeper_status_returns_most_recent_among_interleaved_rows(
    tmp_path: Path,
) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        _sweep_row(timestamp_utc="t1", surface_count=1, blocked_count=0),
        json.dumps({"action": "invocation_slots_pruned", "removed": 2}),
        _sweep_row(timestamp_utc="t2", surface_count=3, blocked_count=1),
        json.dumps({"action": "seed_triage_failed", "source": "reschedule", "error": "x"}),
    ]
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": "t2",
        "surface_count": 3,
        "blocked_count": 1,
        "recent_actions": [
            {"action": "sweep_complete", "timestamp_utc": "t2"},
            {"action": "sweep_complete", "timestamp_utc": "t1"},
        ],
    }


def test_read_sweeper_status_recent_actions_include_non_sweep_rows_with_timestamps(
    tmp_path: Path,
) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        json.dumps({"action": "invocation_slots_pruned", "timestamp_utc": "t1", "removed": 2}),
        _sweep_row(timestamp_utc="t2", surface_count=3, blocked_count=1),
        json.dumps({
            "action": "seed_triage_failed",
            "timestamp_utc": "t3",
            "source": "reschedule",
            "error": "x",
        }),
    ]
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result["recent_actions"] == [
        {"action": "seed_triage_failed", "timestamp_utc": "t3"},
        {"action": "sweep_complete", "timestamp_utc": "t2"},
        {"action": "invocation_slots_pruned", "timestamp_utc": "t1"},
    ]


def test_read_sweeper_status_no_sweep_complete_rows_returns_all_none(
    tmp_path: Path,
) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        json.dumps({"action": "invocation_slots_pruned", "removed": 2}),
        json.dumps({"action": "seed_triage_failed", "source": "reschedule", "error": "x"}),
    ]
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": None,
        "surface_count": None,
        "blocked_count": None,
        "recent_actions": [],
    }


def test_read_sweeper_status_skips_malformed_json_lines(tmp_path: Path) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        "{not valid json",
        _sweep_row(timestamp_utc="t1", surface_count=5, blocked_count=2),
        "another } broken { line",
    ]
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": "t1",
        "surface_count": 5,
        "blocked_count": 2,
        "recent_actions": [{"action": "sweep_complete", "timestamp_utc": "t1"}],
    }


def test_read_sweeper_status_ignores_non_dict_json_lines(tmp_path: Path) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        _sweep_row(timestamp_utc="t1", surface_count=5, blocked_count=2),
        json.dumps([1, 2, 3]),
        json.dumps("just a string"),
    ]
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": "t1",
        "surface_count": 5,
        "blocked_count": 2,
        "recent_actions": [{"action": "sweep_complete", "timestamp_utc": "t1"}],
    }


def test_read_sweeper_status_bounded_to_last_ten_rows(tmp_path: Path) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [_sweep_row(timestamp_utc="stale", surface_count=9, blocked_count=9)]
    lines.extend(
        json.dumps({"action": "invocation_slots_pruned", "removed": i}) for i in range(20)
    )
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": None,
        "surface_count": None,
        "blocked_count": None,
        "recent_actions": [],
    }


def test_read_sweeper_status_never_reads_full_growing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    lines = [
        json.dumps({"action": "invocation_slots_pruned", "removed": i, "pad": "x" * 500})
        for i in range(500)
    ]
    lines.append(_sweep_row(timestamp_utc="fresh", surface_count=7, blocked_count=4))
    (tmp_path / _AUDIT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _boom(self: Path, *a: Any, **kw: Any) -> str:
        raise AssertionError("must not perform a full-file read")

    monkeypatch.setattr(Path, "read_text", _boom)
    result = read_sweeper_status(tmp_path)
    assert result == {
        "timestamp_utc": "fresh",
        "surface_count": 7,
        "blocked_count": 4,
        "recent_actions": [{"action": "sweep_complete", "timestamp_utc": "fresh"}],
    }


def test_read_sweeper_status_defaults_to_audit_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams.triage import _AUDIT_FILE, read_sweeper_status

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    (tmp_path / _AUDIT_FILE).write_text(
        _sweep_row(timestamp_utc="t9", surface_count=2, blocked_count=0) + "\n",
        encoding="utf-8",
    )
    result = read_sweeper_status()
    assert result == {
        "timestamp_utc": "t9",
        "surface_count": 2,
        "blocked_count": 0,
        "recent_actions": [{"action": "sweep_complete", "timestamp_utc": "t9"}],
    }


def test_next_eligible_sweep_at_none_when_meta_absent(queue: Queue) -> None:
    from alan.teams.triage import next_eligible_sweep_at

    assert next_eligible_sweep_at(queue) is None


def test_next_eligible_sweep_at_none_when_meta_malformed(queue: Queue) -> None:
    from alan.teams.triage import next_eligible_sweep_at

    queue.set_meta(_CADENCE_META_KEY, "not-a-number")
    assert next_eligible_sweep_at(queue) is None


def test_next_eligible_sweep_at_derived_from_last_scheduled_marker(queue: Queue) -> None:
    from alan.teams.triage import next_eligible_sweep_at

    last_scheduled = 1752221999.0
    queue.set_meta(_CADENCE_META_KEY, str(last_scheduled))
    expected = datetime.fromtimestamp(
        last_scheduled + _CADENCE_SECONDS, tz=UTC
    ).isoformat()
    assert next_eligible_sweep_at(queue) == expected


def test_triage_run_calls_retention_hook(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)
    calls: list[int] = []

    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, run_retention=lambda: calls.append(1)
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert calls == [1]


def test_triage_run_swallows_retention_errors(
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)

    def _boom() -> None:
        raise RuntimeError("retention exploded")

    team = TriageTeam(queue=queue, workspaces_root=ws_root, run_retention=_boom)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == "triage"


def test_triage_run_folds_in_retention_capacity_surfaces(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)

    near_cap_surface = ClassifiedSurface(
        surface_id="retention_near_cap:task-x",
        kind="retention_near_cap",
        is_blocked=True,
        blocker_reason="retention_near_cap",
        obs={"task_id": "task-x", "team": "triage", "title": "t", "used": 901, "cap": 999},
    )

    def _findings(root: Path | None) -> list[ClassifiedSurface]:
        del root
        return [near_cap_surface]

    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, retention_capacity_findings=_findings
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    state = load_health_state(queue)
    assert state is not None
    assert any(f.kind == "retention_near_cap" for f in state.findings)


def test_triage_run_swallows_retention_capacity_findings_exception(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)

    def _boom(root: Path | None) -> list[Any]:
        del root
        raise RuntimeError("registry scan failed")

    team = TriageTeam(queue=queue, workspaces_root=ws_root, retention_capacity_findings=_boom)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert handover.from_team == "triage"
    state = load_health_state(queue)
    assert state is None or not any(f.kind == "retention_near_cap" for f in state.findings)


def test_triage_run_no_retention_capacity_surfaces_when_hook_unset(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    team.run(ws, None, _noop_emit, inv_id=inv_id)
    state = load_health_state(queue)
    assert state is None or not any(f.kind == "retention_near_cap" for f in state.findings)


def test_triage_run_calls_check_credential_expiry_with_now(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)
    calls: list[datetime] = []

    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, check_credential_expiry=calls.append
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert len(calls) == 1
    assert calls[0].tzinfo is not None


def test_triage_run_uses_consistent_now_for_credential_expiry_sensors(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import secrets_store

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)
    check_calls: list[datetime] = []
    surface_calls: list[datetime] = []

    def _capture_expiring_secrets(now: datetime) -> list[Any]:
        surface_calls.append(now)
        return []

    monkeypatch.setattr(secrets_store, "expiring_secrets", _capture_expiring_secrets)
    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, check_credential_expiry=check_calls.append
    )
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert len(check_calls) == 1
    assert len(surface_calls) == 1
    assert check_calls[0] == surface_calls[0]


def test_triage_run_swallows_check_credential_expiry_errors(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    _seed_audit_files(logs_dir)
    ws, inv_id = _seed_triage_ws(ws_root)

    def _boom(now: datetime) -> None:
        del now
        raise RuntimeError("credential expiry check failed")

    team = TriageTeam(queue=queue, workspaces_root=ws_root, check_credential_expiry=_boom)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert handover.from_team == "triage"


def test_triage_run_no_credential_expiry_check_when_hook_unset(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == "triage"


def test_augmented_surfaces_builds_credential_expiry_surface_from_expiring_secrets(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import secrets_store

    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    meta = secrets_store.SecretRecordMeta(
        name="engine-a:transport", fingerprint="abc", last_verified=None,
        expires_at="2099-01-01", expiry_warned_at=None,
    )
    monkeypatch.setattr(secrets_store, "expiring_secrets", lambda now: [meta])

    augmented = team._augmented_surfaces([], tmp_path / "logs", datetime.now(UTC), [])  # noqa: SLF001

    surfaces = [s for s in augmented if s.kind == "credential_expiry"]
    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.surface_id == "credential_expiry:engine-a:transport"
    assert surface.kind == "credential_expiry"
    assert surface.is_blocked is True
    assert surface.obs == {
        "name": "engine-a:transport",
        "expires_at": "2099-01-01",
        "engine_key": "engine-a",
    }


def test_triage_run_injects_credential_expiry_surface(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import secrets_store
    from alan.health import load_health_state

    team, ws, inv_id, _ = _setup_logs_and_team(monkeypatch, queue, ws_root, tmp_path)
    meta = secrets_store.SecretRecordMeta(
        name="engine-a:transport", fingerprint="abc", last_verified=None,
        expires_at="2099-01-01", expiry_warned_at=None,
    )
    monkeypatch.setattr(secrets_store, "expiring_secrets", lambda now: [meta])

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    state = load_health_state(queue)
    assert state is not None
    assert any(f.kind == "credential_expiry" for f in state.findings)
    assert state.blocked_count >= 1

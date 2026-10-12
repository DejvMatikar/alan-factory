from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NoReturn

import pytest

from alan import secrets_store
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.queue import Queue
from alan.teams import triage as triage_mod
from alan.teams._triage_driver_loops import (
    ATTENTION_KIND,
    CONFIG_QUARANTINE_KIND,
    UNHEALTHY_KIND,
)
from alan.teams.triage import (
    _GUARDED_STEPS,  # noqa: SLF001
    _TEAM_NAME,
    ROLE_SPECS,
    TriageSweepStatus,
    TriageTeam,
    _GuardedSteps,  # noqa: SLF001
    _save_surface_state_safe,  # noqa: SLF001
    _write_triage_sweep_status,  # noqa: SLF001
    load_triage_sweep_status,
)
from alan.workspace import Workspace


def _boom(*_a: object, **_kw: object) -> NoReturn:
    raise RuntimeError("boom")


def _forced_attrs(team: TriageTeam) -> dict[str, tuple[object, str]]:
    return {
        "credential_expiry": (team, "check_credential_expiry"),
        "finisher_rate": (triage_mod, "_compute_finisher_rate"),
        "retention_capacity": (team, "retention_capacity_findings"),
        "stale_live_process": (triage_mod, "_stale_live_process_surface"),
        "driver_loops": (triage_mod, "driver_loop_surfaces"),
        "driver_loop_quarantine": (triage_mod, "driver_loop_config_quarantine_surfaces"),
        "credential_expiry_surfaces": (secrets_store, "expiring_secrets"),
        "auto_update": (triage_mod, "auto_update_surfaces"),
        "main_ci_red": (team, "on_main_ci_red"),
        "health_state": (triage_mod, "save_health_state"),
        "reconcile_incidents": (team, "reconcile_incidents"),
    }


def _force_raising(
    monkeypatch: pytest.MonkeyPatch, team: TriageTeam, steps: Iterable[str]
) -> None:
    forced = _forced_attrs(team)
    for step in steps:
        target, attr = forced[step]
        monkeypatch.setattr(target, attr, _boom)


def _noop_emit(event: Any) -> None:
    del event


def _seed_triage_ws(ws_root: Path) -> tuple[Workspace, str]:
    ws = workspace_mod.create("Failure Triage Sweep", "d", root=ws_root, team=_TEAM_NAME)
    inv_id = workflow_mod.seed_pending_workflow(ws, team=_TEAM_NAME, tasks=list(ROLE_SPECS))
    return ws, inv_id


def _last_sweep_complete_row(tmp_path: Path) -> dict[str, Any]:
    audit_file = tmp_path / "logs" / "triage_audit.jsonl"
    rows: list[dict[str, Any]] = [
        json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    sweep_rows = [r for r in rows if r["action"] == "sweep_complete"]
    return sweep_rows[-1]


def test_write_then_load_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    _write_triage_sweep_status("success", 4.5)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "success"
    assert status.duration_seconds == 4.5
    assert status.computed_at


def test_load_triage_sweep_status_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    assert load_triage_sweep_status() is None


def test_load_triage_sweep_status_returns_none_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams._triage_sweep_status import _triage_sweep_status_path  # noqa: SLF001

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    path = _triage_sweep_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert load_triage_sweep_status() is None


def test_write_triage_sweep_status_swallows_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("alan.teams._triage_sweep_status.store.write_text_mkstemp", _raise)
    _write_triage_sweep_status("failure", 1.0)
    assert load_triage_sweep_status() is None


def test_triage_sweep_status_is_frozen_model() -> None:
    status = TriageSweepStatus(outcome="success", duration_seconds=1.0, computed_at="t")
    with pytest.raises(Exception):  # noqa: B017, PT011 - pydantic frozen-model error
        status.outcome = "failure"  # type: ignore[misc]


def test_run_success_records_success_outcome_and_duration(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "success"
    assert status.duration_seconds >= 0.0

    row = _last_sweep_complete_row(tmp_path)
    assert row["outcome"] == "success"
    assert row["duration_seconds"] >= 0.0


def test_run_finisher_rate_none_without_exception_is_still_success(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    monkeypatch.setattr("alan.teams.triage._compute_finisher_rate", lambda q, _wr, _ad: None)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "success"


def test_run_merge_gate_recovery_exception_marks_degraded(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("reduce failed")

    monkeypatch.setattr("alan.teams.triage.merge_gate_recovery_rates", _boom)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"


def test_run_restart_recovery_exception_marks_degraded(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("counts failed")

    monkeypatch.setattr("alan.teams.triage.restart_recovery_counts", _boom)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"


def test_run_learning_metrics_exception_marks_degraded(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("derive failed")

    monkeypatch.setattr("alan.teams.triage._derive_learning_metrics", _boom)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"


def test_run_health_state_save_exception_marks_degraded(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("save health state failed")

    monkeypatch.setattr("alan.teams.triage.save_health_state", _boom)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"
    row = _last_sweep_complete_row(tmp_path)
    assert row["outcome"] == "degraded"


def test_run_retention_hook_exception_marks_degraded(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)

    def _boom() -> object:
        raise RuntimeError("retention hook failed")

    team = TriageTeam(queue=queue, workspaces_root=ws_root, run_retention=_boom)

    team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "degraded"
    row = _last_sweep_complete_row(tmp_path)
    assert row["outcome"] == "degraded"


@pytest.mark.parametrize(
    ("step", "name", "absent_kinds"),
    [
        ("credential_expiry", "credential_expiry", ()),
        ("finisher_rate", "finisher_rate", ("finisher_regression",)),
        ("retention_capacity", "retention_capacity", ("retention_near_cap",)),
        ("driver_loops", "driver_loops", (UNHEALTHY_KIND, ATTENTION_KIND)),
        ("driver_loop_quarantine", "driver_loops", (CONFIG_QUARANTINE_KIND,)),
        ("credential_expiry_surfaces", "credential_expiry_surfaces", ("credential_expiry",)),
        ("auto_update", "auto_update", ("auto_update",)),
        ("main_ci_red", "main_ci_red", ()),
        ("health_state", "health_state", ()),
        ("reconcile_incidents", "reconcile_incidents", ()),
    ],
)
def test_guarded_step_raising_records_only_its_degraded_name(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    name: str,
    absent_kinds: tuple[str, ...],
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    _force_raising(monkeypatch, team, [step])

    surfaces, _, degraded = team._run_sensors()  # noqa: SLF001

    assert degraded == [name]
    assert {s.kind for s in surfaces}.isdisjoint(absent_kinds)


def test_stale_live_step_raising_records_nothing_and_sweep_succeeds(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    _force_raising(monkeypatch, team, ["stale_live_process"])

    _, _, degraded = team._run_sensors()  # noqa: SLF001
    team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert degraded == []
    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "success"
    assert _last_sweep_complete_row(tmp_path)["outcome"] == "success"


def test_all_guarded_steps_raising_record_names_in_sweep_order(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    _force_raising(monkeypatch, team, _GuardedSteps._fields)

    _, _, degraded = team._run_sensors()  # noqa: SLF001

    assert degraded == [
        "credential_expiry",
        "finisher_rate",
        "retention_capacity",
        "driver_loops",
        "driver_loops",
        "credential_expiry_surfaces",
        "auto_update",
        "main_ci_red",
        "health_state",
        "reconcile_incidents",
    ]


def test_guarded_step_forcers_cover_every_table_row(queue: Queue, ws_root: Path) -> None:
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    assert set(_forced_attrs(team)) == set(_GUARDED_STEPS._asdict())


def test_save_surface_state_safe_records_degraded_when_list_provided(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("alan.teams.triage.save_surface_state", _boom)
    degraded: list[str] = []
    _save_surface_state_safe(queue, {}, degraded)
    assert degraded == ["surface_state"]


def test_run_sensor_raises_records_failure_outcome(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws, inv_id = _seed_triage_ws(ws_root)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)

    def _raise(*_a: Any, **_kw: Any) -> list[Any]:
        raise RuntimeError("sensor failure")

    monkeypatch.setattr("alan.teams.triage.gather_all", _raise)

    with pytest.raises(RuntimeError, match="sensor failure"):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_triage_sweep_status()
    assert status is not None
    assert status.outcome == "failure"
    assert status.duration_seconds >= 0.0

    row = _last_sweep_complete_row(tmp_path)
    assert row["outcome"] == "failure"
    assert row["surface_count"] == 0
    assert row["blocked_count"] == 0

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import reconciler as reconciler_mod
from alan import workspace as workspace_mod
from alan._dispatcher_base import _stamp_snapshot  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import (
    DependencySweepConfig,
    EngineConfig,
    ForgeConfig,
    Posture,
    RepoIdentity,
    admitted_teams,
    hide_and_persist,
    register,
)
from alan.health import compute_health_state
from alan.queue import JobStatus, Queue
from alan.teams.dependency_sweep import DependencySweepTeam
from alan.teams.triage import (
    _CADENCE_META_KEY,
    TriageTeam,
)
from alan.teams.triage_pregate import ClassifiedSurface

_HIDDEN_REPO = RepoIdentity(owner="acme", repo="parked")
_VISIBLE_REPO = RepoIdentity(owner="acme", repo="active")


@pytest.fixture(autouse=True)
def _isolated_engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


def _hide(repo: RepoIdentity) -> None:
    register(EngineConfig(repo=repo, hidden=True), validate_env=False)


def _show(repo: RepoIdentity) -> None:
    register(EngineConfig(repo=repo), validate_env=False)


def _forge_for(repo: RepoIdentity) -> ForgeConfig:
    return ForgeConfig(repo=repo, credential_env_name=f"{repo.repo.upper()}_GH_TOKEN")


def _ci_surface(surface_id: str, repo: str, *, blocked: bool = True) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=surface_id,
        kind="main_ci",
        is_blocked=blocked,
        blocker_reason="ci_failure" if blocked else "",
        obs={"repo": repo, "conclusion": "failure" if blocked else "success"},
    )


def test_compute_health_state_filters_hidden_repo_ci_finding() -> None:
    _hide(_HIDDEN_REPO)
    _show(_VISIBLE_REPO)
    surfaces = [
        _ci_surface("main_ci:acme/parked", _HIDDEN_REPO.slug),
        _ci_surface("main_ci:acme/active", _VISIBLE_REPO.slug),
    ]

    state = compute_health_state(surfaces, datetime.now(UTC))

    assert {f.repo for f in state.findings} == {_VISIBLE_REPO.slug}
    assert state.blocked_count == 1
    assert state.verdict == "degraded"


def test_compute_health_state_all_hidden_yields_healthy_verdict() -> None:
    _hide(_HIDDEN_REPO)
    surfaces = [_ci_surface("main_ci:acme/parked", _HIDDEN_REPO.slug)]

    state = compute_health_state(surfaces, datetime.now(UTC))

    assert state.findings == []
    assert state.blocked_count == 0
    assert state.verdict == "healthy"


def test_compute_health_state_filters_hidden_repo_build_task_finding(ws_root: Path) -> None:
    _hide(_HIDDEN_REPO)
    ws = workspace_mod.create("t", "d", root=ws_root, engine_key=_HIDDEN_REPO.slug)
    surface = ClassifiedSurface(
        surface_id=ws.task_id,
        kind="build_task",
        is_blocked=True,
        blocker_reason="job_failed",
        obs={"job_failed": True, "job_running": False},
    )

    state = compute_health_state([surface], datetime.now(UTC), workspaces_root=ws_root)

    assert state.findings == []
    assert state.blocked_count == 0


def test_compute_health_state_filters_hidden_repo_resolvable_findings(ws_root: Path) -> None:
    _hide(_HIDDEN_REPO)
    ws = workspace_mod.create("t", "d", root=ws_root, engine_key=_HIDDEN_REPO.slug)
    surface = ClassifiedSurface(
        surface_id=ws.task_id,
        kind="build_task",
        is_blocked=False,
        blocker_reason="",
        obs={},
        resolvable=True,
    )

    state = compute_health_state([surface], datetime.now(UTC), workspaces_root=ws_root)

    assert state.resolvable_findings == []


def test_compute_health_state_no_hidden_engines_registered_unaffected() -> None:
    surfaces = [_ci_surface("main_ci:acme/active", _VISIBLE_REPO.slug)]

    state = compute_health_state(surfaces, datetime.now(UTC))

    assert state.blocked_count == 1
    assert state.verdict == "degraded"
    assert state.findings[0].repo == _VISIBLE_REPO.slug


def _credential_expiry_surface(name: str, engine_key: str | None) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"credential_expiry:{name}",
        kind="credential_expiry",
        is_blocked=True,
        blocker_reason="credential_expiry",
        obs={"name": name, "expires_at": "2099-01-01", "engine_key": engine_key},
    )


def test_health_findings_exclude_hidden_repo_keep_visible_and_global() -> None:
    _hide(_HIDDEN_REPO)
    _show(_VISIBLE_REPO)
    surfaces = [
        _ci_surface("main_ci:hidden", _HIDDEN_REPO.slug),
        _credential_expiry_surface("visible-cred", _VISIBLE_REPO.slug),
        _credential_expiry_surface("global-cred", None),
    ]

    state = compute_health_state(surfaces, datetime.now(UTC))

    finding_ids = {f.finding_id for f in state.findings}
    assert "main_ci:hidden" not in finding_ids
    assert {"credential_expiry:visible-cred", "credential_expiry:global-cred"}.issubset(
        finding_ids
    )


def test_health_findings_no_hidden_engines_keeps_all_findings() -> None:
    surfaces = [
        _credential_expiry_surface("a", None),
        _credential_expiry_surface("b", _VISIBLE_REPO.slug),
    ]

    state = compute_health_state(surfaces, datetime.now(UTC))

    finding_ids = {f.finding_id for f in state.findings}
    assert {"credential_expiry:a", "credential_expiry:b"}.issubset(finding_ids)


def test_health_findings_global_finding_stays_visible_when_default_engine_hidden() -> None:
    register(EngineConfig(repo=_HIDDEN_REPO, hidden=True), default=True, validate_env=False)
    surfaces = [
        _credential_expiry_surface("global-cred", None),
        _credential_expiry_surface("scoped-hidden-cred", _HIDDEN_REPO.slug),
    ]

    state = compute_health_state(surfaces, datetime.now(UTC))

    finding_ids = {f.finding_id for f in state.findings}
    assert "credential_expiry:global-cred" in finding_ids
    assert "credential_expiry:scoped-hidden-cred" not in finding_ids


def test_triage_do_schedule_next_sweep_skips_hidden_engine(
    queue: Queue, ws_root: Path
) -> None:
    _hide(_HIDDEN_REPO)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = workspace_mod.create(
        "Failure Triage Sweep", "d", root=ws_root, engine_key=_HIDDEN_REPO.slug
    )

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta(_CADENCE_META_KEY) is None


def test_triage_do_schedule_next_sweep_reschedules_when_engine_not_hidden(
    queue: Queue, ws_root: Path
) -> None:
    _show(_VISIBLE_REPO)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = workspace_mod.create(
        "Failure Triage Sweep", "d", root=ws_root, engine_key=_VISIBLE_REPO.slug
    )

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert queue.get_meta(_CADENCE_META_KEY) is not None


def test_triage_do_schedule_next_sweep_skips_hidden_engine_with_stale_snapshot(
    queue: Queue, ws_root: Path
) -> None:
    _show(_HIDDEN_REPO)
    team = TriageTeam(queue=queue, workspaces_root=ws_root)
    ws = workspace_mod.create(
        "Failure Triage Sweep", "d", root=ws_root, engine_key=_HIDDEN_REPO.slug
    )
    ws = _stamp_snapshot(ws)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.config is not None
    assert ws.meta.engine_snapshot.config.hidden is False
    hide_and_persist(_HIDDEN_REPO)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta(_CADENCE_META_KEY) is None


def test_dep_sweep_do_schedule_next_sweep_skips_hidden_engine_even_if_enabled(
    queue: Queue, ws_root: Path
) -> None:
    register(
        EngineConfig(
            repo=_HIDDEN_REPO,
            hidden=True,
            forge=_forge_for(_HIDDEN_REPO),
            dependency_sweep=DependencySweepConfig(enabled=True),
        ),
        validate_env=False,
    )
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root,
        engine_key=_HIDDEN_REPO.slug,
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_HIDDEN_REPO.slug}") is None


def test_dep_sweep_do_schedule_next_sweep_reschedules_when_not_hidden(
    queue: Queue, ws_root: Path
) -> None:
    register(
        EngineConfig(
            repo=_VISIBLE_REPO,
            forge=_forge_for(_VISIBLE_REPO),
            dependency_sweep=DependencySweepConfig(enabled=True),
        ),
        validate_env=False,
    )
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root,
        engine_key=_VISIBLE_REPO.slug,
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_VISIBLE_REPO.slug}") is not None


def test_dep_sweep_do_schedule_next_sweep_skips_hidden_engine_with_stale_snapshot(
    queue: Queue, ws_root: Path
) -> None:
    register(
        EngineConfig(
            repo=_HIDDEN_REPO,
            forge=_forge_for(_HIDDEN_REPO),
            dependency_sweep=DependencySweepConfig(enabled=True),
        ),
        validate_env=False,
    )
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root,
        engine_key=_HIDDEN_REPO.slug,
    )
    ws = _stamp_snapshot(ws)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.config is not None
    assert ws.meta.engine_snapshot.config.hidden is False
    hide_and_persist(_HIDDEN_REPO)
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_HIDDEN_REPO.slug}") is None


def test_hidden_floor_posture_denies_all_four_recurring_teams() -> None:
    all_recurring = {"triage", "dependency_sweep", "deploy_verify", "maintenance"}

    assert admitted_teams(all_recurring, Posture.observe) == set()
    assert admitted_teams(all_recurring, Posture.self_merge) == set()
    assert admitted_teams(all_recurring, Posture.autonomous) == all_recurring


def test_seed_dep_sweep_skips_hidden_engine_at_startup(queue: Queue, ws_root: Path) -> None:
    register(
        EngineConfig(
            repo=_HIDDEN_REPO,
            hidden=True,
            forge=_forge_for(_HIDDEN_REPO),
            dependency_sweep=DependencySweepConfig(enabled=True),
        ),
        validate_env=False,
    )

    reconciler_mod.seed_dep_sweep(queue, ws_root)

    assert workspace_mod.list_workspaces(root=ws_root) == []


def test_seed_dep_sweep_still_seeds_when_engine_not_hidden(queue: Queue, ws_root: Path) -> None:
    register(
        EngineConfig(
            repo=_VISIBLE_REPO,
            forge=_forge_for(_VISIBLE_REPO),
            dependency_sweep=DependencySweepConfig(enabled=True),
        ),
        validate_env=False,
    )

    reconciler_mod.seed_dep_sweep(queue, ws_root)

    all_ws = workspace_mod.list_workspaces(root=ws_root)
    dep_ws = next(w for w in all_ws if w.meta.team == "dependency_sweep")
    assert dep_ws.meta.engine_key == _VISIBLE_REPO.slug
    jobs = queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1

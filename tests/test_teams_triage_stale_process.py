from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan.live_staleness import StaleLiveProcess
from alan.queue import Queue
from alan.teams import triage as triage_mod
from alan.teams.triage import (
    _DEFAULT_DEPLOY_SURFACE_ID,
    _DEFAULT_MAIN_CI_SURFACE_ID,
    ROLE_SPECS,
    TriageTeam,
    _find_surface,
    _stale_live_process_surface,
)
from alan.teams.triage_pregate import ClassifiedSurface
from alan.workspace import create as create_workspace

_DETECT_PATH = "alan.teams.triage.live_staleness.detect"
_SERVED_SHA_PATH = "alan.teams.triage.live_staleness.served_sha_mod.served_sha"


def _main_ci_surface(sha: str = "b" * 40, status: str = "completed") -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=_DEFAULT_MAIN_CI_SURFACE_ID,
        kind="main_ci",
        is_blocked=False,
        blocker_reason="",
        obs={"sha": sha, "status": status, "conclusion": "success"},
    )


def _deploy_surface(
    status: str = "completed", sha: str = "b" * 40, *, empty: bool = False
) -> ClassifiedSurface:
    obs: dict[str, Any] = {} if empty else {"sha": sha, "status": status, "conclusion": "success"}
    return ClassifiedSurface(
        surface_id=_DEFAULT_DEPLOY_SURFACE_ID,
        kind="deploy",
        is_blocked=False,
        blocker_reason="",
        obs=obs,
    )


def test_find_surface_matches_by_id() -> None:
    s = _main_ci_surface()
    assert _find_surface([s], _DEFAULT_MAIN_CI_SURFACE_ID) is s


def test_find_surface_none_when_absent() -> None:
    assert _find_surface([], _DEFAULT_MAIN_CI_SURFACE_ID) is None


def test_stale_live_process_surface_none_when_main_ci_missing() -> None:
    now = datetime.now(UTC)
    assert _stale_live_process_surface([_deploy_surface()], now) is None


def test_stale_live_process_surface_none_when_deploy_missing() -> None:
    now = datetime.now(UTC)
    assert _stale_live_process_surface([_main_ci_surface()], now) is None


def test_stale_live_process_surface_none_when_detect_returns_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(_DETECT_PATH, lambda **kw: (None, "within_settle_window"))
    surfaces = [_main_ci_surface(), _deploy_surface()]
    assert _stale_live_process_surface(surfaces, now) is None


def test_stale_live_process_surface_builds_classified_surface_on_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    finding = StaleLiveProcess(
        served_sha="a" * 40, target_sha="b" * 40, hot_paths=["alan/server.py"]
    )
    monkeypatch.setattr(_DETECT_PATH, lambda **kw: (finding, ""))
    surfaces = [_main_ci_surface(), _deploy_surface()]
    result = _stale_live_process_surface(surfaces, now)
    assert result is not None
    assert result.surface_id == f"stale_live_process:{'b' * 12}"
    assert result.kind == "stale_live_process"
    assert result.is_blocked is True
    assert result.blocker_reason == "hot_paths"
    assert result.obs == {
        "served_sha": "a" * 40,
        "target_sha": "b" * 40,
        "hot_paths": ["alan/server.py"],
    }
    assert result.fp_obs == {"served_sha": "a" * 40, "target_sha": "b" * 40}
    assert "repo" not in result.obs


def test_stale_live_process_surface_id_differs_across_target_shas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)

    def _fake_detect(**kw: Any) -> tuple[StaleLiveProcess | None, str]:
        target = kw["target_sha"]
        return StaleLiveProcess(served_sha="a" * 40, target_sha=target, hot_paths=["x.py"]), ""

    monkeypatch.setattr(_DETECT_PATH, _fake_detect)
    r1 = _stale_live_process_surface([_main_ci_surface(sha="b" * 40), _deploy_surface()], now)
    r2 = _stale_live_process_surface([_main_ci_surface(sha="c" * 40), _deploy_surface()], now)
    assert r1 is not None
    assert r2 is not None
    assert r1.surface_id != r2.surface_id
    assert r1.surface_id == f"stale_live_process:{'b' * 12}"
    assert r2.surface_id == f"stale_live_process:{'c' * 12}"


def test_stale_live_process_surface_passes_gathered_obs_without_new_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    captured: dict[str, Any] = {}

    def _fake_detect(**kw: Any) -> tuple[StaleLiveProcess | None, str]:
        captured.update(kw)
        return None, "no_divergence"

    monkeypatch.setattr(_DETECT_PATH, _fake_detect)
    surfaces = [
        _main_ci_surface(sha="b" * 40, status="completed"),
        _deploy_surface(status="in_progress"),
    ]
    _stale_live_process_surface(surfaces, now)
    assert captured["target_sha"] == "b" * 40
    assert captured["main_ci_status"] == "completed"
    assert captured["deploy_status"] == "in_progress"
    assert captured["deploy_obs_empty"] is False
    assert captured["now"] is now


def test_stale_live_process_surface_target_sha_ignores_deploy_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    captured: dict[str, Any] = {}

    def _fake_detect(**kw: Any) -> tuple[StaleLiveProcess | None, str]:
        captured.update(kw)
        return None, "no_divergence"

    monkeypatch.setattr(_DETECT_PATH, _fake_detect)
    surfaces = [
        _main_ci_surface(sha="b" * 40),
        _deploy_surface(sha="c" * 40),
    ]
    _stale_live_process_surface(surfaces, now)
    assert captured["target_sha"] == "b" * 40


def test_stale_live_process_surface_deploy_obs_empty_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(_SERVED_SHA_PATH, lambda: "a" * 40)
    result = _stale_live_process_surface(
        [_main_ci_surface(), _deploy_surface(empty=True)], now
    )
    assert result is None


def test_triage_run_swallows_stale_live_process_exception_other_surfaces_intact(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state

    def _boom(*_a: object, **_kw: object) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(triage_mod, "_stale_live_process_surface", _boom)

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

    ws = create_workspace(
        "Failure Triage Sweep", "d", root=ws_root, team=triage_mod._TEAM_NAME  # noqa: SLF001
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team=triage_mod._TEAM_NAME, tasks=list(ROLE_SPECS)  # noqa: SLF001
    )
    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, retention_capacity_findings=_findings
    )
    handover = team.run(ws, None, lambda event: None, inv_id=inv_id)

    assert handover.from_team == "triage"
    state = load_health_state(queue)
    assert state is not None
    assert not any(f.kind == "stale_live_process" for f in state.findings)
    assert any(f.kind == "retention_near_cap" for f in state.findings)


def test_triage_run_folds_in_stale_live_process_finding(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.health import load_health_state

    stale_surface = ClassifiedSurface(
        surface_id=f"stale_live_process:{'b' * 12}",
        kind="stale_live_process",
        is_blocked=True,
        blocker_reason="hot_paths",
        obs={"served_sha": "a" * 40, "target_sha": "b" * 40, "hot_paths": ["x.py"]},
        fp_obs={"served_sha": "a" * 40, "target_sha": "b" * 40},
    )
    monkeypatch.setattr(
        triage_mod, "_stale_live_process_surface", lambda surfaces, now: stale_surface
    )

    def _findings(root: Path | None) -> list[ClassifiedSurface]:
        del root
        return []

    ws = create_workspace(
        "Failure Triage Sweep", "d", root=ws_root, team=triage_mod._TEAM_NAME  # noqa: SLF001
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team=triage_mod._TEAM_NAME, tasks=list(ROLE_SPECS)  # noqa: SLF001
    )
    team = TriageTeam(
        queue=queue, workspaces_root=ws_root, retention_capacity_findings=_findings
    )
    handover = team.run(ws, None, lambda event: None, inv_id=inv_id)

    assert handover.from_team == "triage"
    state = load_health_state(queue)
    assert state is not None
    assert any(f.kind == "stale_live_process" for f in state.findings)

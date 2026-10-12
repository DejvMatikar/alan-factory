from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dep_sweep import group_proposals
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    DependencySweepConfig,
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    RepoIdentity,
    register,
)
from alan.forge import JsonObject
from alan.queue import Queue
from alan.teams import dep_advisory
from alan.teams._base import TeamEvent
from alan.teams._dep_advisory_audit import AuditResult
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams.dependency_sweep import (
    ROLE_SPEC,
    DependencySweepStatus,
    DependencySweepTeam,
    SweepContext,
    _fetch_open_proposals,
    _fetch_pr_files,
    _run_sweep,
    _status_path,  # noqa: SLF001
    _to_proposals,
    load_dependency_sweep_status,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read

_OTHER_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
    credential_env_name="SPROCKET_GH_TOKEN",  # noqa: S106
    authoritative_checks=frozenset(),
)
_ALAN_SWEEP_CTX = SweepContext(forge=DEFAULT_FORGE_TARGET, run_vuln_scan=True)


def _noop_emit(event: TeamEvent) -> None:
    del event


def _make_queue(tmp_path: Path) -> Queue:
    return Queue(db_path=tmp_path / "queue.db")


def _register_sprocket_engine(*, cadence_seconds: int = 3600, enabled: bool = True) -> None:
    register(EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
            credential_env_name="SPROCKET_GH_TOKEN",  # noqa: S106
        ),
        dependency_sweep=DependencySweepConfig(
            enabled=enabled,
            cadence_seconds=cadence_seconds,
            enable_python_vuln_scan=False,
        ),
    ), validate_env=False)


def _seed_dep_sweep_ws(
    tmp_path: Path, queue: Queue
) -> tuple[workspace_mod.Workspace, str]:
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ROLE_SPEC], source_task_id=ws.task_id
    )
    return ws, inv_id


def test_pre_migration_json_without_receipt_fields_still_validates() -> None:
    legacy_json = (
        '{"slug": "sprocket-co/sprocket", "enabled": true, '
        '"last_run_at": "2026-08-17T00:00:00+00:00", '
        '"next_run_at": "2026-08-24T00:00:00+00:00"}'
    )
    status = DependencySweepStatus.model_validate_json(legacy_json)
    assert status.slug == "sprocket-co/sprocket"
    assert status.enabled is True
    assert status.last_outcome is None
    assert status.last_duration_seconds is None


def test_new_status_fields_default_to_none() -> None:
    status = DependencySweepStatus(slug="sprocket-co/sprocket", enabled=True)
    assert status.last_outcome is None
    assert status.last_duration_seconds is None


def test_do_schedule_next_sweep_records_outcome_and_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws, duration_seconds=12.5)  # noqa: SLF001

    status = load_dependency_sweep_status(_OTHER_FORGE)
    assert status is not None
    assert status.last_outcome == "success"
    assert status.last_duration_seconds == 12.5


def test_do_schedule_next_sweep_without_duration_leaves_it_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001 — mirrors direct pinned-test call style

    status = load_dependency_sweep_status(_OTHER_FORGE)
    assert status is not None
    assert status.last_outcome == "success"
    assert status.last_duration_seconds is None


def test_status_path_json_after_schedule_round_trips_receipt_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws, duration_seconds=3.25)  # noqa: SLF001

    raw = _status_path(_OTHER_FORGE).read_text(encoding="utf-8")
    assert '"last_outcome":"success"' in raw
    assert '"last_duration_seconds":3.25' in raw


def test_run_end_to_end_persists_measured_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_dependency_sweep_status(_OTHER_FORGE)
    assert status is not None
    assert status.last_outcome == "success"
    assert status.last_duration_seconds is not None
    assert status.last_duration_seconds >= 0.0


def test_run_sweep_raises_records_failure_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    def _boom(*_a: object, **_kw: object) -> list[dict[str, object]]:
        raise RuntimeError("sweep failure")

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", _boom),
        pytest.raises(RuntimeError, match="sweep failure"),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    status = load_dependency_sweep_status(_OTHER_FORGE)
    assert status is not None
    assert status.last_outcome == "failure"
    assert status.last_duration_seconds is not None
    assert status.last_duration_seconds >= 0.0


def test_run_disabled_engine_does_not_write_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine(enabled=False)
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert load_dependency_sweep_status(_OTHER_FORGE) is None


def test_schedule_next_sweep_not_wired_never_raises(tmp_path: Path) -> None:
    ws = workspace_mod.create("Dependency Sweep", "d", team="dependency_sweep", root=tmp_path)
    team = DependencySweepTeam()
    team._schedule_next_sweep(ws, duration_seconds=1.0)  # noqa: SLF001 — must not raise


def test_monotonic_duration_used_not_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    calls = {"n": 0}
    real_monotonic = time.monotonic

    def _fake_monotonic() -> float:
        calls["n"] += 1
        return real_monotonic()

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
        patch("alan.teams.dependency_sweep.time.monotonic", _fake_monotonic),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert calls["n"] == 2


_LISTING: tuple[JsonObject, ...] = (
    {"number": 1, "mergeable": "MERGEABLE", "title": "Bump requests from 1 to 2"},
    {"number": 2, "mergeable": "MERGEABLE", "title": "Bump fastapi from 1 to 2"},
    {"number": 3, "mergeable": "MERGEABLE", "title": "Bump httpx from 1 to 2"},
)
_UV_LOCK_FILES: JsonObject = {"files": [{"path": "uv.lock"}]}


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _quiet_advisory_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        dep_advisory, "audit_locked_dependencies", lambda _forge: AuditResult("ok")
    )


def test_listing_asks_the_port_for_the_dependency_bots_open_proposals(
    forge: ForgeScript,
) -> None:
    forge.queue("open_prs_by_author", _LISTING)
    ctx = SweepContext(forge=_OTHER_FORGE, run_vuln_scan=False)

    assert _fetch_open_proposals(ctx=ctx) == _LISTING

    assert forge.calls("open_prs_by_author") == [
        (
            ("app/dependabot", ("number", "mergeable", "title")),
            {"forge_target": _OTHER_FORGE, "limit": 100},
        ),
    ]


def test_files_are_read_through_the_single_proposal_read_with_the_adapters_timeout(
    forge: ForgeScript,
) -> None:
    forge.queue("read_pr", pr_read(_UV_LOCK_FILES))
    ctx = SweepContext(forge=_OTHER_FORGE, run_vuln_scan=False)

    assert _fetch_pr_files(41, ctx=ctx) == ["uv.lock"]

    assert forge.calls("read_pr") == [
        ((41, ("files",)), {"forge_target": _OTHER_FORGE, "timeout": GH_TIMEOUT_S}),
    ]


def test_files_read_is_empty_when_the_files_field_is_absent(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"headRefName": "dependabot/uv/x"}))
    assert _fetch_pr_files(41, ctx=_ALAN_SWEEP_CTX) == []


def test_a_failed_files_read_leaves_the_proposal_with_no_lockfiles(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({}, returncode=1, stderr="HTTP 403"))
    (proposal,) = _to_proposals(_LISTING[:1], GH_TIMEOUT_S, ctx=_ALAN_SWEEP_CTX)
    assert proposal.lockfiles == frozenset()


def test_a_proposal_with_no_lockfiles_is_batched_alone_while_the_others_share_a_batch(
    forge: ForgeScript,
) -> None:
    forge.queue(
        "read_pr",
        pr_read(_UV_LOCK_FILES),
        pr_read({}, returncode=1, stderr="HTTP 403"),
        pr_read(_UV_LOCK_FILES),
    )
    batches = group_proposals(_to_proposals(_LISTING, GH_TIMEOUT_S, ctx=_ALAN_SWEEP_CTX))
    assert [batch.proposal_numbers for batch in batches] == [(1, 3), (2,)]


def test_sweep_dispatches_the_unreadable_proposal_alone_and_batches_the_readable_pair(
    tmp_path: Path, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
    forge.queue("open_prs_by_author", _LISTING)
    forge.queue(
        "read_pr",
        pr_read(_UV_LOCK_FILES),
        pr_read({}, returncode=1, stderr="HTTP 403"),
        pr_read(_UV_LOCK_FILES),
    )
    audit: list[dict[str, object]] = []
    _run_sweep(_make_queue(tmp_path), tmp_path, audit, ctx=_ALAN_SWEEP_CTX)
    dispatched = [entry["pr_number"] for entry in audit if entry["action"] == "dispatch"]
    assert dispatched == [1, 2]


def test_run_end_to_end_audit_row_carries_the_weekly_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    with patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "dep_sweep_audit.jsonl").read_text().splitlines()
    ]
    assert [row["trigger"] for row in rows if row["action"] == "sweep_complete"] == ["weekly"]

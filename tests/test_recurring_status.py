from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from alan import engine_config as engine_config_mod
from alan import recurring_forge, recurring_status, repo_status
from alan import workspace as workspace_mod
from alan.digest_sweep import DIGEST_LAST_DATE_META_KEY
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity
from alan.events import PubSub
from alan.health import resolve_digest_deadline
from alan.queue import Queue
from alan.repo_status import Provenance
from alan.teams import _triage_sweep_status
from alan.teams.dependency_sweep import (
    DependencySweepStatus,
    _save_dependency_sweep_status,  # noqa: SLF001
)
from alan.teams.dependency_sweep import (
    _status_path as _dep_sweep_status_path,  # noqa: SLF001
)
from alan.workspace_index import ARCHIVED_ROWS_META_KEY, WorkspaceIndex

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105


def _engine(owner: str = "acme", repo: str = "widget", *, with_forge: bool = True) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    forge = (
        ForgeConfig(
            repo=identity, credential_env_name=_FORGE_CREDENTIAL, authoritative_checks=frozenset()
        )
        if with_forge
        else None
    )
    return EngineConfig(repo=identity, forge=forge)


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def workspace_index(tmp_path: Path, queue: Queue) -> WorkspaceIndex:
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return WorkspaceIndex(root=root, queue=queue)


@pytest.fixture(autouse=True)
def _reset_inflight() -> Iterator[None]:
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001
    yield
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001


_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _provenance(
    status: Literal["fresh", "stale", "refreshing", "unknown"] = "fresh", detail: str = "x"
) -> Provenance:
    return Provenance(status=status, detail=detail)


@pytest.mark.parametrize(
    ("status", "conclusion", "confirmed_absent", "expected"),
    [
        (None, None, True, "never_ran"),
        (None, None, False, "unknown"),
        ("queued", None, True, "queued"),
        ("waiting", None, True, "queued"),
        ("requested", None, True, "queued"),
        ("pending", None, True, "queued"),
        ("in_progress", None, True, "in_progress"),
        ("completed", "success", True, "succeeded"),
        ("completed", "failure", True, "failed"),
        ("completed", "timed_out", True, "failed"),
        ("completed", "startup_failure", True, "failed"),
        ("completed", "action_required", True, "attention"),
        ("completed", "neutral", True, "cancelled"),
        ("completed", "cancelled", True, "cancelled"),
        ("completed", "skipped", True, "cancelled"),
        ("completed", "stale", True, "cancelled"),
        ("completed", "bogus-conclusion", True, "unknown"),
        ("completed", None, True, "unknown"),
        ("bogus-status", None, True, "unknown"),
    ],
)
def test_forge_run_state(
    status: str | None, conclusion: str | None, confirmed_absent: bool, expected: str
) -> None:
    assert (
        recurring_status._forge_run_state(status, conclusion, confirmed_absent=confirmed_absent)
        == expected
    )  # noqa: SLF001


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", "succeeded"),
        ("degraded", "attention"),
        ("failure", "failed"),
        (None, "unknown"),
        ("bogus-outcome", "unknown"),
    ],
)
def test_sweep_run_state(outcome: str | None, expected: str) -> None:
    assert recurring_status._sweep_run_state(outcome) == expected  # noqa: SLF001


def test_tightest_cron_empty() -> None:
    assert recurring_status._tightest_cron(()) == (None, None)  # noqa: SLF001


def test_tightest_cron_all_unparseable_returns_first() -> None:
    result = recurring_status._tightest_cron(("bogus", "also bogus"))  # noqa: SLF001
    assert result == (None, "bogus")


def test_tightest_cron_picks_shortest_interval() -> None:
    result = recurring_status._tightest_cron(("0 2 * * *", "*/30 * * * *"))  # noqa: SLF001
    assert result == (1800.0, "*/30 * * * *")


def test_process_from_workflow_manual() -> None:
    wf = recurring_forge.WorkflowSchedule(
        workflow_id=1,
        name="Manual Job",
        manual=True,
        last_run_provenance=_provenance("unknown"),
        last_run_confirmed_absent=True,
    )
    proc = recurring_status._process_from_workflow(wf)  # noqa: SLF001
    assert proc.cadence.kind == "manual"
    assert proc.duration_state == "unknown"
    assert proc.last_run.state == "never_ran"
    assert proc.last_run.url is None


def test_process_from_workflow_scheduled_measured_duration() -> None:
    wf = recurring_forge.WorkflowSchedule(
        workflow_id=2,
        name="Nightly",
        manual=False,
        cron_entries=("0 2 * * *",),
        last_run_provenance=_provenance(),
        last_run_at="2025-12-31T02:00:00+00:00",
        last_run_succeeded=True,
        last_run_outcome="success",
        last_run_duration_seconds=42.0,
        last_run_status="completed",
        last_run_conclusion="success",
        last_run_url="https://github.com/acme/widget/actions/runs/1",
    )
    proc = recurring_status._process_from_workflow(wf)  # noqa: SLF001
    assert proc.cadence.kind == "cron"
    assert proc.cadence.interval_seconds == pytest.approx(86400.0)
    assert proc.duration_state == "measured"
    assert proc.duration_seconds == pytest.approx(42.0)
    assert proc.last_run.state == "succeeded"
    assert proc.last_run.url == "https://github.com/acme/widget/actions/runs/1"


def test_process_from_workflow_unparseable_cron() -> None:
    wf = recurring_forge.WorkflowSchedule(
        workflow_id=3, name="Weird", manual=False, cron_entries=("bogus",),
        last_run_provenance=_provenance(),
    )
    proc = recurring_status._process_from_workflow(wf)  # noqa: SLF001
    assert proc.cadence.interval_seconds is None
    assert "could not be parsed" in proc.cadence.detail


def test_process_from_dependency_update_known_interval() -> None:
    du = recurring_forge.DependencyEcosystemUpdate(
        ecosystem="pip",
        schedule_interval="weekly",
        last_pr_provenance=_provenance(),
        last_pr_url="https://github.com/acme/widget/pull/1",
    )
    proc = recurring_status._process_from_dependency_update(du)  # noqa: SLF001
    assert proc.cadence.interval_seconds == pytest.approx(604800.0)
    assert proc.duration_state == "not_applicable"
    assert proc.last_run.state == "unknown"
    assert proc.last_run.url == "https://github.com/acme/widget/pull/1"


def test_process_from_dependency_update_no_interval_declared() -> None:
    du = recurring_forge.DependencyEcosystemUpdate(
        ecosystem="npm", schedule_interval=None, last_pr_provenance=_provenance()
    )
    proc = recurring_status._process_from_dependency_update(du)  # noqa: SLF001
    assert proc.cadence.interval_seconds is None
    assert "did not declare" in proc.cadence.detail


def test_processes_from_forge_workflows_present() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="x",
        workflows=(
            recurring_forge.WorkflowSchedule(
                workflow_id=1, name="A", manual=True, last_run_provenance=_provenance("unknown")
            ),
        ),
        workflows_provenance=_provenance(),
        dependency_updates_provenance=_provenance(),
    )
    processes = recurring_status._processes_from_forge(status)  # noqa: SLF001
    assert [p.process_id for p in processes] == ["scheduled_ci:1"]


def test_processes_from_forge_empty_and_fresh_yields_no_placeholder() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="x",
        workflows_provenance=_provenance("fresh", "no workflows declared"),
        dependency_updates_provenance=_provenance("fresh", "no dependabot.yml"),
    )
    assert recurring_status._processes_from_forge(status) == []  # noqa: SLF001


def test_processes_from_forge_unknown_yields_placeholder_for_both_families() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="x",
        workflows_provenance=_provenance("unknown", "no credential"),
        dependency_updates_provenance=_provenance("unknown", "no credential"),
    )
    processes = recurring_status._processes_from_forge(status)  # noqa: SLF001
    assert {p.process_id for p in processes} == {"scheduled_ci", "dependency_update"}
    by_id = {p.process_id: p for p in processes}
    assert by_id["scheduled_ci"].cadence.kind == "cron"
    assert by_id["dependency_update"].cadence.kind == "interval_seconds"
    assert by_id["scheduled_ci"].last_run.state == "unknown"
    assert by_id["dependency_update"].last_run.state == "unknown"


def test_processes_from_forge_dependency_updates_present() -> None:
    status = recurring_forge.RecurringForgeStatus(
        fetched_at="x",
        workflows_provenance=_provenance(),
        dependency_updates=(
            recurring_forge.DependencyEcosystemUpdate(
                ecosystem="pip", last_pr_provenance=_provenance()
            ),
        ),
        dependency_updates_provenance=_provenance(),
    )
    processes = recurring_status._processes_from_forge(status)  # noqa: SLF001
    assert [p.process_id for p in processes] == ["dependency_update:pip"]


def test_digest_process_no_digest_yet(queue: Queue) -> None:
    proc = recurring_status._digest_process(queue=queue, now=_NOW)  # noqa: SLF001
    assert proc.last_run.provenance.status == "unknown"
    assert proc.cadence.kind == "rides_on"


def test_digest_process_overdue_when_deadline_passed_without_digest(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue.set_meta(DIGEST_LAST_DATE_META_KEY, "2025-01-01")
    now = _NOW.replace(hour=23)
    proc = recurring_status._digest_process(queue=queue, now=now)  # noqa: SLF001
    assert proc.overdue.state == "overdue"


def test_digest_process_on_time_when_digest_ran_today(queue: Queue) -> None:
    deadline = resolve_digest_deadline(_NOW)
    queue.set_meta(DIGEST_LAST_DATE_META_KEY, deadline.date().isoformat())
    proc = recurring_status._digest_process(queue=queue, now=_NOW.replace(hour=23))  # noqa: SLF001
    assert proc.overdue.state == "on_time"


def test_digest_process_reads_latest_digest(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDigest:
        generated_at = "2026-01-01T00:00:00+00:00"

    monkeypatch.setattr(recurring_status, "load_latest_digest", lambda q: _FakeDigest())
    proc = recurring_status._digest_process(queue=queue, now=_NOW)  # noqa: SLF001
    assert proc.last_run.at == "2026-01-01T00:00:00+00:00"
    assert proc.last_run.provenance.status == "fresh"


def test_triage_process_no_status_yet(queue: Queue, state_root: Path) -> None:
    proc = recurring_status._triage_process(queue=queue, now=_NOW)  # noqa: SLF001
    assert proc.last_run.provenance.status == "unknown"
    assert proc.last_run.state == "unknown"
    assert proc.duration_state == "unknown"
    assert proc.sub_steps[0].process_id == "daily_digest"


@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [("success", "succeeded"), ("degraded", "attention"), ("failure", "failed")],
)
def test_triage_process_reads_status(
    queue: Queue, state_root: Path, outcome: str, expected_state: str
) -> None:
    _triage_sweep_status._write_triage_sweep_status(outcome, 12.5)  # noqa: SLF001
    proc = recurring_status._triage_process(queue=queue, now=_NOW)  # noqa: SLF001
    assert proc.last_run.outcome == outcome
    assert proc.last_run.state == expected_state
    assert proc.duration_state == "measured"
    assert proc.duration_seconds == pytest.approx(12.5)


def _forge() -> Any:
    from alan.engine_config import ForgeTarget

    return ForgeTarget(
        repo=RepoIdentity(owner="acme", repo="widget"),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=frozenset(),
    )


def test_dependency_sweep_process_disabled() -> None:
    cfg = _engine()
    proc = recurring_status._dependency_sweep_process(cfg, _forge())  # noqa: SLF001
    assert proc.last_run.provenance.status == "unknown"
    assert proc.last_run.state == "unknown"
    assert "disabled" in proc.last_run.provenance.detail


def test_dependency_sweep_process_enabled_no_status_yet(state_root: Path) -> None:
    cfg = _engine().model_copy(
        update={
            "dependency_sweep": engine_config_mod.DependencySweepConfig(
                enabled=True, cadence_seconds=3600
            )
        }
    )
    proc = recurring_status._dependency_sweep_process(cfg, _forge())  # noqa: SLF001
    assert proc.last_run.provenance.status == "unknown"
    assert proc.last_run.state == "unknown"


def test_dependency_sweep_process_enabled_with_status(state_root: Path) -> None:
    cfg = _engine().model_copy(
        update={
            "dependency_sweep": engine_config_mod.DependencySweepConfig(
                enabled=True, cadence_seconds=3600
            )
        }
    )
    forge = _forge()
    _save_dependency_sweep_status(
        forge,
        DependencySweepStatus(
            slug=forge.repo.slug,
            enabled=True,
            last_run_at="2026-01-01T00:00:00+00:00",
            last_outcome="success",
            last_duration_seconds=5.0,
        ),
    )
    proc = recurring_status._dependency_sweep_process(cfg, forge)  # noqa: SLF001
    assert proc.duration_state == "measured"
    assert proc.last_run.state == "succeeded"


def test_dependency_sweep_process_enabled_status_missing_outcome_and_duration(
    state_root: Path,
) -> None:
    cfg = _engine().model_copy(
        update={
            "dependency_sweep": engine_config_mod.DependencySweepConfig(
                enabled=True, cadence_seconds=3600
            )
        }
    )
    forge = _forge()
    _save_dependency_sweep_status(
        forge,
        DependencySweepStatus(
            slug=forge.repo.slug, enabled=True, last_run_at="2026-01-01T00:00:00+00:00"
        ),
    )
    proc = recurring_status._dependency_sweep_process(cfg, forge)  # noqa: SLF001
    assert proc.last_run.state == "unknown"
    assert proc.duration_state == "unknown"


def test_dep_sweep_status_path_is_reachable(state_root: Path) -> None:
    assert str(state_root) in str(_dep_sweep_status_path(_forge()))


def _write_backup_receipt(
    state_root: Path, *, outcome: str, finished_at: str, duration: float = 1.0
) -> None:
    path = state_root / "backup_receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "outcome": outcome,
            "started_at": finished_at,
            "finished_at": finished_at,
            "duration_seconds": duration,
        }),
        encoding="utf-8",
    )


def test_load_backup_receipt_absent(state_root: Path) -> None:
    assert recurring_status._load_backup_receipt() is None  # noqa: SLF001


def test_load_backup_receipt_corrupt(state_root: Path) -> None:
    path = state_root / "backup_receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert recurring_status._load_backup_receipt() is None  # noqa: SLF001


def test_backup_job_process_no_receipt(state_root: Path) -> None:
    proc = recurring_status._backup_job_process()  # noqa: SLF001
    assert proc.last_run.provenance.status == "unknown"
    assert proc.last_run.state == "unknown"


def test_backup_job_process_with_receipt(state_root: Path) -> None:
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    proc = recurring_status._backup_job_process()  # noqa: SLF001
    assert proc.duration_state == "measured"
    assert proc.last_run.state == "succeeded"


def test_backup_coverage_no_receipt(state_root: Path, workspace_index: WorkspaceIndex) -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=workspace_index)  # noqa: SLF001
    assert coverage.provenance.status == "unknown"
    assert "structurally excluded" in coverage.excluded_caveat


def test_backup_coverage_failed_run(state_root: Path, workspace_index: WorkspaceIndex) -> None:
    _write_backup_receipt(state_root, outcome="failure", finished_at="2026-01-01T00:00:00+00:00")
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=workspace_index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is False


def test_backup_coverage_unparseable_finished_at(
    state_root: Path, workspace_index: WorkspaceIndex
) -> None:
    _write_backup_receipt(state_root, outcome="success", finished_at="not-a-timestamp")
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=workspace_index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count is None


def test_backup_coverage_counts_changed_workspaces(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine(), default=True, validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    workspace_mod.create("changed-since", "d", root=root, engine_key="acme/widget")
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count == 1


def test_backup_coverage_excludes_other_repos_changed_workspaces(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine(), default=True, validate_env=False)
    engine_config_mod.register(_engine("acme", "other"), validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    workspace_mod.create("changed-since", "d", root=root, engine_key="acme/widget")
    workspace_mod.create("changed-since-other", "d", root=root, engine_key="acme/other")
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count == 1


def _strip_tzinfo(iso_value: str) -> str:
    return datetime.fromisoformat(iso_value).replace(tzinfo=None).isoformat()


def test_backup_coverage_counts_workspace_with_naive_meta_updated_at(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine(), default=True, validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    ws = workspace_mod.create("changed-since-naive", "d", root=root, engine_key="acme/widget")
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = _strip_tzinfo(raw["updated_at"])
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count == 1


def test_backup_coverage_counts_archived_row_with_naive_updated_at(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine(), default=True, validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    ws = workspace_mod.create("archived-naive", "d", root=root, engine_key="acme/widget")
    index = WorkspaceIndex(root=root, queue=queue)
    index.rows()
    workspace_mod.transition(ws, workspace_mod.Status.ARCHIVED)
    index.rows()
    index.backfill_archived()

    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    blob["rows"][ws.task_id]["updated_at"] = _strip_tzinfo(blob["rows"][ws.task_id]["updated_at"])
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh_index = WorkspaceIndex(root=root, queue=queue)
    fresh_index.rebuild()
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=fresh_index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count == 1


def test_backup_coverage_zero_changed_workspaces_returns_zero_not_none(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine(), default=True, validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2030-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    workspace_mod.create("not-changed-since", "d", root=root, engine_key="acme/widget")
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    coverage = recurring_status._backup_coverage(identity, workspace_index=index)  # noqa: SLF001
    assert coverage.included_in_last_snapshot is True
    assert coverage.changed_since_count == 0


def test_backup_coverage_non_default_repo_unaffected_by_naive_timestamp(
    tmp_path: Path, state_root: Path, projects_root: Path, queue: Queue
) -> None:
    engine_config_mod.register(_engine("acme", "default-repo"), default=True, validate_env=False)
    engine_config_mod.register(_engine("acme", "widget"), default=False, validate_env=False)
    _write_backup_receipt(state_root, outcome="success", finished_at="2026-01-01T00:00:00+00:00")
    root = tmp_path / "workspaces"
    ws = workspace_mod.create("non-default-naive", "d", root=root, engine_key="acme/widget")
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = _strip_tzinfo(raw["updated_at"])
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    result = recurring_status.get_recurring_status(
        identity, queue=queue, workspace_index=index, pubsub=PubSub()
    )
    assert result is not None
    assert result.is_default is False
    assert result.backup_coverage.included_in_last_snapshot is True
    assert result.backup_coverage.changed_since_count == 1


def test_finalize_overdue_skips_rides_on_but_recurses_into_sub_steps() -> None:
    digest = recurring_status.RecurringProcess(
        process_id="daily_digest",
        family="system_sweep",
        label="Daily digest",
        cadence=recurring_status.Cadence(kind="rides_on", rides_on="triage_sweep", detail="x"),
        last_run=recurring_status.LastRun(provenance=_provenance("unknown")),
        duration_state="not_applicable",
        overdue=recurring_status.Overdue(state="on_time", detail="pre-set"),
    )
    triage = recurring_status.RecurringProcess(
        process_id="triage_sweep",
        family="system_sweep",
        label="Failure triage sweep",
        cadence=recurring_status.Cadence(
            kind="interval_seconds", interval_seconds=1800.0, detail="x"
        ),
        last_run=recurring_status.LastRun(
            provenance=_provenance(), at=(_NOW - timedelta(seconds=7200)).isoformat()
        ),
        duration_state="measured",
        duration_seconds=1.0,
        overdue=recurring_status._placeholder_overdue(),  # noqa: SLF001
        sub_steps=(digest,),
    )
    finalized = recurring_status._finalize_overdue(triage, now=_NOW)  # noqa: SLF001
    assert finalized.overdue.state == "overdue"
    assert finalized.sub_steps[0].overdue.state == "on_time"


def test_get_recurring_status_returns_none_for_unknown_repo(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path
) -> None:
    identity = RepoIdentity(owner="nope", repo="nowhere")
    result = recurring_status.get_recurring_status(
        identity, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is None


def test_get_recurring_status_forge_less_engine_has_no_forge_or_sweep_processes(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path, projects_root: Path
) -> None:
    cfg = _engine(with_forge=False)
    engine_config_mod.register(cfg, default=False, validate_env=False)
    result = recurring_status.get_recurring_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    assert result.processes == []
    assert result.service_scope_note == recurring_status._SERVICE_SCOPE_NOTE  # noqa: SLF001


def test_get_recurring_status_non_default_engine_gets_scope_note_not_system_sweeps(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path, projects_root: Path
) -> None:
    cfg = _engine()
    engine_config_mod.register(cfg, default=False, validate_env=False)
    result = recurring_status.get_recurring_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    assert result.is_default is False
    assert result.service_scope_note is not None
    process_ids = {p.process_id for p in result.processes}
    assert "triage_sweep" not in process_ids
    assert "backup_job" not in process_ids
    assert "dependency_sweep" in process_ids


def test_get_recurring_status_default_engine_includes_system_sweeps(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path, projects_root: Path
) -> None:
    cfg = _engine()
    engine_config_mod.register(cfg, default=True, validate_env=False)
    result = recurring_status.get_recurring_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    assert result.is_default is True
    assert result.service_scope_note is None
    process_ids = {p.process_id for p in result.processes}
    assert {"triage_sweep", "backup_job", "dependency_sweep"} <= process_ids
    assert result.backup_coverage is not None


def test_get_recurring_status_cache_miss_yields_refreshing_placeholders(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path, projects_root: Path
) -> None:
    cfg = _engine()
    engine_config_mod.register(cfg, default=True, validate_env=False)
    result = recurring_status.get_recurring_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    scheduled = next(p for p in result.processes if p.process_id == "scheduled_ci")
    assert scheduled.last_run.provenance.status == "refreshing"
    assert scheduled.last_run.state == "unknown"


def test_get_recurring_status_uses_cached_forge_data(
    queue: Queue, workspace_index: WorkspaceIndex, state_root: Path, projects_root: Path
) -> None:
    cfg = _engine()
    engine_config_mod.register(cfg, default=True, validate_env=False)
    forge = _forge()
    cached = recurring_forge.RecurringForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        workflows=(
            recurring_forge.WorkflowSchedule(
                workflow_id=1, name="Nightly", manual=False, cron_entries=("0 2 * * *",),
                last_run_provenance=_provenance(),
            ),
        ),
        workflows_provenance=_provenance(),
        dependency_updates_provenance=_provenance("fresh", "no dependabot.yml"),
    )
    recurring_forge._write_forge_cache(forge, cached)  # noqa: SLF001
    result = recurring_status.get_recurring_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    process_ids = {p.process_id for p in result.processes}
    assert "scheduled_ci:1" in process_ids

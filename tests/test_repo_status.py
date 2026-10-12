from __future__ import annotations

import json
import math
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import delivery_stats as delivery_stats_mod
from alan import engine_config as engine_config_mod
from alan import health as health_mod
from alan import process_runner, repo_status, secrets_store
from alan import project as project_mod
from alan import workspace as workspace_mod
from alan import workspace_index as workspace_index_mod
from alan import workspace_size as size_class_mod
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget, RepoIdentity
from alan.events import PubSub
from alan.forge import RowsOutcome, RowsRead
from alan.forge_gh import GhForge
from alan.queue import Queue
from alan.teams import _pr_audit_log
from alan.teams._base import TeamEvent
from alan.teams.deploy_verify import (
    _AUDIT_FILE as _DEPLOY_AUDIT_FILE,  # pyright: ignore[reportPrivateUsage]
)
from alan.workflow import WorkflowDoc, WorkflowTask, save_workflow
from alan.workspace_index import WorkspaceIndex
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, rows_read

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105
_WORKFLOW = "tests.yml"


def _forge(
    owner: str = "acme", repo: str = "widget", *, authoritative: frozenset[str] = frozenset()
) -> ForgeTarget:
    return ForgeTarget(
        repo=RepoIdentity(owner=owner, repo=repo),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=authoritative,
    )


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


def _stamp_cost(ws: workspace_mod.Workspace, *, cost: float) -> None:
    doc = WorkflowDoc(
        id="inv_001",
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="success",
        started_at=datetime.now(UTC),
        tasks=[
            WorkflowTask(
                id="worker", role="worker", model="sonnet", status="success",
                total_cost_usd=cost,
            )
        ],
    )
    save_workflow(ws, doc)


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


@pytest.fixture
def fake(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


@pytest.fixture(autouse=True)
def _reset_inflight() -> Iterator[None]:
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001
    yield
    repo_status._INFLIGHT_SLUGS.clear()  # noqa: SLF001


def test_read_forge_cache_returns_none_when_absent(state_root: Path) -> None:
    assert repo_status._read_forge_cache(_forge()) is None  # noqa: SLF001


def test_write_then_read_forge_cache_roundtrips(state_root: Path) -> None:
    forge = _forge()
    fresh = repo_status.Provenance(status="fresh", detail="x")
    status = repo_status.ForgeStatus(
        fetched_at="2026-01-01T00:00:00+00:00",
        main_ci=repo_status.MainCiField(provenance=fresh),
        open_prs=repo_status.OpenPrsField(provenance=fresh),
    )
    repo_status._write_forge_cache(forge, status)  # noqa: SLF001
    loaded = repo_status._read_forge_cache(forge)  # noqa: SLF001
    assert loaded == status


def test_read_forge_cache_returns_none_on_corrupt_json(state_root: Path) -> None:
    path = repo_status._forge_cache_path(_forge())  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert repo_status._read_forge_cache(_forge()) is None  # noqa: SLF001


def test_write_forge_cache_swallows_oserror(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    fresh = repo_status.Provenance(status="fresh", detail="x")
    status = repo_status.ForgeStatus(
        fetched_at="2026-01-01T00:00:00+00:00",
        main_ci=repo_status.MainCiField(provenance=fresh),
        open_prs=repo_status.OpenPrsField(provenance=fresh),
    )

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _raise)
    repo_status._write_forge_cache(forge, status)  # noqa: SLF001


def test_cache_age_s_returns_inf_for_unparseable_timestamp() -> None:
    fresh = repo_status.Provenance(status="fresh", detail="x")
    status = repo_status.ForgeStatus(
        fetched_at="not-a-timestamp",
        main_ci=repo_status.MainCiField(provenance=fresh),
        open_prs=repo_status.OpenPrsField(provenance=fresh),
    )
    assert repo_status._cache_age_s(status) == math.inf  # noqa: SLF001


def test_cache_age_s_returns_inf_for_naive_timestamp() -> None:
    fresh = repo_status.Provenance(status="fresh", detail="x")
    status = repo_status.ForgeStatus(
        fetched_at="2026-01-01T00:00:00",
        main_ci=repo_status.MainCiField(provenance=fresh),
        open_prs=repo_status.OpenPrsField(provenance=fresh),
    )
    assert repo_status._cache_age_s(status) == math.inf  # noqa: SLF001


def test_cache_age_s_returns_finite_seconds_for_valid_timestamp() -> None:
    fresh = repo_status.Provenance(status="fresh", detail="x")
    status = repo_status.ForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        main_ci=repo_status.MainCiField(provenance=fresh),
        open_prs=repo_status.OpenPrsField(provenance=fresh),
    )
    assert repo_status._cache_age_s(status) < 1.0  # noqa: SLF001


def test_mark_stale_downgrades_fresh_to_stale() -> None:
    field = repo_status.MainCiField(
        provenance=repo_status.Provenance(status="fresh", detail="x"), green_count=1
    )
    marked = repo_status._mark_stale(field)  # noqa: SLF001
    assert marked.provenance.status == "stale"
    assert marked.green_count == 1


def test_mark_stale_leaves_non_fresh_field_untouched() -> None:
    unknown = repo_status.Provenance(status="unknown", detail="x")
    field = repo_status.OpenPrsField(provenance=unknown)
    marked = repo_status._mark_stale(field)  # noqa: SLF001
    assert marked is field


def test_begin_refresh_coalesces_duplicate_inflight() -> None:
    assert repo_status.begin_refresh("acme/widget") is True
    assert repo_status.begin_refresh("acme/widget") is False
    repo_status.end_refresh("acme/widget")
    assert repo_status.begin_refresh("acme/widget") is True


class _CapturingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def submit(self, fn: Any, *args: Any) -> None:
        self.calls.append((fn, *args))


def test_schedule_refresh_coalesces_duplicate_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _CapturingExecutor()
    monkeypatch.setattr(repo_status, "REFRESH_EXECUTOR", executor)
    forge = _forge()
    pubsub = PubSub()
    assert repo_status.schedule_refresh(forge, pubsub, main_ci_workflow=None) is True
    assert repo_status.schedule_refresh(forge, pubsub, main_ci_workflow=None) is False
    assert len(executor.calls) == 1
    assert executor.calls[0][0] is repo_status._do_refresh_and_publish  # noqa: SLF001


def test_do_refresh_and_publish_writes_cache_publishes_and_clears_inflight(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    bus = PubSub()
    events: list[TeamEvent] = []
    bus.subscribe_all(events.append)
    assert repo_status.begin_refresh(forge.repo.slug) is True
    repo_status._do_refresh_and_publish(forge, bus, None)  # noqa: SLF001
    assert len(events) == 1
    assert events[0].kind == "repo_status_refreshed"
    assert events[0].payload == {"slug": "acme/widget"}
    assert repo_status._read_forge_cache(forge) is not None  # noqa: SLF001
    assert repo_status.begin_refresh(forge.repo.slug) is True


def _stub_open_prs(fake: FakeForge, total: RowsRead, mine: RowsRead) -> None:
    always(fake, "open_prs", total)
    always(fake, "prs_by_author", mine)


def _adapter_backed(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch, op: str, stdout: str
) -> None:
    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", _run)
    fake.stub(op, getattr(GhForge(), op))


def test_fetch_open_prs_reads_both_listings_through_the_port_with_the_thirty_second_bound(
    fake: FakeForge,
) -> None:
    _stub_open_prs(fake, rows_read([{"number": 1}]), rows_read([{"number": 1}]))
    forge = _forge()
    repo_status._fetch_open_prs(forge)  # noqa: SLF001
    assert fake.calls == [
        ("open_prs", (), {"forge_target": forge, "limit": 100, "timeout": 30.0}),
        (
            "prs_by_author", ("@me", ("number",)),
            {"forge_target": forge, "state": "open", "limit": 100, "timeout": 30.0},
        ),
    ]


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_fetch_open_prs_unknown_when_total_call_fails(
    fake: FakeForge, outcome: RowsOutcome
) -> None:
    _stub_open_prs(fake, rows_read(None, outcome=outcome, returncode=1), rows_read([]))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "gh pr list failed"
    assert (field.total, field.authored_by_alan) == (None, None)
    assert [name for name, _args, _kwargs in fake.calls] == ["open_prs"]


def test_fetch_open_prs_unknown_never_zero_when_total_call_succeeds_without_parseable_rows(
    fake: FakeForge,
) -> None:
    _stub_open_prs(fake, rows_read(None), rows_read([]))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "gh pr list failed"
    assert field.total is None


@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_fetch_open_prs_unknown_when_mine_call_fails(fake: FakeForge, outcome: RowsOutcome) -> None:
    _stub_open_prs(
        fake, rows_read([{"number": 1}]), rows_read(None, outcome=outcome, returncode=1)
    )
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "gh pr list --author @me failed"
    assert (field.total, field.authored_by_alan) == (None, None)


def test_fetch_open_prs_unknown_never_zero_when_mine_call_succeeds_without_parseable_rows(
    fake: FakeForge,
) -> None:
    _stub_open_prs(fake, rows_read([{"number": 1}]), rows_read(None))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.provenance.detail == "gh pr list --author @me failed"
    assert field.authored_by_alan is None


def test_fetch_open_prs_reports_zero_for_empty_successful_listings(fake: FakeForge) -> None:
    _stub_open_prs(fake, rows_read([]), rows_read([]))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance == repo_status.Provenance(status="fresh", detail="from gh pr list")
    assert (field.total, field.authored_by_alan) == (0, 0)


def test_fetch_open_prs_success(fake: FakeForge) -> None:
    _stub_open_prs(fake, rows_read([{"number": 1}, {"number": 2}]), rows_read([{"number": 1}]))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.provenance == repo_status.Provenance(status="fresh", detail="from gh pr list")
    assert field.total == 2
    assert field.authored_by_alan == 1


def test_fetch_open_prs_no_longer_counts_non_object_rows_from_the_adapter(
    fake: FakeForge, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adapter_backed(fake, monkeypatch, "open_prs", '[1, "two", {"number": 3}]')
    always(fake, "prs_by_author", rows_read([]))
    field = repo_status._fetch_open_prs(_forge())  # noqa: SLF001
    assert field.total == 1


def test_refresh_forge_status_unknown_when_no_credential_and_no_forge_operation_reached(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    status = repo_status.refresh_forge_status(_forge(), main_ci_workflow=_WORKFLOW)
    assert status.main_ci.provenance == repo_status.Provenance(
        status="unknown", detail="no credential available for this repo's forge"
    )
    assert status.open_prs.provenance == status.main_ci.provenance
    assert fake.calls == []


def test_refresh_forge_status_treats_a_whitespace_only_secret_as_no_credential(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "   \n")  # noqa: ARG005
    status = repo_status.refresh_forge_status(_forge(), main_ci_workflow=None)
    assert status.main_ci.provenance.detail == "no credential available for this repo's forge"
    assert status.open_prs.provenance.detail == "no credential available for this repo's forge"
    assert fake.calls == []


def test_refresh_forge_status_success_delegates_to_fetch_helpers(
    monkeypatch: pytest.MonkeyPatch, fake: FakeForge
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    always(fake, "default_branch", "main")
    always(fake, "commit_check_runs", rows_read([]))
    _stub_open_prs(fake, rows_read([]), rows_read([]))
    status = repo_status.refresh_forge_status(_forge(), main_ci_workflow=None)
    assert status.main_ci.provenance.status == "fresh"
    assert status.open_prs.provenance.status == "fresh"
    assert status.open_prs.total == 0


def _recording_schedule_refresh(
    calls: list[ForgeTarget],
) -> Any:
    def _fake(forge: ForgeTarget, pubsub: PubSub, *, main_ci_workflow: str | None) -> bool:
        del pubsub, main_ci_workflow
        calls.append(forge)
        return True

    return _fake


def test_forge_fields_cache_miss_schedules_refresh_and_marks_refreshing(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(repo_status, "schedule_refresh", _recording_schedule_refresh(calls))
    main_ci, open_prs = repo_status._forge_fields(forge, pubsub=PubSub(), main_ci_workflow=None)  # noqa: SLF001
    assert main_ci.provenance.status == "refreshing"
    assert open_prs.provenance.status == "refreshing"
    assert calls == [forge]


def test_forge_fields_stale_cache_marks_stale_and_schedules_refresh(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    fresh = repo_status.Provenance(status="fresh", detail="x")
    old_status = repo_status.ForgeStatus(
        fetched_at=(
            datetime.now(UTC) - timedelta(seconds=repo_status.FORGE_STATUS_TTL_S + 1)
        ).isoformat(),
        main_ci=repo_status.MainCiField(provenance=fresh, green_count=1, red_count=0),
        open_prs=repo_status.OpenPrsField(provenance=fresh, total=2, authored_by_alan=1),
    )
    repo_status._write_forge_cache(forge, old_status)  # noqa: SLF001
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(repo_status, "schedule_refresh", _recording_schedule_refresh(calls))
    main_ci, open_prs = repo_status._forge_fields(forge, pubsub=PubSub(), main_ci_workflow=None)  # noqa: SLF001
    assert main_ci.provenance.status == "stale"
    assert main_ci.green_count == 1
    assert open_prs.provenance.status == "stale"
    assert calls == [forge]


def test_forge_fields_fresh_cache_served_without_scheduling_refresh(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge = _forge()
    fresh = repo_status.Provenance(status="fresh", detail="x")
    recent_status = repo_status.ForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        main_ci=repo_status.MainCiField(provenance=fresh, green_count=3, red_count=0),
        open_prs=repo_status.OpenPrsField(provenance=fresh, total=5, authored_by_alan=2),
    )
    repo_status._write_forge_cache(forge, recent_status)  # noqa: SLF001
    calls: list[ForgeTarget] = []
    monkeypatch.setattr(repo_status, "schedule_refresh", _recording_schedule_refresh(calls))
    main_ci, open_prs = repo_status._forge_fields(forge, pubsub=PubSub(), main_ci_workflow=None)  # noqa: SLF001
    assert main_ci.provenance.status == "fresh"
    assert main_ci.green_count == 3
    assert calls == []


def test_in_repo_scope_matches_project_id_in_scope_set() -> None:
    ts = datetime.now(UTC)
    row = workspace_index_mod.WorkspaceRow(
        task_id="a", title="t", status=workspace_mod.Status.ACTIVE, created_at=ts, updated_at=ts,
        project_id="proj-1",
    )
    assert delivery_stats_mod.in_repo_scope(row, "acme/widget", {"proj-1"}) is True
    assert delivery_stats_mod.in_repo_scope(row, "acme/widget", set()) is False


def test_in_repo_scope_unassigned_row_matches_engine_key() -> None:
    engine_config_mod.register(_engine("acme", "widget"), default=True, validate_env=False)
    engine_config_mod.register(_engine("other", "repo"), validate_env=False)
    ts = datetime.now(UTC)
    row = workspace_index_mod.WorkspaceRow(
        task_id="a", title="t", status=workspace_mod.Status.ACTIVE, created_at=ts, updated_at=ts,
        project_id=None, engine_key="acme/widget",
    )
    assert delivery_stats_mod.in_repo_scope(row, "acme/widget", set()) is True
    assert delivery_stats_mod.in_repo_scope(row, "other/repo", set()) is False


def test_in_repo_scope_unassigned_row_null_engine_key_folds_to_default_scope() -> None:
    engine_config_mod.register(_engine("other", "repo"), validate_env=False)
    ts = datetime.now(UTC)
    row = workspace_index_mod.WorkspaceRow(
        task_id="a", title="t", status=workspace_mod.Status.ACTIVE, created_at=ts, updated_at=ts,
        project_id=None, engine_key=None,
    )
    default_slug = engine_config_mod.default_engine_config().repo.slug
    assert delivery_stats_mod.in_repo_scope(row, default_slug, set()) is True
    assert delivery_stats_mod.in_repo_scope(row, "other/repo", set()) is False


def test_scoped_rows_returns_validated_rows_and_this_repos_project_ids(
    tmp_path: Path, queue: Queue, projects_root: Path
) -> None:
    engine_config_mod.register(_engine("acme", "widget"), default=True, validate_env=False)
    engine_config_mod.register(_engine("other", "repo"), validate_env=False)
    project = project_mod.create("Acme Widget", root=projects_root)
    project = project_mod.update(project, {"engine_key": "acme/widget"}, root=projects_root)
    other_project = project_mod.create("Other", root=projects_root)
    project_mod.update(other_project, {"engine_key": "other/repo"}, root=projects_root)
    root = tmp_path / "workspaces"
    live = workspace_mod.create("live", "d", root=root, engine_key="acme/widget")
    archived_ws = workspace_mod.create("archived", "d", root=root, engine_key="acme/widget")
    workspace_mod.transition(archived_ws, workspace_mod.Status.ARCHIVED)
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    assert {r.task_id for r in rows} == {live.task_id}
    assert {r.task_id for r in archived} == {archived_ws.task_id}
    assert project_ids == {project.id}


def test_cost_field_no_scoped_rows_is_none_not_zero(
    tmp_path: Path, queue: Queue, projects_root: Path
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    field = repo_status._cost_field(rows, archived, project_ids, identity.slug)  # noqa: SLF001
    assert field.total_cost_usd is None
    assert field.unknown_count == 0


def test_cost_field_sums_project_and_unassigned_costs_across_live_and_archived(
    tmp_path: Path, queue: Queue, projects_root: Path
) -> None:
    project = project_mod.create("Acme Widget", root=projects_root)
    project = project_mod.update(project, {"engine_key": "acme/widget"}, root=projects_root)
    root = tmp_path / "workspaces"
    ws_project = workspace_mod.create(
        "proj-ws", "d", root=root, engine_key="acme/widget", project_id=project.id
    )
    _stamp_cost(ws_project, cost=15.0)
    ws_unassigned = workspace_mod.create(
        "loose-ws", "d", root=root, engine_key="acme/widget", project_id=None
    )
    _stamp_cost(ws_unassigned, cost=5.0)
    ws_archived = workspace_mod.create(
        "archived-ws", "d", root=root, engine_key="acme/widget", project_id=None
    )
    _stamp_cost(ws_archived, cost=2.0)
    workspace_mod.transition(ws_archived, workspace_mod.Status.ARCHIVED)
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    field = repo_status._cost_field(rows, archived, project_ids, identity.slug)  # noqa: SLF001
    assert field.total_cost_usd == pytest.approx(22.0)
    assert field.unknown_count == 0
    assert "all six model-spend categories" in field.provenance.detail


def test_cost_field_excludes_other_repos_costs_and_tracks_unknowns_as_a_floor(
    tmp_path: Path, queue: Queue, projects_root: Path
) -> None:
    engine_config_mod.register(_engine("acme", "widget"), default=True, validate_env=False)
    engine_config_mod.register(_engine("other", "repo"), validate_env=False)
    root = tmp_path / "workspaces"
    ws_mine = workspace_mod.create("mine", "d", root=root, engine_key="acme/widget")
    workspace_mod.create("untouched", "d", root=root, engine_key="acme/widget")
    workspace_mod.create("other", "d", root=root, engine_key="other/repo")
    _stamp_cost(ws_mine, cost=4.0)
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    field = repo_status._cost_field(rows, archived, project_ids, identity.slug)  # noqa: SLF001
    assert field.total_cost_usd == pytest.approx(4.0)
    assert field.unknown_count == 1


def test_delivery_time_field_has_five_entries_in_size_order_and_empty_state(
    tmp_path: Path, queue: Queue, projects_root: Path
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    index = WorkspaceIndex(root=root, queue=queue)
    identity = RepoIdentity(owner="acme", repo="widget")
    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    field = repo_status._delivery_time_field(  # noqa: SLF001
        rows, archived, project_ids, identity.slug
    )
    assert [stat.size for stat in field.by_size] == list(size_class_mod.SizeClass)
    for stat in field.by_size:
        assert stat.median_active_seconds is None
        assert stat.delivered_count == 0


def test_repo_status_has_no_local_in_repo_scope_refork() -> None:
    assert not hasattr(repo_status, "_in_repo_scope")


def test_repo_scope_predicate_shared_across_all_four_folds(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    ts = datetime.now(UTC)
    project = project_mod.Project(
        id="p1", title="P", engine_key=identity.slug, created_at=ts, updated_at=ts
    )
    monkeypatch.setattr(project_mod, "list_projects", lambda **_kw: [project])
    delivered_row = workspace_index_mod.WorkspaceRow(
        task_id="delivered", title="t", status=workspace_mod.Status.ARCHIVED,
        created_at=ts, updated_at=ts, project_id=None, engine_key=identity.slug,
        scope_ref="pr:1", active_closed_seconds=60.0,
        size_class=size_class_mod.SizeClass.M, total_cost_usd=3.0,
    )
    index = WorkspaceIndex(root=tmp_path / "workspaces", queue=queue)
    monkeypatch.setattr(index, "validated_rows", lambda: ([], [delivered_row]))
    state = health_mod.HealthState(
        verdict="degraded", blocked_count=0, in_flight_count=0,
        findings=[_finding(identity.slug)], computed_at=ts.isoformat(),
    )
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: state)

    canonicalised: list[str | None] = []
    original_canonical_scope_key = engine_config_mod.canonical_scope_key

    def _spy(engine_key: str | None) -> RepoIdentity:
        canonicalised.append(engine_key)
        return original_canonical_scope_key(engine_key)

    monkeypatch.setattr(engine_config_mod, "canonical_scope_key", _spy)

    rows, archived, project_ids = repo_status._scoped_rows(identity, index)  # noqa: SLF001
    cost = repo_status._cost_field(rows, archived, project_ids, identity.slug)  # noqa: SLF001
    delivery = repo_status._delivery_time_field(  # noqa: SLF001
        rows, archived, project_ids, identity.slug
    )
    health = repo_status._health_field(identity, queue=queue, is_default=False)  # noqa: SLF001

    assert project_ids == {"p1"}
    assert cost.total_cost_usd == pytest.approx(3.0)
    assert sum(s.delivered_count for s in delivery.by_size) == 1
    assert health.finding_count == 1

    assert project.engine_key in canonicalised
    assert delivered_row.engine_key in canonicalised
    assert _finding(identity.slug).engine_key in canonicalised
    assert canonicalised.count(identity.slug) == 8


def test_contradiction_fixture_header_and_strip_membership_agree_across_engines() -> None:
    ts = datetime.now(UTC)
    default_slug = engine_config_mod.default_engine_config().repo.slug
    other_cfg = _engine("other-co", "other-repo", with_forge=False)
    engine_config_mod.register(other_cfg, default=False, validate_env=False)
    other_slug = other_cfg.repo.slug

    projects = [
        project_mod.Project(id="p-none", title="P", engine_key=None, created_at=ts, updated_at=ts),
        project_mod.Project(
            id="p-default", title="P", engine_key=default_slug, created_at=ts, updated_at=ts
        ),
        project_mod.Project(
            id="p-other", title="P", engine_key=other_slug, created_at=ts, updated_at=ts
        ),
    ]

    def _delivered_row(
        task_id: str, *, project_id: str | None, engine_key: str | None
    ) -> workspace_index_mod.WorkspaceRow:
        return workspace_index_mod.WorkspaceRow(
            task_id=task_id, title="t", status=workspace_mod.Status.ARCHIVED,
            created_at=ts, updated_at=ts, project_id=project_id, engine_key=engine_key,
            scope_ref=f"pr:{task_id}", active_closed_seconds=60.0,
            size_class=size_class_mod.SizeClass.M, total_cost_usd=1.0,
        )

    rows = [
        _delivered_row("bound-none", project_id="p-none", engine_key=None),
        _delivered_row("bound-default", project_id="p-default", engine_key=None),
        _delivered_row("bound-other", project_id="p-other", engine_key=None),
        _delivered_row("loose-none", project_id=None, engine_key=None),
        _delivered_row("loose-default", project_id=None, engine_key=default_slug),
        _delivered_row("loose-other", project_id=None, engine_key=other_slug),
        _delivered_row("loose-ghost", project_id=None, engine_key="ghost-co/ghost-repo"),
    ]

    for identity, expected_task_ids in (
        (
            RepoIdentity.from_slug(default_slug),
            {"bound-none", "bound-default", "loose-none", "loose-default", "loose-ghost"},
        ),
        (
            RepoIdentity.from_slug(other_slug),
            {"bound-other", "loose-other"},
        ),
    ):
        project_ids = {
            p.id
            for p in projects
            if engine_config_mod.same_repo_scope(p.engine_key, identity.slug)
        }
        cost_field = repo_status._cost_field(rows, [], project_ids, identity.slug)  # noqa: SLF001
        delivery_field = repo_status._delivery_time_field(  # noqa: SLF001
            rows, [], project_ids, identity.slug
        )
        assert cost_field.total_cost_usd == pytest.approx(len(expected_task_ids) * 1.0)
        delivered_total = sum(stat.delivered_count for stat in delivery_field.by_size)
        assert delivered_total == len(expected_task_ids)


def test_tail_relevant_audit_entry_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert repo_status._tail_relevant_audit_entry(tmp_path / "missing.jsonl") is None  # noqa: SLF001


def test_tail_relevant_audit_entry_skips_blank_malformed_and_non_dict_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"action": "deploy_confirmed", "pr_number": 7, '
        '"timestamp_utc": "2026-01-01T00:00:00+00:00"}\n'
        "[1, 2, 3]\n"
        "not json\n"
        '{"action": "irrelevant_thing"}\n'
        "\n",
        encoding="utf-8",
    )
    entry = repo_status._tail_relevant_audit_entry(path)  # noqa: SLF001
    assert entry is not None
    assert entry["pr_number"] == 7


def test_tail_relevant_audit_entry_returns_none_when_no_relevant_action(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text('{"action": "something_else"}\n', encoding="utf-8")
    assert repo_status._tail_relevant_audit_entry(path) is None  # noqa: SLF001


def test_tail_relevant_audit_entry_returns_the_last_matching_entry(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"action": "deploy_confirmed", "pr_number": 1}\n'
        '{"action": "escalate_no_merged_at", "pr_number": 2}\n',
        encoding="utf-8",
    )
    entry = repo_status._tail_relevant_audit_entry(path)  # noqa: SLF001
    assert entry is not None
    assert entry["pr_number"] == 2


def test_last_deploy_field_unknown_for_non_default_engine() -> None:
    field = repo_status._last_deploy_field(is_default=False)  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.outcome is None


def test_last_deploy_field_unknown_when_no_audit_entries() -> None:
    field = repo_status._last_deploy_field(is_default=True)  # noqa: SLF001
    assert field.provenance.status == "unknown"


def test_last_deploy_field_confirmed_from_latest_entry() -> None:
    audit_path = _pr_audit_log.audit_dir() / _DEPLOY_AUDIT_FILE
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({
            "action": "deploy_confirmed",
            "pr_number": 9,
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        })
        + "\n",
        encoding="utf-8",
    )
    field = repo_status._last_deploy_field(is_default=True)  # noqa: SLF001
    assert field.outcome == "confirmed"
    assert field.pr_number == 9
    assert field.at == "2026-01-01T00:00:00+00:00"


def test_last_deploy_field_escalate_outcome() -> None:
    audit_path = _pr_audit_log.audit_dir() / _DEPLOY_AUDIT_FILE
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({"action": "escalate_no_merged_at", "pr_number": 3}) + "\n", encoding="utf-8"
    )
    field = repo_status._last_deploy_field(is_default=True)  # noqa: SLF001
    assert field.outcome == "escalate"
    assert field.pr_number == 3
    assert field.at is None


def test_last_deploy_field_handles_missing_pr_number_and_timestamp() -> None:
    audit_path = _pr_audit_log.audit_dir() / _DEPLOY_AUDIT_FILE
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps({"action": "deploy_confirmed"}) + "\n", encoding="utf-8")
    field = repo_status._last_deploy_field(is_default=True)  # noqa: SLF001
    assert field.pr_number is None
    assert field.at is None


def test_health_field_unknown_when_state_absent(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: None)
    identity = RepoIdentity(owner="acme", repo="widget")
    field = repo_status._health_field(identity, queue=queue, is_default=True)  # noqa: SLF001
    assert field.provenance.status == "unknown"
    assert field.finding_count is None


def test_health_field_unknown_when_state_stale(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    stale = health_mod.HealthState(
        verdict="healthy",
        blocked_count=0,
        in_flight_count=0,
        findings=[],
        computed_at="2000-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: stale)
    identity = RepoIdentity(owner="acme", repo="widget")
    field = repo_status._health_field(identity, queue=queue, is_default=True)  # noqa: SLF001
    assert field.provenance.status == "unknown"


def _finding(engine_key: str | None) -> health_mod.HealthFinding:
    return health_mod.HealthFinding(
        finding_id=f"f-{engine_key}",
        kind="stuck",
        severity="high",
        description="d",
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
        engine_key=engine_key,
    )


def test_health_field_counts_only_matching_engine_findings_and_hides_verdict(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=False, validate_env=False)
    identity = cfg.repo
    state = health_mod.HealthState(
        verdict="degraded",
        blocked_count=2,
        in_flight_count=0,
        findings=[_finding("acme/widget"), _finding("other/repo")],
        computed_at=datetime.now(UTC).isoformat(),
    )
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: state)
    field = repo_status._health_field(identity, queue=queue, is_default=False)  # noqa: SLF001
    assert field.finding_count == 1
    assert field.verdict is None


def test_health_field_returns_verdict_only_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=True, validate_env=False)
    identity = cfg.repo
    state = health_mod.HealthState(
        verdict="degraded",
        blocked_count=1,
        in_flight_count=0,
        findings=[_finding("acme/widget")],
        computed_at=datetime.now(UTC).isoformat(),
    )
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: state)
    field = repo_status._health_field(identity, queue=queue, is_default=True)  # noqa: SLF001
    assert field.finding_count == 1
    assert field.verdict == "degraded"


def test_health_field_counts_engine_key_none_finding_as_the_default_engine(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=True, validate_env=False)
    engine_config_mod.register(_engine("other", "repo"), validate_env=False)
    identity = cfg.repo
    state = health_mod.HealthState(
        verdict="degraded", blocked_count=1, in_flight_count=0,
        findings=[_finding(None), _finding("other/repo")],
        computed_at=datetime.now(UTC).isoformat(),
    )
    monkeypatch.setattr(repo_status, "load_health_state", lambda q: state)
    field = repo_status._health_field(identity, queue=queue, is_default=True)  # noqa: SLF001
    assert field.finding_count == 1


def test_resolve_engine_found_and_is_default() -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=True, validate_env=False)
    resolved = repo_status._resolve_engine(cfg.repo)  # noqa: SLF001
    assert resolved == (cfg, True)


def test_resolve_engine_found_but_a_different_engine_is_default() -> None:
    default_cfg = _engine("default-owner", "default-repo")
    engine_config_mod.register(default_cfg, default=True, validate_env=False)
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=False, validate_env=False)
    resolved = repo_status._resolve_engine(cfg.repo)  # noqa: SLF001
    assert resolved == (cfg, False)


def test_resolve_engine_found_with_no_default_registered_at_all() -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=False, validate_env=False)
    resolved = repo_status._resolve_engine(cfg.repo)  # noqa: SLF001
    assert resolved == (cfg, False)


def test_resolve_engine_falls_back_to_hardcoded_default_for_fresh_install() -> None:
    default_repo = engine_config_mod.default_engine_config().repo
    resolved = repo_status._resolve_engine(default_repo)  # noqa: SLF001
    assert resolved is not None
    cfg, is_default = resolved
    assert is_default is True
    assert cfg.repo == default_repo


def test_resolve_engine_none_when_nothing_registered_and_identity_is_not_default() -> None:
    identity = RepoIdentity(owner="nope", repo="nowhere")
    assert repo_status._resolve_engine(identity) is None  # noqa: SLF001


def test_resolve_engine_none_when_a_different_default_is_registered() -> None:
    default_cfg = _engine("default-owner", "default-repo")
    engine_config_mod.register(default_cfg, default=True, validate_env=False)
    identity = RepoIdentity(owner="nope", repo="nowhere")
    assert repo_status._resolve_engine(identity) is None  # noqa: SLF001


def test_get_repo_status_returns_none_for_unknown_repo(
    queue: Queue, workspace_index: WorkspaceIndex
) -> None:
    identity = RepoIdentity(owner="nope", repo="nowhere")
    result = repo_status.get_repo_status(
        identity, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is None


def test_get_repo_status_all_unknown_when_forge_less(
    queue: Queue, workspace_index: WorkspaceIndex
) -> None:
    cfg = _engine("acme", "widget", with_forge=False)
    engine_config_mod.register(cfg, default=True, validate_env=False)
    result = repo_status.get_repo_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    assert result.is_default is True
    fields = (
        result.main_ci,
        result.open_prs,
        result.cost,
        result.delivery_time,
        result.last_deploy,
        result.health,
    )
    for field in fields:
        assert field.provenance.status == "unknown"
    assert result.delivery_time.by_size == []


def test_get_repo_status_wires_all_fields_together(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, workspace_index: WorkspaceIndex
) -> None:
    cfg = _engine("acme", "widget")
    engine_config_mod.register(cfg, default=False, validate_env=False)
    sentinel_main_ci = repo_status.MainCiField(
        provenance=repo_status.Provenance(status="fresh", detail="m")
    )
    sentinel_open_prs = repo_status.OpenPrsField(
        provenance=repo_status.Provenance(status="fresh", detail="o")
    )
    sentinel_cost = repo_status.CostField(
        provenance=repo_status.Provenance(status="fresh", detail="c")
    )
    sentinel_delivery_time = repo_status.DeliveryTimeField(
        provenance=repo_status.Provenance(status="fresh", detail="t"), by_size=[]
    )
    sentinel_deploy = repo_status.LastDeployField(
        provenance=repo_status.Provenance(status="fresh", detail="d")
    )
    sentinel_health = repo_status.HealthField(
        provenance=repo_status.Provenance(status="fresh", detail="h")
    )
    monkeypatch.setattr(
        repo_status,
        "_forge_fields",
        lambda forge, *, pubsub, main_ci_workflow: (  # noqa: ARG005
            sentinel_main_ci,
            sentinel_open_prs,
        ),
    )
    monkeypatch.setattr(
        repo_status, "_scoped_rows", lambda identity, workspace_index: ([], [], set())  # noqa: ARG005
    )
    monkeypatch.setattr(
        repo_status,
        "_cost_field",
        lambda rows, archived, project_ids, slug: sentinel_cost,  # noqa: ARG005
    )
    monkeypatch.setattr(
        repo_status,
        "_delivery_time_field",
        lambda rows, archived, project_ids, slug: sentinel_delivery_time,  # noqa: ARG005
    )
    monkeypatch.setattr(
        repo_status, "_last_deploy_field", lambda *, is_default: sentinel_deploy  # noqa: ARG005
    )
    monkeypatch.setattr(
        repo_status,
        "_health_field",
        lambda identity, *, queue, is_default: sentinel_health,  # noqa: ARG005
    )
    result = repo_status.get_repo_status(
        cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=PubSub()
    )
    assert result is not None
    assert result.slug == "acme/widget"
    assert result.is_default is False
    assert result.main_ci is sentinel_main_ci
    assert result.open_prs is sentinel_open_prs
    assert result.cost is sentinel_cost
    assert result.delivery_time is sentinel_delivery_time
    assert result.last_deploy is sentinel_deploy
    assert result.health is sentinel_health


def test_forge_status_ttl_is_ten_minutes() -> None:
    assert repo_status.FORGE_STATUS_TTL_S == 600.0

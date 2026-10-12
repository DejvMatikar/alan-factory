from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alan import engine_config as engine_config_mod
from alan.delivery_stats import delivered_rows_for_repo, delivery_time_by_size
from alan.engine_config import EngineConfig, RepoIdentity
from alan.workspace import Status
from alan.workspace_index import WorkspaceRow
from alan.workspace_size import SizeClass

_TS = datetime.now(UTC)


def _row(
    task_id: str,
    *,
    status: Status = Status.ARCHIVED,
    project_id: str | None = None,
    engine_key: str | None = None,
    scope_ref: str | None = "pr:1",
    active_closed_seconds: float | None = 60.0,
    size_class: SizeClass | None = SizeClass.M,
) -> WorkspaceRow:
    return WorkspaceRow(
        task_id=task_id,
        title="t",
        status=status,
        created_at=_TS,
        updated_at=_TS,
        project_id=project_id,
        engine_key=engine_key,
        scope_ref=scope_ref,
        active_closed_seconds=active_closed_seconds,
        size_class=size_class,
    )


def test_delivered_rows_for_repo_includes_project_scoped_row() -> None:
    row = _row("a", project_id="proj-1")
    assert delivered_rows_for_repo([row], [], "acme/widget", {"proj-1"}) == [row]


def test_delivered_rows_for_repo_excludes_row_from_other_project() -> None:
    row = _row("a", project_id="other-proj")
    assert delivered_rows_for_repo([row], [], "acme/widget", {"proj-1"}) == []


def test_delivered_rows_for_repo_includes_unassigned_row_matching_engine_key() -> None:
    row = _row("a", project_id=None, engine_key="acme/widget")
    assert delivered_rows_for_repo([row], [], "acme/widget", set()) == [row]


def test_delivered_rows_for_repo_excludes_unassigned_row_with_other_engine_key() -> None:
    engine_config_mod.register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")),
        default=True,
        validate_env=False,
    )
    engine_config_mod.register(
        EngineConfig(repo=RepoIdentity(owner="other", repo="repo")), validate_env=False
    )
    row = _row("a", project_id=None, engine_key="other/repo")
    assert delivered_rows_for_repo([row], [], "acme/widget", set()) == []


def test_delivered_rows_for_repo_excludes_non_delivered_status() -> None:
    row = _row("a", project_id=None, engine_key="acme/widget", status=Status.ACTIVE)
    assert delivered_rows_for_repo([row], [], "acme/widget", set()) == []


def test_delivered_rows_for_repo_excludes_row_without_pr_scope_ref() -> None:
    row = _row("a", project_id=None, engine_key="acme/widget", scope_ref=None)
    assert delivered_rows_for_repo([row], [], "acme/widget", set()) == []


def test_delivered_rows_for_repo_excludes_row_without_active_closed_seconds() -> None:
    row = _row("a", project_id=None, engine_key="acme/widget", active_closed_seconds=None)
    assert delivered_rows_for_repo([row], [], "acme/widget", set()) == []


def test_delivered_rows_for_repo_scans_both_live_and_archived_lists() -> None:
    live = _row("live", project_id=None, engine_key="acme/widget", status=Status.COMPLETED)
    archived = _row("archived", project_id=None, engine_key="acme/widget", status=Status.ARCHIVED)
    result = delivered_rows_for_repo([live], [archived], "acme/widget", set())
    assert {r.task_id for r in result} == {"live", "archived"}


def test_delivery_time_by_size_returns_all_five_sizes() -> None:
    result = delivery_time_by_size([])
    assert set(result) == set(SizeClass)
    for median, count in result.values():
        assert median is None
        assert count == 0


def test_delivery_time_by_size_excludes_row_with_no_size_class() -> None:
    row = _row("a", size_class=None)
    result = delivery_time_by_size([row])
    assert result[SizeClass.M] == (None, 0)


def test_delivery_time_by_size_excludes_row_with_no_active_closed_seconds() -> None:
    row = _row("a", size_class=SizeClass.M, active_closed_seconds=None)
    result = delivery_time_by_size([row])
    assert result[SizeClass.M] == (None, 0)


def test_delivery_time_by_size_reports_median_and_count() -> None:
    rows = [
        _row("a", size_class=SizeClass.S, active_closed_seconds=10.0),
        _row("b", size_class=SizeClass.S, active_closed_seconds=20.0),
        _row("c", size_class=SizeClass.S, active_closed_seconds=90.0),
    ]
    result = delivery_time_by_size(rows)
    median, count = result[SizeClass.S]
    assert median == pytest.approx(20.0)
    assert count == 3


def test_delivery_time_by_size_is_robust_to_a_single_outlier() -> None:
    rows = [
        _row("a", size_class=SizeClass.L, active_closed_seconds=100.0),
        _row("b", size_class=SizeClass.L, active_closed_seconds=110.0),
        _row("c", size_class=SizeClass.L, active_closed_seconds=100_000.0),
    ]
    median, count = delivery_time_by_size(rows)[SizeClass.L]
    assert median == pytest.approx(110.0)
    assert count == 3

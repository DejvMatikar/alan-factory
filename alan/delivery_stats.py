from __future__ import annotations

import itertools
import statistics

from alan import engine_config
from alan.workspace_index import DELIVERED_STATUSES, WorkspaceRow
from alan.workspace_size import SizeClass


def in_repo_scope(
    row: WorkspaceRow,
    slug: str,
    project_ids: set[str],
    *,
    target: engine_config.RepoIdentity | None = None,
) -> bool:
    if row.project_id is not None:
        return row.project_id in project_ids
    if target is not None:
        return engine_config.canonical_scope_key(row.engine_key) == target
    return engine_config.same_repo_scope(row.engine_key, slug)


def _is_delivered_in_repo(
    row: WorkspaceRow,
    slug: str,
    project_ids: set[str],
    *,
    target: engine_config.RepoIdentity | None,
) -> bool:
    if not in_repo_scope(row, slug, project_ids, target=target):
        return False
    if row.status not in DELIVERED_STATUSES:
        return False
    if not row.resolved_scope_ref().startswith("pr:"):
        return False
    return row.active_closed_seconds is not None


def delivered_rows_for_repo(
    rows: list[WorkspaceRow],
    archived: list[WorkspaceRow],
    slug: str,
    project_ids: set[str],
) -> list[WorkspaceRow]:
    target = engine_config.canonical_scope_key(slug)
    return [
        row
        for row in itertools.chain(rows, archived)
        if _is_delivered_in_repo(row, slug, project_ids, target=target)
    ]


def delivery_time_by_size(
    rows: list[WorkspaceRow],
) -> dict[SizeClass, tuple[float | None, int]]:
    buckets: dict[SizeClass, list[float]] = {size: [] for size in SizeClass}
    for row in rows:
        if row.size_class is None:
            continue
        seconds = row.active_closed_seconds
        if seconds is None:
            continue
        buckets[row.size_class].append(seconds)
    return {
        size: (statistics.median(values) if values else None, len(values))
        for size, values in buckets.items()
    }

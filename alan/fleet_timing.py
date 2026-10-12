from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alan.workflow import (
    ActiveInterval,
    WorkflowRowSummary,
    list_invocation_ids,
    load_workflow_summary_cached,
    workflow_file,
)


@dataclass(frozen=True)
class ActiveFleetTime:
    closed_seconds: float | None
    open_since: datetime | None

    def live_seconds(self, now: datetime) -> float | None:
        if self.closed_seconds is None and self.open_since is None:
            return None
        total = self.closed_seconds or 0.0
        if self.open_since is not None:
            total += max(0.0, (now - self.open_since).total_seconds())
        return total


def _merge_intervals(intervals: list[ActiveInterval]) -> list[ActiveInterval]:
    ordered = sorted(intervals, key=lambda interval: interval[0])
    merged: list[ActiveInterval] = []
    for start, end in ordered:
        if not merged:
            merged.append((start, end))
            continue
        cur_start, cur_end = merged[-1]
        if cur_end is not None and start > cur_end:
            merged.append((start, end))
            continue
        merged[-1] = (cur_start, _combine_end(cur_end, end))
    return merged


def _combine_end(cur_end: datetime | None, end: datetime | None) -> datetime | None:
    if cur_end is None or end is None:
        return None
    return max(cur_end, end)


def _reduce_intervals(intervals: list[ActiveInterval]) -> ActiveFleetTime:
    merged = _merge_intervals(intervals)
    closed_total = 0.0
    has_closed = False
    open_since: datetime | None = None
    for start, end in merged:
        if end is None:
            open_since = start
        else:
            closed_total += (end - start).total_seconds()
            has_closed = True
    return ActiveFleetTime(
        closed_seconds=closed_total if has_closed else None,
        open_since=open_since,
    )


def active_fleet_time_for_summaries(
    summaries: Iterable[WorkflowRowSummary],
) -> ActiveFleetTime:
    intervals = [interval for summary in summaries for interval in summary.active_intervals]
    return _reduce_intervals(intervals)


def active_fleet_time_for_workspace(ws_path: Path) -> ActiveFleetTime:
    summaries = (
        load_workflow_summary_cached(workflow_file(ws_path, inv_id))
        for inv_id in list_invocation_ids(ws_path)
    )
    return active_fleet_time_for_summaries(summaries)


def lead_seconds(*, created_at: datetime, delivered_at: datetime | None) -> float | None:
    if delivered_at is None:
        return None
    return (delivered_at - created_at).total_seconds()

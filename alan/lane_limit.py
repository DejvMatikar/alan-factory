from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from alan.engine_config import RepoIdentity, canonical_scope_key
from alan.engine_registry_store import append_lane_limit_audit
from alan.queue import Queue, SeatJobFacts
from alan.workspace import Status, Workspace, list_workspaces

_LIMIT_META_PREFIX = "engine_lane_limit_v1:"
_IDLE_WINDOW = timedelta(days=7)
_TERMINAL_STATUSES = frozenset({Status.COMPLETED, Status.ARCHIVED})
_EMPTY_FACTS = SeatJobFacts(has_running=False, has_pending_pickable=False, last_activity=None)

_creation_lock = threading.Lock()

_log = logging.getLogger(__name__)


class LaneFullError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SeatHolder(BaseModel):
    task_id: str
    title: str
    status: Status


class LaneStatus(BaseModel):
    limit: int | None
    seats: int
    holders: list[SeatHolder]


def _limit_key(repo: RepoIdentity) -> str:
    return f"{_LIMIT_META_PREFIX}{repo.slug}"


def get_limit(queue: Queue, repo: RepoIdentity) -> int | None:
    raw = queue.get_meta(_limit_key(repo))
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        _log.warning("lane limit meta for %s has non-integer value %r", repo.slug, raw)
        return None
    if value < 1:
        _log.warning("lane limit meta for %s has out-of-range value %r", repo.slug, raw)
        return None
    return value


def set_limit(
    queue: Queue, repo: RepoIdentity, value: int | None, *, source: str
) -> int | None:
    current = get_limit(queue, repo)
    if value == current:
        return current
    key = _limit_key(repo)
    if value is None:
        queue.delete_meta(key)
    else:
        queue.set_meta(key, str(value))
    append_lane_limit_audit(repo.slug, old=current, new=value, source=source)
    return value


def holds_seat(
    status: Status, facts: SeatJobFacts, created_at: datetime, *, now: datetime
) -> bool:
    if status in _TERMINAL_STATUSES:
        return False
    if facts.has_running or facts.has_pending_pickable:
        return True
    last_activity = facts.last_activity
    effective = created_at if last_activity is None else max(last_activity, created_at)
    return now - effective < _IDLE_WINDOW


def _matching_workspaces(root: Path, repo: RepoIdentity) -> list[Workspace]:
    candidates = [
        ws
        for ws in list_workspaces(root=root)
        if canonical_scope_key(ws.meta.engine_key) == repo
    ]
    return sorted(candidates, key=lambda ws: (ws.meta.created_at, ws.task_id))


def seat_holders(
    queue: Queue, root: Path, repo: RepoIdentity, *, now: datetime | None = None
) -> list[SeatHolder]:
    _now = now if now is not None else datetime.now(UTC)
    candidates = _matching_workspaces(root, repo)
    facts_by_id = queue.seat_job_facts([ws.task_id for ws in candidates])
    return [
        SeatHolder(task_id=ws.task_id, title=ws.meta.title, status=ws.meta.status)
        for ws in candidates
        if holds_seat(
            ws.meta.status, facts_by_id.get(ws.task_id, _EMPTY_FACTS), ws.meta.created_at, now=_now
        )
    ]


def lane_status(
    queue: Queue, root: Path, repo: RepoIdentity, *, now: datetime | None = None
) -> LaneStatus:
    holders = seat_holders(queue, root, repo, now=now)
    return LaneStatus(limit=get_limit(queue, repo), seats=len(holders), holders=holders)


def _lane_full_detail(repo: RepoIdentity, limit: int, holders: list[SeatHolder]) -> str:
    holder_text = ", ".join(f"{h.task_id} ({h.status.value})" for h in holders)
    return f"lane full: {repo.slug} limit={limit} seats={len(holders)} holders={holder_text}"


def create_within_limit(
    queue: Queue,
    *,
    root: Path,
    engine_key: str | None,
    create: Callable[[], Workspace],
) -> Workspace:
    repo = canonical_scope_key(engine_key)
    limit = get_limit(queue, repo)
    if limit is None:
        return create()
    with _creation_lock:
        holders = seat_holders(queue, root, repo)
        if len(holders) >= limit:
            raise LaneFullError(_lane_full_detail(repo, limit, holders))
        return create()

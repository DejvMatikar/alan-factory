from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from alan import build_chat as build_chat_mod
from alan import explore_chat as explore_chat_mod
from alan import workspace as workspace_mod
from alan._chat_core import ChatSessionLocks, list_sessions
from alan.dispatcher import Dispatcher
from alan.engine_config import RepoIdentity, hide_and_persist
from alan.queue import JobStatus, Queue
from alan.slot_pool import PoolBusyError, PoolDisposeIncompleteError

_TERMINAL_STATUSES: frozenset[workspace_mod.Status] = frozenset({
    workspace_mod.Status.ARCHIVED,
    workspace_mod.Status.COMPLETED,
})


class QuiesceReport(BaseModel):
    slug: str
    residue_workspace_ids: list[str] = Field(default_factory=list)
    workers_interrupted: int = 0
    chats_cancelled: int = 0
    pool_disposed: bool = False
    pool_residue: str | None = None


def _references_repo(ws: workspace_mod.Workspace, repo: RepoIdentity) -> bool:
    if ws.meta.engine_key == repo.slug:
        return True
    snapshot = ws.meta.engine_snapshot
    return snapshot is not None and snapshot.config is not None and snapshot.config.repo == repo


def _non_terminal_workspaces_for_repo(
    repo: RepoIdentity, workspaces_root: Path | None
) -> list[workspace_mod.Workspace]:
    return [
        ws
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.status not in _TERMINAL_STATUSES and _references_repo(ws, repo)
    ]


def _interrupt_running_workers(
    workspaces: list[workspace_mod.Workspace], dispatcher: Dispatcher | None
) -> dict[str, bool]:
    if dispatcher is None:
        return dict.fromkeys((ws.task_id for ws in workspaces), False)
    return {ws.task_id: dispatcher.interrupt_task(ws.task_id) for ws in workspaces}


def _pause_future_jobs(workspaces: list[workspace_mod.Workspace], queue: Queue) -> None:
    for ws in workspaces:
        queue.pause_task(ws.task_id)


def _residue_workspace_ids(
    workspaces: list[workspace_mod.Workspace],
    interrupted: dict[str, bool],
    queue: Queue,
) -> list[str]:
    residue: list[str] = []
    for ws in workspaces:
        if interrupted[ws.task_id]:
            continue
        still_running = queue.list_jobs_for_workspace(ws.task_id, status=JobStatus.RUNNING)
        if still_running:
            residue.append(ws.task_id)
    return residue


def _cancel_chats(
    slug: str,
    *,
    build_chat_root: Path | None,
    build_chat_locks: ChatSessionLocks,
    explore_chat_root: Path | None,
    explore_chat_locks: ChatSessionLocks,
) -> int:
    build_cancelled = sum(
        1
        for summary in list_sessions(build_chat_mod.CONFIG, root=build_chat_root)
        if summary.engine_key == slug and build_chat_locks.cancel(summary.session_id)
    )
    explore_cancelled = sum(
        1
        for summary in list_sessions(explore_chat_mod.CONFIG, root=explore_chat_root)
        if summary.engine_key == slug and explore_chat_locks.cancel(summary.session_id)
    )
    return build_cancelled + explore_cancelled


def _dispose_pool(dispatcher: Dispatcher | None, slug: str) -> tuple[bool, str | None]:
    if dispatcher is None:
        return False, None
    try:
        dispatcher.dispose_pool(slug)
    except (PoolBusyError, PoolDisposeIncompleteError) as exc:
        return False, str(exc)
    return True, None


def quiesce_repo(
    repo: RepoIdentity,
    *,
    dispatcher: Dispatcher | None,
    queue: Queue,
    workspaces_root: Path | None,
    build_chat_root: Path | None,
    build_chat_locks: ChatSessionLocks,
    explore_chat_root: Path | None,
    explore_chat_locks: ChatSessionLocks,
) -> QuiesceReport:
    hide_and_persist(repo)
    workspaces = _non_terminal_workspaces_for_repo(repo, workspaces_root)
    interrupted = _interrupt_running_workers(workspaces, dispatcher)
    _pause_future_jobs(workspaces, queue)
    residue_workspace_ids = _residue_workspace_ids(workspaces, interrupted, queue)
    chats_cancelled = _cancel_chats(
        repo.slug,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )
    pool_disposed, pool_residue = _dispose_pool(dispatcher, repo.slug)
    return QuiesceReport(
        slug=repo.slug,
        residue_workspace_ids=residue_workspace_ids,
        workers_interrupted=sum(1 for v in interrupted.values() if v),
        chats_cancelled=chats_cancelled,
        pool_disposed=pool_disposed,
        pool_residue=pool_residue,
    )

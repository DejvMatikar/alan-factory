from __future__ import annotations

import subprocess
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path

from alan import worker_records
from alan import workflow as workflow_mod
from alan.slot_pool import SlotPool, SlotReservedError, get_active_pool
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import EmitFn, TeamEvent, node_turns_audit_dir
from alan.teams._slot_derived_data import per_slot_derived_data
from alan.workspace import Workspace
from alan.worktree import WorktreeHandle, implementation_worktree

WorktreeFactory = Callable[..., AbstractContextManager[WorktreeHandle]]
_GIT_TIMEOUT_S = 30.0
_ADOPTION_AUDIT_FILE = "slot_adoption_audit.jsonl"
_ANCESTOR_CHECK_ERROR_RC = -1


def _matches_survivor(record: worker_records.WorkerRecord, task_id: str, inv_id: str) -> bool:
    return record.task_id == task_id and record.inv_id == inv_id and record.pgid is not None


def _find_survivor_reservation(
    task_id: str, inv_id: str
) -> worker_records.WorkerRecord | None:
    matches = [
        record
        for _path, record in worker_records.load_all()
        if record is not None and _matches_survivor(record, task_id, inv_id)
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: r.started_at or 0.0)


def _git_ref(args: list[str], cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603,S607
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        timeout=_GIT_TIMEOUT_S, check=False,
    )
    return (result.stdout or "").strip() or "HEAD"


def _ancestor_check_rc(sha: str, cwd: Path) -> int:
    try:
        result = subprocess.run(  # noqa: S603,S607
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
            cwd=str(cwd), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S, check=False,
        )
    except subprocess.SubprocessError:
        return _ANCESTOR_CHECK_ERROR_RC
    return result.returncode


def _audit_adoption(
    record: worker_records.WorkerRecord,
    slot_path: Path,
    *,
    resolution: str,
    stored: str | None,
    ancestor_rc: int | None,
    resolved_base_ref: str,
) -> None:
    append_jsonl_audit(
        node_turns_audit_dir() / _ADOPTION_AUDIT_FILE,
        {
            "task_id": record.task_id,
            "inv_id": record.inv_id,
            "team": record.team,
            "slot_path": str(slot_path),
            "resolution": resolution,
            "stored_anchor": stored,
            "ancestor_rc": ancestor_rc,
            "resolved_base_ref": resolved_base_ref,
        },
        log_label="slot_adoption",
    )


def _adopted_base_ref(
    stored: str | None, slot_path: Path, record: worker_records.WorkerRecord
) -> tuple[str, bool]:
    head = _git_ref(["rev-parse", "HEAD"], slot_path)
    if stored is None:
        base_ref, resolution, rc = head, "no_stored_anchor", None
    else:
        rc = _ancestor_check_rc(stored, slot_path)
        if rc == 0:
            base_ref, resolution = stored, "stored_anchor"
        else:
            base_ref, resolution = head, "ancestor_check_failed"
    _audit_adoption(
        record, slot_path,
        resolution=resolution, stored=stored, ancestor_rc=rc, resolved_base_ref=base_ref,
    )
    return base_ref, resolution == "ancestor_check_failed"


@contextmanager
def _adopted_handle(
    record: worker_records.WorkerRecord, pool: SlotPool, resume_base_ref: str | None
) -> Generator[tuple[WorktreeHandle, bool], None, None]:
    slot_path = Path(record.slot_path)
    with pool.adopted_lease(slot_path, record.session_id) as idx:
        base_ref, stale_anchor = _adopted_base_ref(resume_base_ref, slot_path, record)
        branch = _git_ref(["rev-parse", "--abbrev-ref", "HEAD"], slot_path)
        yield (
            WorktreeHandle(path=slot_path, base_ref=base_ref, branch=branch, slot_index=idx),
            stale_anchor,
        )


def _resume_base_ref(
    graph: workflow_mod.WorkflowGraph | None, persist_base_ref: bool
) -> str | None:
    if graph is None or not persist_base_ref:
        return None
    return graph.doc.base_ref_sha


def _resolve_lease_params(
    graph: workflow_mod.WorkflowGraph | None,
    persist_base_ref: bool,
    *,
    prefer_slot: bool = True,
) -> tuple[int | None, str | None]:
    if graph is None:
        return None, None
    preferred_slot = graph.slot_index if prefer_slot else None
    return preferred_slot, _resume_base_ref(graph, persist_base_ref)


def _emit_adoption_lost(
    emit: EmitFn, task_id: str, inv_id: str, record: worker_records.WorkerRecord
) -> None:
    emit(TeamEvent(
        "slot_adoption_lost",
        {"task_id": task_id, "inv_id": inv_id, "slot_path": record.slot_path},
    ))


def _adopt_or_degrade(
    survivor: worker_records.WorkerRecord,
    pool: SlotPool,
    emit: EmitFn,
    task_id: str,
    inv_id: str,
    resume_base_ref: str | None,
) -> tuple[ExitStack, WorktreeHandle, bool] | None:
    stack = ExitStack()
    try:
        handle, stale_anchor = stack.enter_context(
            _adopted_handle(survivor, pool, resume_base_ref)
        )
    except SlotReservedError:
        stack.close()
        _emit_adoption_lost(emit, task_id, inv_id, survivor)
        return None
    return stack, handle, stale_anchor


def _make_on_blocked(
    emit: EmitFn, task_id: str, inv_id: str, preferred_slot: int | None
) -> Callable[[], None]:
    def on_blocked() -> None:
        emit(TeamEvent(
            "slot_resume_waiting",
            {
                "task_id": task_id,
                "inv_id": inv_id,
                "slot_index": str(preferred_slot),
            },
        ))

    return on_blocked


def _should_refresh_base_ref(
    graph: workflow_mod.WorkflowGraph, handle: WorktreeHandle, *, force_refresh: bool = False
) -> bool:
    return graph.doc.base_ref_sha is None or handle.rescued_ref is not None or force_refresh


def _persist_affinity(
    workspace: Workspace,
    graph: workflow_mod.WorkflowGraph | None,
    handle: WorktreeHandle,
    persist_base_ref: bool,
    *,
    force_refresh_base_ref: bool = False,
) -> None:
    if graph is None or handle.slot_index is None:
        return
    graph.set_slot_index(handle.slot_index)
    if persist_base_ref and _should_refresh_base_ref(
        graph, handle, force_refresh=force_refresh_base_ref
    ):
        graph.set_base_ref_sha(handle.base_ref)
    workflow_mod.save_workflow(workspace, graph.doc)
    if persist_base_ref:
        worker_records.write_slot_hold(workspace.workspace_id, str(handle.path))


@contextmanager
def leased_with_affinity(
    workspace: Workspace,
    graph: workflow_mod.WorkflowGraph | None,
    task_id: str,
    inv_id: str,
    emit: EmitFn,
    *,
    existing_branch: str | None = None,
    persist_base_ref: bool = False,
    worktree_factory: WorktreeFactory = implementation_worktree,
) -> Generator[WorktreeHandle, None, None]:
    pool = get_active_pool()
    adoption_lost = False
    resume_base_ref = _resume_base_ref(graph, persist_base_ref)
    if pool is not None:
        survivor = _find_survivor_reservation(task_id, inv_id)
        if survivor is not None:
            adopted = _adopt_or_degrade(
                survivor, pool, emit, task_id, inv_id, resume_base_ref
            )
            if adopted is not None:
                stack, handle, stale_anchor = adopted
                with stack:
                    _persist_affinity(
                        workspace, graph, handle, persist_base_ref,
                        force_refresh_base_ref=stale_anchor,
                    )
                    with per_slot_derived_data(workspace, handle.slot_index):
                        yield handle
                return
            adoption_lost = True
    preferred_slot, resume_base_ref = _resolve_lease_params(
        graph, persist_base_ref, prefer_slot=not adoption_lost
    )
    on_blocked = _make_on_blocked(emit, task_id, inv_id, preferred_slot)
    with worktree_factory(
        task_id, inv_id,
        existing_branch=existing_branch,
        preferred_slot=preferred_slot,
        resume_base_ref=resume_base_ref,
        on_blocked=on_blocked,
    ) as handle:
        _persist_affinity(workspace, graph, handle, persist_base_ref)
        with per_slot_derived_data(workspace, handle.slot_index):
            yield handle


__all__ = ["leased_with_affinity"]

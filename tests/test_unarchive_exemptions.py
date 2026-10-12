from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_ledger as ledger
from alan import unblock_sweep as sweep_mod
from alan import workflow as workflow_mod
from alan import workflow_interrupt
from alan import workspace as workspace_mod
from alan._dispatcher_retire import restore_from_archive
from alan._reconciler_orphans import (
    find_orphaned_interrupted_workspaces,
    reconcile_orphaned_workflows,
)
from alan.events import PubSub
from alan.incident import IncidentClass, derive_incidents
from alan.lifecycle import Blocker, ParkReason, Status
from alan.queue import Queue
from alan.workflow import PendingTaskSpec, seed_pending_workflow
from alan.workspace import Workspace
from alan.workspace_index import WorkspaceIndex, WorkspaceRow
from tests import _unblock_support as support

_CRASH_BLOCKER = Blocker(kind="reason", name="reason", detail="restart interrupted")


@pytest.fixture
def unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


def _row(ws: Workspace) -> WorkspaceRow:
    now = datetime.now(ws.meta.updated_at.tzinfo)
    return WorkspaceRow(
        task_id=ws.task_id,
        title="t",
        status=Status.AWAITING_INPUT,
        created_at=now,
        updated_at=now,
    )


def _restored_from_active(ws_root: Path, queue: Queue, **create_fields: Any) -> Workspace:
    ws = workspace_mod.create("restored", "d", root=ws_root, **create_fields)
    archived = workspace_mod.transition(ws, Status.ARCHIVED)
    return restore_from_archive(archived, queue)


def _parked_after_the_operator_acts(ws: Workspace, reason: ParkReason) -> Workspace:
    running = workspace_mod.transition(ws, Status.ACTIVE)
    return workspace_mod.transition(
        running, Status.AWAITING_INPUT, park_reason=reason, blockers=[_CRASH_BLOCKER]
    )


def _seed_interrupted(ws: Workspace) -> None:
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-orphan")
    workflow_mod.save_workflow(ws, graph.doc)
    workflow_interrupt.interrupt_workflow_on_disk(ws, inv_id, error="x", resumable=True)


def _sweep(
    queue: Queue,
    ws_root: Path,
    *,
    enqueue: Any,
    client: support.FakeIssueClient,
) -> int:
    return sweep_mod.sweep(
        queue=queue,
        pubsub=PubSub(),
        workspace_index=WorkspaceIndex(root=ws_root, queue=queue),
        workspaces_root=ws_root,
        now=time.time(),
        lock_for=support.lock_for,
        enqueue_workflow=enqueue,
        read_readiness=lambda *_a, **_k: support.readiness(),
        deps=support.deps(),
        issue_client=client,
    )


def test_derive_incidents_ignores_a_workspace_restored_from_archive(
    ws_root: Path, queue: Queue
) -> None:
    ws = _restored_from_active(ws_root, queue)
    assert ws.meta.status is Status.AWAITING_INPUT
    assert derive_incidents(workspaces=[ws]) == []


def test_derive_incidents_reports_a_crash_park_after_the_operator_acts(
    ws_root: Path, queue: Queue
) -> None:
    restored = _restored_from_active(ws_root, queue)
    parked = _parked_after_the_operator_acts(restored, ParkReason.CRASH)
    incidents = derive_incidents(workspaces=[parked])
    assert [i.incident_class for i in incidents] == [IncidentClass.PARK_CRASH]


def test_exempt_reason_names_the_restore_for_a_workspace_restored_from_archive(
    ws_root: Path, queue: Queue
) -> None:
    ws = _restored_from_active(ws_root, queue)
    assert sweep_mod.exempt_reason(ws, None, support.ENGINE) == "restored from archive"


def test_candidate_for_skips_a_workspace_restored_from_archive(
    ws_root: Path, queue: Queue
) -> None:
    ws = _restored_from_active(ws_root, queue)
    assert sweep_mod.candidate_for(_row(ws), workspaces_root=ws_root) is None


def test_candidate_for_returns_once_the_park_reason_changes(ws_root: Path, queue: Queue) -> None:
    restored = _restored_from_active(ws_root, queue)
    parked = _parked_after_the_operator_acts(restored, ParkReason.CRASH)
    assert sweep_mod.exempt_reason(parked, None, support.ENGINE) is None
    assert sweep_mod.candidate_for(_row(parked), workspaces_root=ws_root) is not None


@pytest.mark.usefixtures("unblock_enabled")
def test_the_sweep_does_not_probe_report_or_file_for_a_restored_workspace(
    ws_root: Path, queue: Queue
) -> None:
    _restored_from_active(ws_root, queue, team="verification")
    ledger.ensure_schema(queue)
    client = support.FakeIssueClient()
    acted = _sweep(queue, ws_root, enqueue=support.never_enqueue, client=client)
    assert acted == 0
    assert queue.run("SELECT * FROM unblock_ledger", ()) == []
    assert sweep_mod.load_watch(queue) == {}
    assert client.ensured == []
    assert client.comments == []


@pytest.mark.usefixtures("unblock_enabled")
def test_the_sweep_acts_on_a_crash_park_once_the_operator_has_acted(
    ws_root: Path, queue: Queue
) -> None:
    restored = _restored_from_active(ws_root, queue, team="verification")
    _parked_after_the_operator_acts(restored, ParkReason.CRASH)
    dispatched: list[tuple[str, str | None, str | None]] = []
    acted = _sweep(
        queue,
        ws_root,
        enqueue=support.tracking_enqueue(dispatched),
        client=support.FakeIssueClient(),
    )
    assert acted == 1
    assert dispatched[0][:2] == ("verification", "generated")


def test_orphan_finder_skips_an_interrupted_workspace_restored_from_archive(
    ws_root: Path, queue: Queue
) -> None:
    restored = _restored_from_active(ws_root, queue)
    _seed_interrupted(restored)
    assert find_orphaned_interrupted_workspaces(workspaces_root=ws_root) == []


def test_orphan_finder_still_finds_an_interrupted_crash_parked_workspace(
    ws_root: Path, queue: Queue
) -> None:
    restored = _restored_from_active(ws_root, queue)
    parked = _parked_after_the_operator_acts(restored, ParkReason.CRASH)
    _seed_interrupted(parked)
    found = find_orphaned_interrupted_workspaces(workspaces_root=ws_root)
    assert [w.task_id for w, _inv in found] == [parked.task_id]


def test_orphan_reconcile_leaves_a_workspace_restored_from_archive_untouched(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    restored = _restored_from_active(ws_root, queue)
    _seed_interrupted(restored)
    parked = reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert parked == []
    reloaded = workspace_mod.read(restored.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.UNARCHIVED
    assert queue.list_jobs(task_id=restored.task_id) == []

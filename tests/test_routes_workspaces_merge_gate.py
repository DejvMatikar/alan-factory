from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan.events import PubSub
from alan.handover import Handover, save_handover
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.teams import _pr_post, _pr_ready
from alan.workspace import Workspace, create


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _dispatch_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch"


def _post_dispatch(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_dispatch_url(ws), json=body, headers=_control_headers()),
    )


def _prior_handover(task_id: str, *, pr_number: int | None) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_prior",
        handover_type="forward",
        task_id=task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
        pr_number=pr_number,
    )


def test_manual_dispatch_without_prior_handover_skips_merge_gate(
    client: TestClient, ws_root: Path, queue: Queue,
) -> None:
    ws = create("Dispatch No Prior", "d", root=ws_root)

    res = _post_dispatch(client, ws, {"team": "research"})

    assert res.status_code == 202
    assert queue.list_jobs()


def test_manual_dispatch_recovers_mechanically_clearable_block_before_dispatch(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Dispatch Mechanically Recoverable", "d", root=ws_root)
    save_handover(ws, _prior_handover(ws.task_id, pr_number=555))
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: True)

    res = _post_dispatch(client, ws, {"team": "pr_merge"})

    assert res.status_code == 202
    [job] = queue.list_jobs()
    assert job.team == "pr_merge"
    assert job.instructions is None


def test_manual_dispatch_never_refuses_and_attaches_block_class_to_instructions(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Dispatch Attaches Block Class", "d", root=ws_root)
    save_handover(ws, _prior_handover(ws.task_id, pr_number=777))
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )

    res = _post_dispatch(client, ws, {"team": "implementation_fix"})

    assert res.status_code == 202
    [job] = queue.list_jobs()
    assert job.team == "implementation_fix"
    assert job.instructions is not None
    assert MergeBlockClass.GENUINE.value in job.instructions
    assert "ci/test" in job.instructions


def test_manual_dispatch_prior_handover_with_no_pr_number_skips_merge_gate(
    client: TestClient, ws_root: Path, queue: Queue,
) -> None:
    ws = create("Dispatch Prior No PR", "d", root=ws_root)
    save_handover(ws, _prior_handover(ws.task_id, pr_number=None))

    res = _post_dispatch(client, ws, {"team": "research"})

    assert res.status_code == 202
    [job] = queue.list_jobs()
    assert job.instructions is None

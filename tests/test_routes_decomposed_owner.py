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
from alan.handover import Handover, OpenQuestion, list_handovers, save_handover
from alan.queue import Queue
from alan.workspace import Task, TaskStatus, Workspace, create, read

_PENDING_OPTION = "Complete the out-of-fleet step yourself, then re-dispatch verification."
_WAIVE_OPTION = "Waive this finding and proceed to merge."


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


def _escalation(
    task_id: str,
    *,
    from_team: str,
    open_questions: list[OpenQuestion],
    handover_id: str,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type="escalation",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=open_questions,
    )


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def test_answer_question_decomposed_dispatches_to_owning_subtask(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="blocked-h1"),
    ]
    ws = create("Decomposed Answer", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(
        id="verif-judge-parse",
        question="judge failed to parse",
        proposed_answer_space=["retry", "review manually"],
        recommended="retry",
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    blocking = _escalation(
        ws.task_id, from_team="verification", open_questions=[q], handover_id="blocked-h1"
    )
    save_handover(ws, blocking)
    sibling = _escalation(
        ws.task_id, from_team="verification", open_questions=[], handover_id="sibling-h2"
    )
    save_handover(ws, sibling)

    res = _post_answer(client, ws, {"question_id": "verif-judge-parse", "chosen_option": "retry"})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"]["team"] == "verification"

    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].task_id == "sub-b"
    assert jobs[0].parent_handover_id == "blocked-h1"

    reloaded = read(ws.task_id, root=ws_root)
    assert "sub-b" in reloaded.meta.dispatch_instructions
    assert ws.task_id not in reloaded.meta.dispatch_instructions


def test_answer_question_decomposed_no_owner_returns_404_and_writes_nothing(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("Decomposed No Owner", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    blocking = _escalation(
        ws.task_id, from_team="implementation_fix", open_questions=[q], handover_id="h1"
    )
    save_handover(ws, blocking)

    before = len(list_handovers(ws))
    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 404
    assert ws.task_id in res.json()["detail"]
    assert len(list_handovers(ws)) == before
    assert queue.list_jobs() == []


def test_answer_question_pending_external_on_ownerless_decomposed_workspace_still_records(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("Decomposed Pending External", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(
        id="q1",
        question="needs vault access",
        proposed_answer_space=[_PENDING_OPTION, _WAIVE_OPTION],
        recommended=_PENDING_OPTION,
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={_PENDING_OPTION: "verification", _WAIVE_OPTION: "pr_merge"},
        option_effects={_PENDING_OPTION: "pending_external", _WAIVE_OPTION: "closes"},
    )
    save_handover(
        ws, _escalation(ws.task_id, from_team="verification", open_questions=[q], handover_id="h1")
    )

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": _PENDING_OPTION})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert body["pending_external"] is True
    assert queue.list_jobs() == []


def test_dispatch_task_team_decomposed_root_id_resolves_to_owning_subtask(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-open"),
    ]
    ws = create("Decomposed Dispatch", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(id="q1", question="x?", resolver_role="human", blocking=True)
    handover = _escalation(
        ws.task_id, from_team="verification", open_questions=[q], handover_id="h-open"
    )
    save_handover(ws, handover)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].task_id == "sub-b"


def test_dispatch_task_team_decomposed_no_owner_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("Dispatch No Owner", "d", root=ws_root, tasks=tasks)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404
    assert ws.task_id in res.json()["detail"]


def test_dispatch_task_team_decomposed_multi_candidate_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-a"),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-b"),
    ]
    ws = create("Dispatch Multi Owner", "d", root=ws_root, tasks=tasks)
    q1 = OpenQuestion(id="q1", question="x?", resolver_role="human", blocking=True)
    q2 = OpenQuestion(id="q2", question="y?", resolver_role="human", blocking=True)
    handover_a = _escalation(
        ws.task_id, from_team="verification", open_questions=[q1], handover_id="h-a"
    )
    handover_b = _escalation(
        ws.task_id, from_team="verification", open_questions=[q2], handover_id="h-b"
    )
    save_handover(ws, handover_a)
    save_handover(ws, handover_b)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404


def test_dispatch_task_team_undecomposed_workspace_unaffected(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Undecomposed Dispatch", "d", root=ws_root)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].task_id == ws.task_id


def test_dispatch_task_team_genuinely_unknown_id_on_undecomposed_workspace_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Dispatch Unknown Id", "d", root=ws_root)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/bogus-id/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == f"task 'bogus-id' not found in workspace {ws.task_id!r}"


def test_dispatch_task_team_bogus_id_on_decomposed_workspace_with_owner_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-open"),
    ]
    ws = create("Decomposed Dispatch Bogus Id", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(id="q1", question="x?", resolver_role="human", blocking=True)
    handover = _escalation(
        ws.task_id, from_team="verification", open_questions=[q], handover_id="h-open"
    )
    save_handover(ws, handover)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/bogus-id/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == f"task 'bogus-id' not found in workspace {ws.task_id!r}"


def test_answer_question_bogus_id_on_decomposed_workspace_with_owner_returns_404(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="blocked-h1"),
    ]
    ws = create("Decomposed Answer Bogus Id", "d", root=ws_root, tasks=tasks)
    q = OpenQuestion(
        id="q1",
        question="which?",
        proposed_answer_space=["a", "b"],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    blocking = _escalation(
        ws.task_id, from_team="verification", open_questions=[q], handover_id="blocked-h1"
    )
    save_handover(ws, blocking)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/bogus-id/answer",
        json={"question_id": "q1", "chosen_option": "a"},
        headers=_control_headers(),
    )
    assert res.status_code == 404
    assert res.json()["detail"] == f"task 'bogus-id' not found in workspace {ws.task_id!r}"
    assert queue.list_jobs() == []

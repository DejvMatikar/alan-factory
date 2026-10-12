from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

import alan.routes.pr_attach as pr_attach_mod
from alan import control_plane, process_runner, server
from alan import workflow as workflow_mod
from alan.dispatch import prior_handover_for_task
from alan.dispatcher import Dispatcher
from alan.events import PubSub
from alan.handover import (
    Handover,
    HandoverError,
    find_handover_by_id,
    list_handovers,
    record_pr_attachment,
    save_handover,
)
from alan.queue import Queue
from alan.teams import pr_merge as pr_merge_mod
from alan.teams._forge_errors import ForgeRateLimited, ForgeTransientError
from alan.workflow import PendingTaskSpec
from alan.workspace import (
    InterventionAction,
    Status,
    Task,
    TaskStatus,
    TestScenario,
    Workspace,
    create,
    read,
    transition,
    update_task_status,
)
from tests._helpers import seed_and_run


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path,
    bc_root: Path,
    ec_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher  # noqa: SLF001
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (  # noqa: SLF001
        lambda: bc_root
    )
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (  # noqa: SLF001
        lambda: ec_root
    )
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _matching_ref(ws: Workspace) -> str:
    return f"teams/{ws.task_id}-inv001"


def _zero_subtask_ws(ws_root: Path, title: str) -> Workspace:
    return create(title, "d", root=ws_root)


def _multi_subtask_ws(ws_root: Path, title: str) -> Workspace:
    return create(
        title, "d", root=ws_root,
        tasks=[Task(id="sub-a", status=TaskStatus.AWAITING_INPUT)],
    )


def _seed_handover(
    ws: Workspace,
    *,
    handover_id: str,
    pr_number: int | None = None,
    revisit_of: str | None = None,
    iteration: int = 1,
    from_invocation_id: str = "inv1",
) -> Handover:
    handover = Handover(
        handover_id=handover_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id=from_invocation_id,
        to_team_suggestions=[],
        iteration=iteration,
        revisit_of=revisit_of,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="synthesis",
        pr_number=pr_number,
    )
    save_handover(ws, handover)
    return handover


def _two_subtask_ws(ws_root: Path, title: str) -> Workspace:
    return create(
        title, "d", root=ws_root,
        tasks=[
            Task(id="sub-a", status=TaskStatus.AWAITING_INPUT),
            Task(id="sub-b", status=TaskStatus.AWAITING_INPUT),
        ],
    )


def _seed_attributed_handover(
    ws: Workspace, subtask_id: str, *, handover_id: str, pr_number: int | None = None
) -> Handover:
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="implementation_fix",
        tasks=[PendingTaskSpec(id="worker", role="worker", model="sonnet")],
        source_task_id=subtask_id,
    )
    return _seed_handover(
        ws, handover_id=handover_id, pr_number=pr_number, from_invocation_id=inv_id
    )


def _attach(
    client: TestClient,
    ws: Workspace,
    task_id: str,
    pr_number: int,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(
            f"/api/workspaces/{ws.task_id}/tasks/{task_id}/attach-pr",
            json={"pr_number": pr_number},
            headers=_control_headers() if headers is None else headers,
        ),
    )


def test_attach_pr_zero_subtask_success(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Zero Subtask Attach")
    prior = _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, ws.task_id, 42)
    assert res.status_code == 200
    data = res.json()
    assert data["pr_number"] == 42
    assert data["outcome"] == "no_subtask_pin"
    assert data["handover_id"] != prior.handover_id
    reloaded = read(ws.task_id, root=ws_root)
    handovers = list_handovers(reloaded)
    assert handovers[-1].handover_id == data["handover_id"]
    assert handovers[-1].pr_number == 42
    assert handovers[-1].revisit_of == prior.handover_id
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.ATTACH_PR]


def test_attach_pr_multi_subtask_repoints_pin(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Multi Subtask Attach")
    escalation = _seed_handover(ws, handover_id="esc-1")
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    tail = _seed_handover(
        ws, handover_id="tail-1", revisit_of=escalation.handover_id, iteration=2
    )
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-a", 77)
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "attached"
    new_id = data["handover_id"]
    assert new_id != tail.handover_id
    reloaded = read(ws.task_id, root=ws_root)
    subtask = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert subtask.blocked_handover_id == new_id
    assert subtask.status == TaskStatus.AWAITING_INPUT
    new_handover = find_handover_by_id(reloaded, new_id)
    assert new_handover is not None
    assert new_handover.revisit_of == escalation.handover_id
    assert new_handover.pr_number == 77


def _gh_merged_response(pr_number: int) -> Any:
    def _run(cmd: Any, *, cwd: Any = None, timeout: float = 0.0, env: Any = None) -> Any:
        del cwd, timeout, env
        assert cmd[:4] == ["gh", "pr", "view", str(pr_number)]
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"state": "MERGED", "mergeStateStatus": "CLEAN"})
        )

    return _run


def test_attach_then_merge_dispatch_proceeds_subtask_shape(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Dispatch Proceeds Subtask")
    escalation = _seed_handover(ws, handover_id="esc-1")
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    ws = transition(ws, Status.ESCALATED)
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-a", 42)
    assert res.status_code == 200

    reloaded = read(ws.task_id, root=ws_root)
    prior = prior_handover_for_task(reloaded, "sub-a")
    assert prior is not None
    assert prior.pr_number == 42

    monkeypatch.setenv("ALAN_PR_MERGE_AUDIT_DISABLE", "1")
    monkeypatch.setattr(process_runner, "run_subprocess", _gh_merged_response(42))
    handover = seed_and_run(pr_merge_mod.TEAM, reloaded, prior, lambda _e: None, task_id="sub-a")
    assert handover.handover_type == "terminal"
    assert handover.pr_number == 42
    assert "without a PR number" not in handover.full_synthesis


def test_attach_then_merge_dispatch_proceeds_zero_subtask_shape(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Dispatch Proceeds Zero Subtask")
    _seed_handover(ws, handover_id="h1")
    ws = transition(ws, Status.ESCALATED)
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, ws.task_id, 77)
    assert res.status_code == 200

    reloaded = read(ws.task_id, root=ws_root)
    prior = prior_handover_for_task(reloaded, reloaded.task_id)
    assert prior is not None
    assert prior.pr_number == 77

    monkeypatch.setenv("ALAN_PR_MERGE_AUDIT_DISABLE", "1")
    monkeypatch.setattr(process_runner, "run_subprocess", _gh_merged_response(77))
    handover = seed_and_run(pr_merge_mod.TEAM, reloaded, prior, lambda _e: None)
    assert handover.handover_type == "terminal"
    assert handover.pr_number == 77
    assert "without a PR number" not in handover.full_synthesis


def test_attach_pr_idempotent_noop_when_same_pr_number(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Idempotent Attach")
    prior = _seed_handover(ws, handover_id="h1", pr_number=99)
    called: list[int] = []

    def _record_call(*a: object, **kw: object) -> str:
        called.append(1)
        return _matching_ref(ws)

    monkeypatch.setattr(pr_attach_mod, "resolve_existing_branch", _record_call)
    res = _attach(client, ws, ws.task_id, 99)
    assert res.status_code == 200
    data = res.json()
    assert data["handover_id"] == prior.handover_id
    assert data["outcome"] == "no_subtask_pin"
    assert called == []
    reloaded = read(ws.task_id, root=ws_root)
    assert len(list_handovers(reloaded)) == 1


def test_attach_pr_concurrent_calls_append_exactly_once(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Concurrent Attach")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    results: list[pr_attach_mod.AttachPrResult] = []
    results_lock = threading.Lock()

    def _fire() -> None:
        result = pr_attach_mod._attach(ws, ws.task_id, 55, ws_root, queue)  # noqa: SLF001
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=_fire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert len(results) == 5
    assert all(r.pr_number == 55 for r in results)
    reloaded = read(ws.task_id, root=ws_root)
    handovers = list_handovers(reloaded)
    assert len(handovers) == 2
    attached = [h for h in handovers if h.pr_number == 55]
    assert len(attached) == 1


def test_attach_pr_no_prior_handover_409(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "No Prior Attach")
    res = _attach(client, ws, ws.task_id, 1)
    assert res.status_code == 409
    assert "no prior handover" in res.json()["detail"]


def test_attach_pr_archived_workspace_409(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Archived Attach")
    _seed_handover(ws, handover_id="h1")
    ws = transition(ws, Status.ARCHIVED)
    res = _attach(client, ws, ws.task_id, 1)
    assert res.status_code == 409
    assert res.json()["detail"] == "workspace is archived"


def test_attach_pr_forge_unreachable_transient_503(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Forge Unreachable Attach")
    _seed_handover(ws, handover_id="h1")

    def _raise(*a: object, **kw: object) -> str | None:
        raise ForgeTransientError("boom")

    monkeypatch.setattr(pr_attach_mod, "resolve_existing_branch", _raise)
    res = _attach(client, ws, ws.task_id, 5)
    assert res.status_code == 503
    assert "forge unreachable" in res.json()["detail"]


def test_attach_pr_forge_rate_limited_503(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Forge Rate Limited Attach")
    _seed_handover(ws, handover_id="h1")

    def _raise(*a: object, **kw: object) -> str | None:
        raise ForgeRateLimited("boom", 0.0)

    monkeypatch.setattr(pr_attach_mod, "resolve_existing_branch", _raise)
    res = _attach(client, ws, ws.task_id, 5)
    assert res.status_code == 503


def test_attach_pr_not_found_404(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Not Found Attach")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: None)
    res = _attach(client, ws, ws.task_id, 5)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]


def test_attach_pr_prefix_mismatch_422(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Mismatch Attach")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: "teams/unrelated-task-inv1"
    )
    res = _attach(client, ws, ws.task_id, 5)
    assert res.status_code == 422
    assert f"teams/{ws.task_id}" in res.json()["detail"]


def test_attach_pr_unknown_subtask_404(client: TestClient, ws_root: Path) -> None:
    ws = _multi_subtask_ws(ws_root, "Unknown Subtask Attach")
    _seed_handover(ws, handover_id="h1")
    res = _attach(client, ws, "no-such-subtask", 5)
    assert res.status_code == 404
    assert "no-such-subtask" in res.json()["detail"]


def test_attach_pr_without_control_token_401(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "401 Attach")
    _seed_handover(ws, handover_id="h1")
    res = _attach(client, ws, ws.task_id, 1, headers={})
    assert res.status_code == 401


def test_attach_pr_wrong_control_token_403(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "403 Attach")
    _seed_handover(ws, handover_id="h1")
    res = _attach(
        client, ws, ws.task_id, 1,
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_attach_pr_unknown_workspace_404(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces/ghost/tasks/ghost/attach-pr",
        json={"pr_number": 1},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_attach_pr_invalid_pr_number_422(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Invalid PR Number Attach")
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/attach-pr",
        json={"pr_number": 0},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_attach_pr_writes_success_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "Audit Attach")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    _attach(client, ws, ws.task_id, 8)
    entry = json.loads((logs_root / "pr_attach_audit.jsonl").read_text().splitlines()[0])
    assert entry["workspace_id"] == ws.task_id
    assert entry["pr_number"] == 8
    assert entry["path"] == "attached"


def test_attach_pr_writes_refusal_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "Refusal Audit Attach")
    res = _attach(client, ws, ws.task_id, 8)
    assert res.status_code == 409
    entry = json.loads((logs_root / "pr_attach_audit.jsonl").read_text().splitlines()[0])
    assert entry["path"] == "no_prior_handover"


def test_attach_pr_writes_noop_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "Noop Audit Attach")
    _seed_handover(ws, handover_id="h1", pr_number=3)
    _attach(client, ws, ws.task_id, 3)
    entry = json.loads((logs_root / "pr_attach_audit.jsonl").read_text().splitlines()[0])
    assert entry["path"] == "noop"


def test_record_pr_attachment_amends_prior_fields(ws_root: Path) -> None:
    ws = create("Record Attach Unit", "d", root=ws_root)
    prior = _seed_handover(ws, handover_id="h1", iteration=3)
    new = record_pr_attachment(ws, prior, 21)
    assert new.handover_type == "revisit_amend"
    assert new.task_id == ws.task_id
    assert new.iteration == 4
    assert new.revisit_of == "h1"
    assert new.pr_number == 21
    assert new.next_hop is None
    assert new.full_synthesis.startswith("Operator attached PR #21.")
    assert "synthesis" in new.full_synthesis


def test_record_pr_attachment_iteration_from_prior_not_chain_tail(ws_root: Path) -> None:
    ws = create("Record Attach Tail Unit", "d", root=ws_root)
    escalation = _seed_handover(ws, handover_id="esc", iteration=2)
    _seed_handover(ws, handover_id="tail", revisit_of="esc", iteration=9)
    new = record_pr_attachment(ws, escalation, 55)
    assert new.iteration == 3


def test_record_pr_attachment_persists_handover(ws_root: Path) -> None:
    ws = create("Record Attach Persist Unit", "d", root=ws_root)
    prior = _seed_handover(ws, handover_id="h1")
    new = record_pr_attachment(ws, prior, 12)
    reloaded = read(ws.task_id, root=ws_root)
    found = find_handover_by_id(reloaded, new.handover_id)
    assert found is not None
    assert found.pr_number == 12


def test_handover_stamped_with_subtask_id_instead_of_workspace_id_raises(
    ws_root: Path,
) -> None:
    ws = _multi_subtask_ws(ws_root, "Id Conflation Unit")
    prior = _seed_handover(ws, handover_id="h1")
    conflated = prior.model_copy(update={"handover_id": "conflated", "task_id": "sub-a"})
    with pytest.raises(HandoverError):
        save_handover(ws, conflated)


def test_record_pr_attachment_carries_test_scenarios(ws_root: Path) -> None:
    ws = create("Record Attach Scenarios Unit", "d", root=ws_root)
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    prior = Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv1",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="synthesis",
        test_scenarios=[scenario],
    )
    save_handover(ws, prior)
    new = record_pr_attachment(ws, prior, 9)
    assert [s.id for s in new.test_scenarios] == ["s1"]


def test_attach_refuses_409_when_job_pending_for_subtask(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _multi_subtask_ws(ws_root, "Pending Job Attach")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 409
    assert "in flight" in res.json()["detail"]


def test_attach_refuses_409_when_job_running_for_subtask(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _multi_subtask_ws(ws_root, "Running Job Attach")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    claimed = queue.claim()
    assert claimed is not None
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 409
    assert "in flight" in res.json()["detail"]


def test_attach_refusal_when_job_pending_leaves_workspace_unmodified(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _multi_subtask_ws(ws_root, "Refusal Leaves Workspace Unmodified")
    _seed_handover(ws, handover_id="h1")
    before = read(ws.task_id, root=ws_root)
    queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 409
    after = read(ws.task_id, root=ws_root)
    assert after.meta == before.meta
    assert len(list_handovers(after)) == len(list_handovers(before))


def test_attach_refusal_names_in_flight_job_in_detail(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _multi_subtask_ws(ws_root, "Named Job Detail")
    _seed_handover(ws, handover_id="h1")
    job = queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    res = _attach(client, ws, "sub-a", 5)
    detail = res.json()["detail"]
    assert f"job {job.id}" in detail
    assert "implementation_fix" in detail
    assert "pending" in detail


def test_attach_refusal_caps_detail_at_five_jobs(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _multi_subtask_ws(ws_root, "Cap Five Jobs")
    _seed_handover(ws, handover_id="h1")
    for _ in range(6):
        queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 409
    assert res.json()["detail"].count("job ") == 5


def test_attach_refusal_audit_path_is_in_flight(
    client: TestClient,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "In Flight Audit Path")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue(ws.workspace_id, "implementation", workspace_id=ws.workspace_id)
    res = _attach(client, ws, ws.task_id, 5)
    assert res.status_code == 409
    entry = json.loads((logs_root / "pr_attach_audit.jsonl").read_text().splitlines()[0])
    assert entry["path"] == "in_flight"


def test_attach_refusal_audit_line_omits_job_identifiers(
    client: TestClient,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "In Flight Audit No Job Ids")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue(ws.workspace_id, "implementation", workspace_id=ws.workspace_id)
    _attach(client, ws, ws.task_id, 5)
    entry = json.loads((logs_root / "pr_attach_audit.jsonl").read_text().splitlines()[0])
    assert set(entry.keys()) == {
        "workspace_id", "task_id", "pr_number", "path", "timestamp_utc",
    }


def test_attach_succeeds_with_outcome_attached_when_nothing_in_flight(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Nothing In Flight Success")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-a", 12)
    assert res.status_code == 200
    assert res.json()["outcome"] == "attached"


def test_repeat_attach_returns_already_attached_and_repoints(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Repeat Attach Repoints")
    escalation = _seed_handover(ws, handover_id="esc-1")
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    first = _attach(client, ws, "sub-a", 77)
    assert first.status_code == 200
    new_handover_id = first.json()["handover_id"]

    reloaded = read(ws.task_id, root=ws_root)
    cleared = update_task_status(
        reloaded, "sub-a", TaskStatus.AWAITING_INPUT, clear_blocked_handover_id=True
    )

    second = _attach(client, cleared, "sub-a", 77)
    assert second.status_code == 200
    data = second.json()
    assert data["outcome"] == "already_attached"
    assert data["handover_id"] == new_handover_id

    final = read(ws.task_id, root=ws_root)
    subtask = next(t for t in final.meta.tasks if t.id == "sub-a")
    assert subtask.blocked_handover_id == new_handover_id


def test_repeat_attach_after_pointer_cleared_externally_repoints_again(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Repeat After Clear")
    escalation = _seed_handover(ws, handover_id="esc-1")
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    first = _attach(client, ws, "sub-a", 88)
    assert first.status_code == 200
    before_clear = read(ws.task_id, root=ws_root)
    before_updated_at = before_clear.meta.updated_at
    cleared = update_task_status(
        before_clear, "sub-a", TaskStatus.AWAITING_INPUT, clear_blocked_handover_id=True
    )
    assert next(t for t in cleared.meta.tasks if t.id == "sub-a").blocked_handover_id is None

    second = _attach(client, cleared, "sub-a", 88)
    assert second.status_code == 200
    assert second.json()["outcome"] == "already_attached"

    final = read(ws.task_id, root=ws_root)
    subtask = next(t for t in final.meta.tasks if t.id == "sub-a")
    assert subtask.blocked_handover_id == first.json()["handover_id"]
    assert final.meta.updated_at > before_updated_at


def test_repeat_attach_does_not_record_intervention(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Repeat No Intervention")
    _seed_handover(ws, handover_id="h1", pr_number=3)
    _attach(client, ws, ws.task_id, 3)
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_attach_on_zero_subtask_workspace_returns_no_subtask_pin(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "Zero Subtask No Pin")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, ws.task_id, 12)
    assert res.status_code == 200
    assert res.json()["outcome"] == "no_subtask_pin"


def test_attach_liveness_gate_keys_on_workspace_id_when_task_id_unmatched(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _zero_subtask_ws(ws_root, "Liveness Fallback Key")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue(ws.workspace_id, "implementation", workspace_id=ws.workspace_id)
    res = _attach(client, ws, "arbitrary-task-id", 9)
    assert res.status_code == 409


def test_attach_refusal_clears_once_the_in_flight_job_leaves_live_status(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _multi_subtask_ws(ws_root, "Liveness Clears")
    _seed_handover(ws, handover_id="h1")
    queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    blocked = _attach(client, ws, "sub-a", 5)
    assert blocked.status_code == 409
    claimed = queue.claim()
    assert claimed is not None
    queue.fail(claimed.id, "boom")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 200


def test_attach_response_schema_has_no_repointed_field(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _zero_subtask_ws(ws_root, "No Repointed Field")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, ws.task_id, 4)
    data = res.json()
    assert "repointed" not in data
    assert "outcome" in data


def test_attach_on_split_workspace_with_no_attributable_handover_accepts_the_subtask(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _two_subtask_ws(ws_root, "Unattributable Split Attach")
    _seed_handover(ws, handover_id="h1")
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-b", 31)
    assert res.status_code == 200
    assert res.json()["outcome"] == "attached"


def test_attach_refuses_a_subtask_whose_own_history_is_empty(
    client: TestClient, ws_root: Path
) -> None:
    ws = _two_subtask_ws(ws_root, "Never Produced Attach")
    _seed_attributed_handover(ws, "sub-a", handover_id="a1", pr_number=5)
    res = _attach(client, ws, "sub-b", 5)
    assert res.status_code == 409
    assert "no prior handover" in res.json()["detail"]
    assert len(list_handovers(read(ws.task_id, root=ws_root))) == 1


def test_attach_of_a_pull_request_the_subtask_already_carries_repins_without_amending(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _two_subtask_ws(ws_root, "Already Carried Attach")
    _seed_attributed_handover(ws, "sub-a", handover_id="a1", pr_number=5)
    escalation = _seed_attributed_handover(ws, "sub-a", handover_id="a-esc")
    _seed_attributed_handover(ws, "sub-b", handover_id="b1", pr_number=9)
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    forge_calls: list[int] = []

    def _record_call(*a: object, **kw: object) -> str:
        forge_calls.append(1)
        return _matching_ref(ws)

    monkeypatch.setattr(pr_attach_mod, "resolve_existing_branch", _record_call)
    res = _attach(client, ws, "sub-a", 5)
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "already_attached"
    assert data["handover_id"] == escalation.handover_id
    assert forge_calls == []
    assert len(list_handovers(read(ws.task_id, root=ws_root))) == 3


def test_attach_of_a_different_pull_request_amends_from_the_recovered_prior(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _two_subtask_ws(ws_root, "Different PR Attach")
    _seed_attributed_handover(ws, "sub-a", handover_id="a1", pr_number=5)
    escalation = _seed_attributed_handover(ws, "sub-a", handover_id="a-esc")
    ws = update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id=escalation.handover_id
    )
    monkeypatch.setattr(
        pr_attach_mod, "resolve_existing_branch", lambda *a, **kw: _matching_ref(ws)
    )
    res = _attach(client, ws, "sub-a", 6)
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    prior = prior_handover_for_task(reloaded, "sub-a")
    assert prior is not None
    assert prior.handover_id == res.json()["handover_id"]
    assert prior.pr_number == 6
    assert prior.revisit_of == escalation.handover_id


def test_openapi_409_description_mentions_in_flight(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"][
        "/api/workspaces/{workspace_id}/tasks/{task_id}/attach-pr"
    ]["post"]["responses"]
    assert "in flight" in responses["409"]["description"]

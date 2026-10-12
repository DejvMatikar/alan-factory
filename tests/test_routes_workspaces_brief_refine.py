from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.app.workspace_creation as creation_mod
import alan.build_chat as build_chat_mod
import alan.lane_limit as lane_limit_mod
import alan.prep_chat as prep_chat_mod
import alan.teams.brief_refine as brief_refine_mod
from alan import server
from alan.app.workspace_create_models import UNCOVERED_CLAUSES_DETAIL
from alan.app.workspace_creation import should_refine
from alan.build_chat import BuildChatMessage
from alan.dispatcher import Dispatcher, PubSub
from alan.engine_config import canonical_scope_key
from alan.queue import Queue
from alan.routes._workspaces_merge_gate import create_dispatch_skip_decision
from alan.workflow import list_invocations
from alan.workspace import create, read


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture(autouse=True)
def _stub_predispatch_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])


@pytest.fixture
def client(
    ws_root: Path,
    bc_root: Path,
    ec_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_should_refine_true_for_freshly_created_workspace(ws_root: Path) -> None:
    ws = create("Fresh", "d", root=ws_root)
    assert should_refine(ws)


def test_should_refine_false_once_an_invocation_exists(ws_root: Path) -> None:
    from alan.workflow import PendingTaskSpec, seed_pending_workflow

    ws = create("Not Fresh", "d", root=ws_root)
    spec = PendingTaskSpec(id="brief_refiner", role="brief_refiner", model="opus")
    seed_pending_workflow(ws, team="brief_refine", tasks=[spec])
    reloaded = read(ws.task_id, root=ws_root)
    assert not should_refine(reloaded)
    assert list_invocations(reloaded)


def test_create_workspace_with_team_dispatches_brief_refine_first(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Refine Me", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    jobs = queue.list_jobs_for_workspace(task_id)
    assert len(jobs) == 1
    assert jobs[0].team == "brief_refine"


def test_create_workspace_with_team_stamps_pending_first_hop(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Refine Me", "description": "d", "team": "implementation_plan"},
    )
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert ws.meta.pending_first_hop == "implementation_plan"


def test_create_workspace_with_instructions_persists_sticky_not_job_instructions(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Refine With Instructions",
            "description": "d",
            "team": "research",
            "instructions": "Focus narrowly on the login flow.",
        },
    )
    task_id = res.json()["task_id"]
    jobs = queue.list_jobs_for_workspace(task_id)
    assert jobs[0].instructions is None
    ws = read(task_id, root=ws_root)
    sticky = ws.meta.dispatch_instructions.get(task_id)
    assert sticky is not None
    assert sticky.text == "Focus narrowly on the login flow."
    assert sticky.origin == "dispatch"


def test_create_workspace_without_instructions_writes_no_sticky(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "No Instructions", "description": "d", "team": "research"},
    )
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert task_id not in ws.meta.dispatch_instructions


def test_create_workspace_without_team_does_not_stamp_pending_first_hop(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post("/api/workspaces", json={"title": "No Team", "description": "d"})
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert ws.meta.pending_first_hop is None


def test_create_workspace_skips_refine_when_should_refine_is_false(
    client: TestClient, queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(creation_mod, "should_refine", lambda ws: False)
    res = client.post(
        "/api/workspaces",
        json={"title": "Skip Refine", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    jobs = queue.list_jobs_for_workspace(task_id)
    assert len(jobs) == 1
    assert jobs[0].team == "research"


def test_create_workspace_with_internal_only_dispatch_as_bypasses_manual_gate(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    import asyncio

    from alan.app.workspace_creation import dispatch_root_task_or_rollback

    ws = create("Internal Dispatch", "d", root=ws_root, pending_first_hop="research")
    asyncio.run(
        dispatch_root_task_or_rollback(
            ws, "research", None, None, queue, pubsub, ws_root,
            merge_gate=create_dispatch_skip_decision(), dispatch_as="brief_refine",
        )
    )
    jobs = queue.list_jobs_for_workspace(ws.task_id)
    assert len(jobs) == 1
    assert jobs[0].team == "brief_refine"


def test_create_without_team_archives_prep_chat_and_exposes_transcript(
    client: TestClient, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "No Team Prep",
            "description": "d",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["prep_chat"]["session_id"] == session.session_id
    transcript = client.get(f"/api/workspaces/{task_id}/prep-chat")
    assert transcript.status_code == 200
    assert transcript.json()["session_id"] == session.session_id


def test_create_with_seeded_tasks_archives_prep_chat(client: TestClient, bc_root: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Seeded Prep",
            "description": "d",
            "tasks": [{"id": "a", "team": "implementation"}],
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    transcript = client.get(f"/api/workspaces/{task_id}/prep-chat")
    assert transcript.status_code == 200


def test_create_with_team_still_archives_prep_chat(client: TestClient, bc_root: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Team Prep",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    transcript = client.get(f"/api/workspaces/{task_id}/prep-chat")
    assert transcript.status_code == 200


@pytest.mark.parametrize("team", [None, "research"])
def test_create_still_returns_201_when_prep_chat_archive_raises(
    client: TestClient,
    bc_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    team: str | None,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(prep_chat_mod, "archive", _boom)
    session = build_chat_mod.create_session(root=bc_root)
    body: dict[str, object] = {
        "title": "Archive Explodes",
        "description": "d",
        "prep_chat_kind": "build",
        "prep_chat_session_id": session.session_id,
    }
    if team is not None:
        body["team"] = team
    with caplog.at_level(logging.WARNING):
        res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert client.get(f"/api/workspaces/{task_id}").status_code == 200
    assert any("prep-chat archive failed" in r.message for r in caplog.records)


def test_create_without_prep_chat_ref_attempts_no_archive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def _track(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(prep_chat_mod, "archive", _track)
    res = client.post("/api/workspaces", json={"title": "No Prep Ref", "description": "d"})
    assert res.status_code == 201
    assert calls == []


def test_create_archive_runs_after_brief_refine_source_write(
    client: TestClient, bc_root: Path, ws_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    session = build_chat_mod.append_user_message_and_mark_started(
        session,
        BuildChatMessage(role="user", content="Please build the thing.", ts=datetime.now(UTC)),
        root=bc_root,
    )
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Source Preserved",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    source_path = ws.path / brief_refine_mod.SOURCE_FILE_NAME
    assert source_path.read_text(encoding="utf-8").strip() == "Please build the thing."


def test_create_dispatch_failure_rolls_back_before_archiving(
    client: TestClient, bc_root: Path, ws_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Dispatch Fails",
            "description": "d",
            "team": "not-a-real-team",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 400
    assert (bc_root / session.session_id / "session.json").is_file()
    assert not ws_root.exists() or list(ws_root.iterdir()) == []


def test_create_refused_by_lane_limit_leaves_session_live(
    client: TestClient, queue: Queue, ws_root: Path, bc_root: Path
) -> None:
    lane_limit_mod.set_limit(queue, canonical_scope_key(None), 1, source="api")
    create("existing", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Lane Full",
            "description": "d",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 409
    assert (bc_root / session.session_id / "session.json").is_file()


def test_create_with_bad_seeded_team_creates_no_workspace(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Bad Seed",
            "description": "d",
            "tasks": [{"id": "a", "team": "decomposer"}],
        },
    )
    assert res.status_code == 400
    assert not ws_root.exists() or list(ws_root.iterdir()) == []


def test_create_with_supplied_clause_id_creates_no_workspace(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Bad Clause Id",
            "description": "d",
            "requirements": [{"id": "req-caller-chosen", "text": "clause a"}],
        },
    )
    assert res.status_code == 422
    assert not ws_root.exists() or list(ws_root.iterdir()) == []


def test_create_with_both_supplied_clause_id_and_bad_seeded_team_returns_422(
    client: TestClient,
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Both Bad",
            "description": "d",
            "tasks": [{"id": "a", "team": "decomposer"}],
            "requirements": [{"id": "req-caller-chosen", "text": "clause a"}],
        },
    )
    assert res.status_code == 422


def test_create_with_both_uncovered_clause_and_bad_seeded_team_returns_uncovered_detail(
    client: TestClient,
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Uncovered And Bad Team",
            "description": "d",
            "tasks": [
                {"id": "a", "team": "implementation"},
                {"id": "b", "team": "decomposer"},
            ],
            "requirements": [{"text": "clause a"}],
        },
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == UNCOVERED_CLAUSES_DETAIL


def test_create_without_team_still_dispatches_nothing(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces", json={"title": "No Team No Dispatch", "description": "d"}
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert queue.list_jobs_for_workspace(task_id) == []

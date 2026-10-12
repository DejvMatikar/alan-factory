from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.app.workspace_creation as creation_mod
import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
from alan import project as project_mod
from alan import project_auto_continue, server
from alan import project_roadmap as project_roadmap_mod
from alan import reconciler as reconciler_mod
from alan import workspace as workspace_mod
from alan.dispatch_provenance import DispatchSurface
from alan.dispatcher import Dispatcher
from alan.engine_config import default_engine_config, register
from alan.events import PubSub
from alan.handover import Handover, save_handover
from alan.project_roadmap import ParsedRoadmapItem, RoadmapItem
from alan.queue import Job, JobStatus, Queue
from alan.teams.dependency_sweep import _ensure_pr_workspace
from alan.workspace import Status, Workspace


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


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


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(p))
    return p


class _RecordingEnqueue:
    def __call__(self, **kwargs: Any) -> tuple[Job, str]:
        job = Job(
            id=1,
            task_id=kwargs["task_id"],
            team=kwargs["team"].name,
            status=JobStatus.PENDING,
            enqueued_at=datetime.now(UTC),
        )
        return job, "inv_001"


def _make_project(projects_root: Path) -> project_mod.Project:
    return project_mod.create("Sprocket", root=projects_root)


def _seed_roadmap(
    project_id: str, projects_root: Path, *titles: str
) -> list[RoadmapItem]:
    parsed = [ParsedRoadmapItem(title=t, text=f"{t} body") for t in titles]
    return project_roadmap_mod.seed_items(project_id, parsed, root=projects_root)


def _deliver(ws: Workspace) -> None:
    save_handover(
        ws,
        Handover(
            handover_id="h1",
            handover_chain_id="h1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_000",
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="done",
            pr_number=1,
        ),
    )
    workspace_mod.transition(ws, Status.COMPLETED)


def test_build_chat_dispatch_records_build_chat_surface(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Build Chat WS",
            "description": "d",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.BUILD_CHAT


def test_explore_chat_dispatch_records_explore_chat_surface(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    session = explore_chat_mod.create_session("architecture", root=ec_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Explore Chat WS",
            "description": "d",
            "prep_chat_kind": "explore",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.EXPLORE_CHAT


def test_api_direct_dispatch_records_api_direct_surface(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post("/api/workspaces", json={"title": "Api Direct WS", "description": "d"})
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.API_DIRECT


def test_triage_sweep_records_triage_sweep_surface(queue: Queue, ws_root: Path) -> None:
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    triage_ws = next(
        ws for ws in workspace_mod.list_workspaces(root=ws_root) if ws.meta.team == "triage"
    )
    assert triage_ws.meta.dispatch_provenance.surface == DispatchSurface.TRIAGE_SWEEP


def test_dep_sweep_parent_records_dep_sweep_parent_surface(
    queue: Queue, ws_root: Path
) -> None:
    register(default_engine_config(), default=True, validate_env=False)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = next(
        ws
        for ws in workspace_mod.list_workspaces(root=ws_root)
        if ws.meta.team == "dependency_sweep" and ws.meta.title == "Dependency Sweep"
    )
    assert dep_ws.meta.dispatch_provenance.surface == DispatchSurface.DEP_SWEEP_PARENT


def test_dep_sweep_pr_records_dep_sweep_pr_surface(ws_root: Path) -> None:
    ws = _ensure_pr_workspace(123, ws_root)
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.DEP_SWEEP_PR


def test_project_auto_continue_records_project_auto_continue_surface(
    ws_root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _make_project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship it")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=ws_root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    outcome = project_auto_continue.advance_project(
        project.id,
        workspaces_root=ws_root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=_RecordingEnqueue(),
    )
    assert outcome is not None
    assert (
        outcome.workspace.meta.dispatch_provenance.surface
        == DispatchSurface.PROJECT_AUTO_CONTINUE
    )


def test_dispatch_actor_absent_is_none(client: TestClient, ws_root: Path) -> None:
    res = client.post("/api/workspaces", json={"title": "No Actor", "description": "d"})
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.actor is None


def test_dispatch_actor_valid_persists_verbatim(client: TestClient, ws_root: Path) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "With Actor", "description": "d", "dispatch_actor": "issue-driver"},
    )
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.actor == "issue-driver"


def test_dispatch_actor_malformed_is_422_and_creates_no_workspace(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Bad Actor", "description": "d", "dispatch_actor": "Not Valid!"},
    )
    assert res.status_code == 422
    assert workspace_mod.list_workspaces(root=ws_root) == []


def test_prep_chat_session_id_persists_and_is_archived_even_without_team(
    client: TestClient, ws_root: Path, bc_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Archived Without Team",
            "description": "d",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    ws = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert ws.meta.dispatch_provenance.prep_chat_session_id == session.session_id
    assert (ws.path / "prep-chat" / session.session_id / "session.json").is_file()

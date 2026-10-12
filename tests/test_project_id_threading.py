from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.claude_cli as claude_cli
import alan.explore_chat as explore_chat_mod
import alan.project as project_mod
import alan.workspace as workspace_mod
from alan import server


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
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def client(
    ws_root: Path,
    bc_root: Path,
    ec_root: Path,
    projects_root: Path,
) -> Iterator[TestClient]:
    _ = projects_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_legacy_meta_and_chat_backcompat(ws_root: Path, bc_root: Path, ec_root: Path) -> None:
    ws = workspace_mod.create("Legacy WS", "d", root=ws_root)
    raw_meta = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    assert "project_id" in raw_meta
    del raw_meta["project_id"]
    (ws.path / "meta.json").write_text(json.dumps(raw_meta), encoding="utf-8")
    reloaded_ws = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded_ws.meta.project_id is None
    all_ws = workspace_mod.list_workspaces(root=ws_root)
    assert all(w.meta.project_id != "some-project" for w in all_ws)

    bc_session = build_chat_mod.create_session(root=bc_root)
    bc_path = bc_root / bc_session.session_id / "session.json"
    raw_bc = json.loads(bc_path.read_text(encoding="utf-8"))
    assert "project_id" in raw_bc
    del raw_bc["project_id"]
    bc_path.write_text(json.dumps(raw_bc), encoding="utf-8")
    reloaded_bc = build_chat_mod.load_session(bc_session.session_id, root=bc_root)
    assert reloaded_bc.project_id is None
    assert reloaded_bc.project_id != "some-project"

    ec_session = explore_chat_mod.create_session("brainstorming", root=ec_root)
    ec_path = ec_root / ec_session.session_id / "session.json"
    raw_ec = json.loads(ec_path.read_text(encoding="utf-8"))
    assert "project_id" in raw_ec
    del raw_ec["project_id"]
    ec_path.write_text(json.dumps(raw_ec), encoding="utf-8")
    reloaded_ec = explore_chat_mod.load_session(ec_session.session_id, root=ec_root)
    assert reloaded_ec.project_id is None
    assert reloaded_ec.project_id != "some-project"


def test_alan_projects_root_three_surface_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_root = tmp_path / "envroot"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(env_root))
    assert project_mod._root(None) == env_root  # noqa: SLF001

    assert claude_cli._WORKER_SANDBOX_ROOT_ENVS["ALAN_PROJECTS_ROOT"] == "projects"  # noqa: SLF001

    server_env_path = (
        Path(__file__).resolve().parent.parent
        / "alan"
        / "web"
        / "e2e"
        / "fixtures"
        / "server-env.ts"
    )
    server_env_src = server_env_path.read_text(encoding="utf-8")
    assert "ALAN_PROJECTS_ROOT" in server_env_src


def test_workspace_create_stamps_project_id_and_toggles_ledger_membership(
    client: TestClient, ws_root: Path
) -> None:
    project_id = client.post("/api/projects", json={"title": "Spine"}).json()["id"]

    in_project = client.post(
        "/api/workspaces",
        json={"title": "In Project", "description": "d", "project_id": project_id},
    )
    assert in_project.status_code == 201
    in_task_id = in_project.json()["task_id"]

    client.post("/api/workspaces", json={"title": "No Project", "description": "d"})

    reloaded = workspace_mod.read(in_task_id, root=ws_root)
    assert reloaded.meta.project_id == project_id

    ledger = client.get(f"/api/projects/{project_id}").json()["workspaces"]
    task_ids = [entry["task_id"] for entry in ledger]
    assert task_ids == [in_task_id]


def test_workspace_create_without_project_id_defaults_none(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces", json={"title": "Unassigned", "description": "d"}
    )
    task_id = res.json()["task_id"]
    reloaded = workspace_mod.read(task_id, root=ws_root)
    assert reloaded.meta.project_id is None


def test_build_chat_create_accepts_and_persists_project_id(
    client: TestClient, bc_root: Path
) -> None:
    res = client.post(
        "/api/build-chat/sessions",
        json={"engine_key": "DejvMatikar/alan", "project_id": "spine"},
    )
    assert res.status_code == 201
    session_id = res.json()["session_id"]
    persisted = build_chat_mod.load_session(session_id, root=bc_root)
    assert persisted.project_id == "spine"


def test_build_chat_create_without_project_id_defaults_none(
    client: TestClient, bc_root: Path
) -> None:
    res = client.post("/api/build-chat/sessions", json={"engine_key": "DejvMatikar/alan"})
    assert res.status_code == 201
    session_id = res.json()["session_id"]
    persisted = build_chat_mod.load_session(session_id, root=bc_root)
    assert persisted.project_id is None


def test_explore_chat_create_accepts_and_persists_project_id(
    client: TestClient, ec_root: Path
) -> None:
    res = client.post(
        "/api/explore-chat/sessions",
        json={
            "consultant_key": "brainstorming",
            "engine_key": "DejvMatikar/alan",
            "project_id": "spine",
        },
    )
    assert res.status_code == 201
    session_id = res.json()["session_id"]
    persisted = explore_chat_mod.load_session(session_id, root=ec_root)
    assert persisted.project_id == "spine"


def test_explore_chat_create_without_project_id_defaults_none(
    client: TestClient, ec_root: Path
) -> None:
    res = client.post(
        "/api/explore-chat/sessions",
        json={"consultant_key": "brainstorming", "engine_key": "DejvMatikar/alan"},
    )
    assert res.status_code == 201
    session_id = res.json()["session_id"]
    persisted = explore_chat_mod.load_session(session_id, root=ec_root)
    assert persisted.project_id is None

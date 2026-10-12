from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.workspace as workspace_mod
from alan import server


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def client(ws_root: Path, projects_root: Path) -> Iterator[TestClient]:
    _ = projects_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _create_project(client: TestClient, title: str = "Spine") -> str:
    res = client.post("/api/projects", json={"title": title})
    assert res.status_code == 201
    project_id: str = res.json()["id"]
    return project_id


def _import_roadmap(client: TestClient, project_id: str, markdown: str) -> list[dict[str, str]]:
    res = client.post(f"/api/projects/{project_id}/roadmap/import", json={"markdown": markdown})
    assert res.status_code == 201
    items: list[dict[str, str]] = res.json()["items"]
    return items


def test_workspace_create_without_roadmap_item_id_leaves_item_unlinked(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    item_id = _import_roadmap(client, project_id, "## Item One\nDo the thing.\n")[0]["id"]

    res = client.post(
        "/api/workspaces",
        json={"title": "Unstamped", "description": "d", "project_id": project_id},
    )
    assert res.status_code == 201
    reloaded = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert reloaded.meta.roadmap_item_id is None

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(i for i in roadmap if i["id"] == item_id)
    assert entry["status"] == "not_started"
    assert entry["merged_prs"] == []


def test_workspace_create_with_roadmap_item_id_links_back_to_item(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    item_id = _import_roadmap(client, project_id, "## Item One\nDo the thing.\n")[0]["id"]

    res = client.post(
        "/api/workspaces",
        json={
            "title": "Stamped",
            "description": "d",
            "project_id": project_id,
            "roadmap_item_id": item_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    reloaded = workspace_mod.read(task_id, root=ws_root)
    assert reloaded.meta.roadmap_item_id == item_id

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(i for i in roadmap if i["id"] == item_id)
    assert entry["status"] == "in_flight"
    assert entry["merged_prs"] == []


def _create_completed_pr_workspace(
    *, ws_root: Path, project_id: str, roadmap_item_id: str, title: str, pr_number: int
) -> None:
    ws = workspace_mod.create(
        title,
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=roadmap_item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships the feature")],
            )
        ],
    )
    workspace_mod.record_task_evidence(ws, "solo", f"PR #{pr_number}")
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )


def test_roadmap_item_shows_delivered_with_merged_pr_once_stamped_workspace_completes(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    item_id = _import_roadmap(client, project_id, "## Item One\nDo the thing.\n")[0]["id"]

    _create_completed_pr_workspace(
        ws_root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        title="Delivered WS",
        pr_number=42,
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(i for i in roadmap if i["id"] == item_id)
    assert entry["status"] == "delivered"
    assert entry["merged_prs"] == [42]


def test_roadmap_item_merges_prs_across_superseded_and_followup_workspaces(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    item_id = _import_roadmap(client, project_id, "## Item One\nDo the thing.\n")[0]["id"]

    _create_completed_pr_workspace(
        ws_root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        title="First Attempt",
        pr_number=11,
    )
    _create_completed_pr_workspace(
        ws_root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        title="Follow Up",
        pr_number=10,
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(i for i in roadmap if i["id"] == item_id)
    assert entry["status"] == "delivered"
    assert entry["merged_prs"] == [10, 11]


def test_roadmap_item_stays_delivered_after_workspace_auto_archived(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    item_id = _import_roadmap(client, project_id, "## Item One\nDo the thing.\n")[0]["id"]

    _create_completed_pr_workspace(
        ws_root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        title="Delivered Then Archived",
        pr_number=7,
    )
    ws = workspace_mod.list_workspaces(root=ws_root)[0]
    workspace_mod.transition(ws, workspace_mod.Status.ARCHIVED)

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(i for i in roadmap if i["id"] == item_id)
    assert entry["status"] == "delivered"
    assert entry["merged_prs"] == [7]


def test_roadmap_item_id_stamp_is_not_validated_against_roadmap_store(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Ghost Stamp",
            "description": "d",
            "project_id": project_id,
            "roadmap_item_id": "itm_does_not_exist",
        },
    )
    assert res.status_code == 201
    reloaded = workspace_mod.read(res.json()["task_id"], root=ws_root)
    assert reloaded.meta.roadmap_item_id == "itm_does_not_exist"

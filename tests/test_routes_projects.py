from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import pytest
from fastapi.testclient import TestClient

import alan.project_amendment as project_amendment_mod
from alan import handover as handover_mod
from alan import server
from alan import workspace as workspace_mod


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def client(projects_root: Path, ws_root: Path) -> Iterator[TestClient]:
    _ = projects_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _stamp_meta(ws: workspace_mod.Workspace, **fields: object) -> None:
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw.update(fields)
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")


def _stamp_project_id(ws: workspace_mod.Workspace, project_id: str) -> None:
    _stamp_meta(ws, project_id=project_id)


def _create_project(client: TestClient, title: str = "Spine") -> str:
    res = client.post("/api/projects", json={"title": title})
    assert res.status_code == 201
    project_id: str = res.json()["id"]
    return project_id


def _import_roadmap(client: TestClient, project_id: str, markdown: str) -> httpx.Response:
    res: httpx.Response = client.post(
        f"/api/projects/{project_id}/roadmap/import", json={"markdown": markdown}
    )
    return res


def _preview_roadmap(client: TestClient, project_id: str, markdown: str) -> httpx.Response:
    res: httpx.Response = client.post(
        f"/api/projects/{project_id}/roadmap/preview", json={"markdown": markdown}
    )
    return res


def test_create_project_returns_201_with_active_status(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": "Spine"})
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == "spine"
    assert body["status"] == "active"
    assert body["doc_ref"] is None


def test_create_project_with_doc_ref(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": "Docs", "doc_ref": "docs/x.md"})
    assert res.status_code == 201
    assert res.json()["doc_ref"] == "docs/x.md"


def test_create_project_with_intended_engine_key_echoes_it(client: TestClient) -> None:
    res = client.post(
        "/api/projects", json={"title": "Intent", "intended_engine_key": "acme/sprocket"}
    )
    assert res.status_code == 201
    assert res.json()["intended_engine_key"] == "acme/sprocket"


def test_create_project_omitted_intended_engine_key_is_none(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": "No Intent"})
    assert res.status_code == 201
    assert res.json()["intended_engine_key"] is None


def test_create_project_unregistered_intended_engine_key_still_201s(client: TestClient) -> None:
    res = client.post(
        "/api/projects",
        json={"title": "Ghost Intent", "intended_engine_key": "ghost-co/ghost-repo"},
    )
    assert res.status_code == 201
    assert res.json()["intended_engine_key"] == "ghost-co/ghost-repo"


def test_create_project_rejects_empty_intended_engine_key_422(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": "Empty Intent", "intended_engine_key": ""})
    assert res.status_code == 422


def test_create_project_rejects_overlong_intended_engine_key_422(client: TestClient) -> None:
    res = client.post(
        "/api/projects", json={"title": "Long Intent", "intended_engine_key": "a" * 201}
    )
    assert res.status_code == 422


def test_list_and_detail_projects_carry_intended_engine_key(client: TestClient) -> None:
    create_res = client.post(
        "/api/projects", json={"title": "Carries Intent", "intended_engine_key": "acme/sprocket"}
    )
    project_id = create_res.json()["id"]

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.json()["intended_engine_key"] == "acme/sprocket"

    listing = client.get("/api/projects")
    entry = next(p for p in listing.json() if p["id"] == project_id)
    assert entry["intended_engine_key"] == "acme/sprocket"


def test_create_project_defaults_autonomy_and_budget(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": "Autonomy Defaults"})
    assert res.status_code == 201
    body = res.json()
    assert body["autonomy"] == "propose"
    assert body["budget_usd"] is None


def test_create_project_rejects_blank_title(client: TestClient) -> None:
    res = client.post("/api/projects", json={"title": ""})
    assert res.status_code == 422


def test_list_projects_empty(client: TestClient) -> None:
    res = client.get("/api/projects")
    assert res.status_code == 200
    assert res.json() == []


def test_list_projects_returns_created_projects(client: TestClient) -> None:
    client.post("/api/projects", json={"title": "Alpha"})
    client.post("/api/projects", json={"title": "Beta"})
    res = client.get("/api/projects")
    ids = sorted(p["id"] for p in res.json())
    assert ids == ["alpha", "beta"]


def test_list_projects_default_excludes_archived(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "To Archive"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})
    res = client.get("/api/projects")
    assert res.json() == []


def test_list_projects_include_archived_returns_all(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "To Archive"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})
    res = client.get("/api/projects", params={"include": "archived"})
    ids = [p["id"] for p in res.json()]
    assert ids == [project_id]


def _write_cost(ws_path: Path, *, cost: float) -> None:
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    (ws_path / "manager_summary_stream.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )


def test_list_projects_zero_workspaces_project_reports_zero_count_and_spend(
    client: TestClient,
) -> None:
    client.post("/api/projects", json={"title": "Empty"})
    res = client.get("/api/projects")
    entry = res.json()[0]
    assert entry["workspace_count"] == 0
    assert entry["total_spend_usd"] == pytest.approx(0.0)


def test_list_projects_includes_workspace_count_and_total_spend(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Parity"})
    project_id = create_res.json()["id"]
    ws1 = workspace_mod.create("Active Child", "d", root=ws_root)
    _stamp_project_id(ws1, project_id)
    _write_cost(ws1.path, cost=1.25)

    list_res = client.get("/api/projects")
    entry = next(p for p in list_res.json() if p["id"] == project_id)

    assert entry["workspace_count"] == 1
    assert entry["total_spend_usd"] == pytest.approx(1.25)


def test_list_projects_aggregate_includes_archived_workspaces(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Parity"})
    project_id = create_res.json()["id"]
    ws1 = workspace_mod.create("Active Child", "d", root=ws_root)
    ws2 = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(ws1, project_id)
    _stamp_project_id(ws2, project_id)
    _write_cost(ws1.path, cost=1.25)
    _write_cost(ws2.path, cost=2.75)
    workspace_mod.transition(
        workspace_mod.read(ws2.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    list_res = client.get("/api/projects")
    entry = next(p for p in list_res.json() if p["id"] == project_id)

    assert entry["workspace_count"] == 2
    assert entry["total_spend_usd"] == pytest.approx(4.0)


def test_list_projects_total_spend_matches_project_detail_ledger_burn(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Parity"})
    project_id = create_res.json()["id"]
    ws1 = workspace_mod.create("Active Child", "d", root=ws_root)
    ws2 = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(ws1, project_id)
    _stamp_project_id(ws2, project_id)
    _write_cost(ws1.path, cost=1.25)
    _write_cost(ws2.path, cost=2.75)
    workspace_mod.transition(
        workspace_mod.read(ws2.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    list_res = client.get("/api/projects")
    entry = next(p for p in list_res.json() if p["id"] == project_id)

    detail_res = client.get(f"/api/projects/{project_id}")
    ledger_task_ids = {w["task_id"] for w in detail_res.json()["workspaces"]}

    active = client.get("/api/workspaces").json()
    archived = client.get("/api/workspaces", params={"bucket": "archived"}).json()
    burn = sum(
        w["total_cost_usd"] or 0.0
        for w in [*active, *archived]
        if w["task_id"] in ledger_task_ids
    )
    assert entry["total_spend_usd"] == pytest.approx(burn)


def test_list_projects_workspace_count_matches_project_detail_ledger(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Parity"})
    project_id = create_res.json()["id"]
    ws1 = workspace_mod.create("Active Child", "d", root=ws_root)
    ws2 = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(ws1, project_id)
    _stamp_project_id(ws2, project_id)
    workspace_mod.transition(
        workspace_mod.read(ws2.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    list_res = client.get("/api/projects")
    entry = next(p for p in list_res.json() if p["id"] == project_id)

    detail_res = client.get(f"/api/projects/{project_id}")
    ledger_task_ids = {w["task_id"] for w in detail_res.json()["workspaces"]}

    assert ledger_task_ids == {ws1.task_id, ws2.task_id}
    assert entry["workspace_count"] == len(ledger_task_ids)


def test_list_projects_aggregates_read_index_rows_once_per_request_not_per_project(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace_index import WorkspaceIndex, WorkspaceRow

    client.post("/api/projects", json={"title": "P1"})
    client.post("/api/projects", json={"title": "P2"})
    client.post("/api/projects", json={"title": "P3"})

    calls: list[str] = []
    original_rows = WorkspaceIndex.rows
    original_archived_snapshot = WorkspaceIndex.archived_snapshot

    def counting_rows(self: WorkspaceIndex) -> list[WorkspaceRow]:
        calls.append("rows")
        return original_rows(self)

    def counting_archived_snapshot(self: WorkspaceIndex) -> list[WorkspaceRow]:
        calls.append("archived_snapshot")
        return original_archived_snapshot(self)

    monkeypatch.setattr(WorkspaceIndex, "rows", counting_rows)
    monkeypatch.setattr(WorkspaceIndex, "archived_snapshot", counting_archived_snapshot)

    res = client.get("/api/projects")

    assert res.status_code == 200
    assert len(res.json()) == 3
    assert calls.count("rows") == 1
    assert calls.count("archived_snapshot") == 1


def test_list_projects_empty_after_filtering_skips_index_reads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace_index import WorkspaceIndex, WorkspaceRow

    create_res = client.post("/api/projects", json={"title": "OnlyArchived"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    def boom(self: WorkspaceIndex) -> list[WorkspaceRow]:
        raise AssertionError("index rows must not be read when the project list is empty")

    monkeypatch.setattr(WorkspaceIndex, "rows", boom)
    monkeypatch.setattr(WorkspaceIndex, "archived_snapshot", boom)

    res = client.get("/api/projects")
    assert res.status_code == 200
    assert res.json() == []


def test_get_project_404_for_unknown_id(client: TestClient) -> None:
    res = client.get("/api/projects/nope")
    assert res.status_code == 404


def test_get_project_returns_detail_with_empty_ledger(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Empty Ledger"})
    project_id = create_res.json()["id"]
    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == project_id
    assert body["workspaces"] == []
    assert body["autonomy"] == "propose"
    assert body["budget_usd"] is None


def test_get_project_detail_round_trips_autonomy_and_budget(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Detail Autonomy"})
    project_id = create_res.json()["id"]
    client.patch(
        f"/api/projects/{project_id}",
        json={"autonomy": "auto_continue", "budget_usd": 12.5},
    )

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "auto_continue"
    assert body["budget_usd"] == pytest.approx(12.5)


def test_patch_project_404_for_unknown_id(client: TestClient) -> None:
    res = client.patch("/api/projects/nope", json={"title": "x"})
    assert res.status_code == 404


def test_patch_project_title_only(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Original"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"title": "Renamed"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Renamed"
    assert body["status"] == "active"
    assert body["doc_ref"] is None


def test_patch_project_status_only(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"status": "archived"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "archived"
    assert body["title"] == "P"


def test_patch_project_archived_to_active_ok(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Round Trip"})
    project_id = create_res.json()["id"]
    archived = client.patch(f"/api/projects/{project_id}", json={"status": "archived"})
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    reactivated = client.patch(f"/api/projects/{project_id}", json={"status": "active"})

    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    assert reactivated.json()["title"] == "Round Trip"


def test_patch_project_completed_to_archived_still_blocked(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Shipped"})
    project_id = create_res.json()["id"]
    complete_res = client.post(f"/api/projects/{project_id}/complete")
    assert complete_res.status_code == 200

    res = client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    assert res.status_code == 409
    assert res.json()["detail"] == "project_already_completed"
    reloaded = client.get(f"/api/projects/{project_id}")
    assert reloaded.json()["status"] == "completed"


def test_patch_project_completed_to_active_still_blocked(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Shipped Too"})
    project_id = create_res.json()["id"]
    complete_res = client.post(f"/api/projects/{project_id}/complete")
    assert complete_res.status_code == 200

    res = client.patch(f"/api/projects/{project_id}", json={"status": "active"})

    assert res.status_code == 409
    assert res.json()["detail"] == "project_already_completed"


def test_patch_project_completed_title_only_still_succeeds(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Shipped Rename"})
    project_id = create_res.json()["id"]
    complete_res = client.post(f"/api/projects/{project_id}/complete")
    assert complete_res.status_code == 200

    res = client.patch(f"/api/projects/{project_id}", json={"title": "Renamed After Ship"})

    assert res.status_code == 200
    assert res.json()["title"] == "Renamed After Ship"
    assert res.json()["status"] == "completed"


def test_patch_project_doc_ref_only(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"doc_ref": "docs/y.md"})
    assert res.status_code == 200
    body = res.json()
    assert body["doc_ref"] == "docs/y.md"
    assert body["title"] == "P"


def test_patch_project_no_fields_is_noop(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={})
    assert res.status_code == 200
    assert res.json()["title"] == "P"


def test_patch_project_autonomy_only(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"autonomy": "auto_continue"})
    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "auto_continue"
    assert body["title"] == "P"
    assert body["budget_usd"] is None


def test_patch_project_budget_usd_only(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"budget_usd": 250.5})
    assert res.status_code == 200
    body = res.json()
    assert body["budget_usd"] == pytest.approx(250.5)
    assert body["autonomy"] == "propose"


def test_patch_project_budget_usd_null_clears_it(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 50})
    res = client.patch(f"/api/projects/{project_id}", json={"budget_usd": None})
    assert res.status_code == 200
    assert res.json()["budget_usd"] is None


def test_patch_project_omitted_budget_usd_leaves_it_unchanged(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 75})
    res = client.patch(f"/api/projects/{project_id}", json={"title": "Renamed"})
    assert res.status_code == 200
    assert res.json()["budget_usd"] == pytest.approx(75)


def test_patch_project_rejects_negative_budget_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"budget_usd": -5})
    assert res.status_code == 422


def test_patch_project_rejects_infinite_budget_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(
        f"/api/projects/{project_id}",
        content=b'{"budget_usd": Infinity}',
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 422


def test_patch_project_rejects_nan_budget_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(
        f"/api/projects/{project_id}",
        content=b'{"budget_usd": NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 422


def test_patch_project_unknown_autonomy_rejected_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"autonomy": "bogus"})
    assert res.status_code == 422


def test_patch_project_explicit_null_autonomy_rejected_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"autonomy": None})
    assert res.status_code == 422


def test_patch_project_unknown_status_rejected_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"status": "bogus"})
    assert res.status_code == 422


def test_patch_project_explicit_null_title_rejected_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"title": None})
    assert res.status_code == 422


def test_patch_project_explicit_null_status_rejected_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "P"})
    project_id = create_res.json()["id"]
    res = client.patch(f"/api/projects/{project_id}", json={"status": None})
    assert res.status_code == 422


def _register_bindable_engine(slug: str = "acme/sprocket") -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    owner, repo = slug.split("/")
    register(EngineConfig(repo=RepoIdentity(owner=owner, repo=repo)))


def _write_preflight_result(
    slug: str,
    *,
    overall: Literal["pass", "fail", "unknown"] = "pass",
    checked_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    from alan import engine_config as engine_config_mod
    from alan import engine_preflight as engine_preflight_mod

    cfg = engine_config_mod.lookup_by_slug(slug)
    assert cfg is not None
    forge = engine_config_mod.forge_target_for_workspace(slug, cfg)
    path = engine_preflight_mod._preflight_path(forge)  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    result = engine_preflight_mod.PreflightResult(
        slug=slug, checked_at=checked_at, overall=overall, findings=()
    )
    path.write_text(result.model_dump_json(), encoding="utf-8")


def test_patch_project_engine_key_only_success(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    create_res = client.post("/api/projects", json={"title": "Bind Target"})
    project_id = create_res.json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 200
    body = res.json()
    assert body["engine_key"] == "acme/sprocket"
    assert body["title"] == "Bind Target"


def test_patch_project_engine_key_blocked_by_live_workspaces_409(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    create_res = client.post("/api/projects", json={"title": "Live Blocked"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Active Child", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["detail"] == "project_has_live_workspaces"
    assert body["blocking_workspace_ids"] == [ws.task_id]
    reloaded = client.get(f"/api/projects/{project_id}")
    assert reloaded.json()["engine_key"] is None


def test_patch_project_engine_key_blocking_ids_scoped_to_project(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    target = client.post("/api/projects", json={"title": "Target"}).json()["id"]
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    other_ws = workspace_mod.create("Other Active Child", "d", root=ws_root)
    _stamp_project_id(other_ws, other)

    res = client.patch(f"/api/projects/{target}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 200
    assert res.json()["engine_key"] == "acme/sprocket"


def test_patch_project_engine_key_allowed_when_all_terminal(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    project_id = client.post("/api/projects", json={"title": "All Terminal"}).json()["id"]
    completed_ws = workspace_mod.create("Completed Child", "d", root=ws_root)
    archived_ws = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(completed_ws, project_id)
    _stamp_project_id(archived_ws, project_id)
    workspace_mod.transition(
        workspace_mod.read(completed_ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )
    workspace_mod.transition(
        workspace_mod.read(archived_ws.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 200
    assert res.json()["engine_key"] == "acme/sprocket"


def test_patch_project_engine_key_unbind_to_null_skips_checks(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    create_res = client.post("/api/projects", json={"title": "Unbind Me"})
    project_id = create_res.json()["id"]
    project = project_mod.read(project_id)
    project_mod.update(project, {"engine_key": "acme/unregistered"})
    ws = workspace_mod.create("Still Active", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": None})

    assert res.status_code == 200
    assert res.json()["engine_key"] is None


def test_patch_project_engine_key_unchanged_is_noop(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    create_res = client.post("/api/projects", json={"title": "Already Bound"})
    project_id = create_res.json()["id"]
    project = project_mod.read(project_id)
    project_mod.update(project, {"engine_key": "acme/unregistered"})

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/unregistered"})

    assert res.status_code == 200
    assert res.json()["engine_key"] == "acme/unregistered"


def test_patch_project_engine_key_unknown_engine_400(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    create_res = client.post("/api/projects", json={"title": "Unknown Engine"})
    project_id = create_res.json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "no/such-engine"})

    assert res.status_code == 400
    assert res.json()["detail"] == "unknown engine: 'no/such-engine'"


def test_patch_project_engine_key_preflight_failing_422(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="fail")
    project_id = client.post("/api/projects", json={"title": "Failing Preflight"}).json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 422
    assert res.json()["detail"] == "engine preflight not passing"


def test_patch_project_engine_key_preflight_unknown_422(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="unknown")
    project_id = client.post("/api/projects", json={"title": "Unknown Preflight"}).json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 422


def test_patch_project_engine_key_preflight_never_run_422(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    project_id = client.post("/api/projects", json={"title": "No Preflight Yet"}).json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 422


def test_engine_preflight_passing_false_for_unregistered_engine() -> None:
    from alan.routes.projects import _engine_preflight_passing

    assert _engine_preflight_passing("no/such-engine") is False


def test_patch_project_engine_key_check_ordering_unknown_engine_before_live_workspaces(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    project_id = client.post("/api/projects", json={"title": "Ordering"}).json()["id"]
    ws = workspace_mod.create("Active Child", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "no/such-engine"})

    assert res.status_code == 400


def test_patch_project_intended_engine_key_is_not_a_patch_field_and_skips_rebind_gate_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import engine_config as engine_config_mod

    calls: list[str] = []
    original_lookup = engine_config_mod.lookup_by_slug

    def _spy(slug: str) -> object:
        calls.append(slug)
        return original_lookup(slug)

    monkeypatch.setattr(engine_config_mod, "lookup_by_slug", _spy)
    create_res = client.post(
        "/api/projects",
        json={"title": "Intent Gate Skip", "intended_engine_key": "acme/sprocket"},
    )
    project_id = create_res.json()["id"]

    res = client.patch(
        f"/api/projects/{project_id}", json={"intended_engine_key": "should-be-ignored"}
    )

    assert res.status_code == 200
    assert res.json()["intended_engine_key"] == "acme/sprocket"
    assert calls == []


def test_patch_project_engine_key_still_reaches_engine_rebind_gate_body(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    calls: list[str] = []
    original_lookup = engine_config_mod.lookup_by_slug

    def _spy(slug: str) -> object:
        calls.append(slug)
        return original_lookup(slug)

    monkeypatch.setattr(engine_config_mod, "lookup_by_slug", _spy)
    create_res = client.post("/api/projects", json={"title": "Engine Gate Reached"})
    project_id = create_res.json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    assert res.status_code == 200
    assert calls


def test_get_and_list_project_echo_bound_engine_key(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_bindable_engine("acme/sprocket")
    _write_preflight_result("acme/sprocket", overall="pass")
    project_id = client.post("/api/projects", json={"title": "Echo Engine"}).json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"engine_key": "acme/sprocket"})

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.json()["engine_key"] == "acme/sprocket"

    listing = client.get("/api/projects")
    entry = next(p for p in listing.json() if p["id"] == project_id)
    assert entry["engine_key"] == "acme/sprocket"


def test_delete_project_returns_204_and_removes_dir(
    client: TestClient, projects_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Doomed"})
    project_id = create_res.json()["id"]
    res = client.delete(f"/api/projects/{project_id}")
    assert res.status_code == 204
    assert res.content == b""
    assert not (projects_root / project_id).exists()
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_delete_project_404_for_unknown_id(client: TestClient) -> None:
    res = client.delete("/api/projects/nope")
    assert res.status_code == 404


def test_delete_project_409_when_workspace_references_it(
    client: TestClient, projects_root: Path, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Referenced"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Child", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.delete(f"/api/projects/{project_id}")

    assert res.status_code == 409
    assert res.json()["detail"] == "project_has_workspaces"
    assert (projects_root / project_id).exists()
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.project_id == project_id


def test_delete_project_409_when_referencing_workspace_is_archived(
    client: TestClient, projects_root: Path, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Referenced Archived"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(ws, project_id)
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    res = client.delete(f"/api/projects/{project_id}")

    assert res.status_code == 409
    assert res.json()["detail"] == "project_has_workspaces"
    assert (projects_root / project_id).exists()


def test_delete_project_ignores_workspaces_belonging_to_other_projects(
    client: TestClient, projects_root: Path, ws_root: Path
) -> None:
    proj_a = client.post("/api/projects", json={"title": "A"}).json()["id"]
    proj_b = client.post("/api/projects", json={"title": "B"}).json()["id"]
    ws = workspace_mod.create("In B", "d", root=ws_root)
    _stamp_project_id(ws, proj_b)

    res = client.delete(f"/api/projects/{proj_a}")

    assert res.status_code == 204
    assert not (projects_root / proj_a).exists()
    assert (projects_root / proj_b).exists()


def test_delete_project_purges_only_that_projects_pending_amendments(
    client: TestClient, projects_root: Path
) -> None:
    proj_a = client.post("/api/projects", json={"title": "A"}).json()["id"]
    proj_b = client.post("/api/projects", json={"title": "B"}).json()["id"]
    queue = server.app.state.queue
    fp_a, _rec_a = project_amendment_mod.propose_amendment(
        queue,
        proj_a,
        vision="V",
        scope="S",
        decision_entry="D",
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )
    fp_b, _rec_b = project_amendment_mod.propose_amendment(
        queue,
        proj_b,
        vision="V2",
        scope="S2",
        decision_entry="D2",
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )

    res = client.delete(f"/api/projects/{proj_a}")

    assert res.status_code == 204
    assert project_amendment_mod.get_pending_amendment(queue, fp_a) is None
    assert project_amendment_mod.get_pending_amendment(queue, fp_b) is not None


def test_delete_project_translates_rmtree_race_to_404(
    client: TestClient, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_res = client.post("/api/projects", json={"title": "Racy"})
    project_id = create_res.json()["id"]

    def boom(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(project_id)

    monkeypatch.setattr("alan.project.shutil.rmtree", boom)
    res = client.delete(f"/api/projects/{project_id}")

    assert res.status_code == 404


def test_ledger_orders_by_created_at(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Ordered"})
    project_id = create_res.json()["id"]
    second = workspace_mod.create("Second", "d", root=ws_root)
    first = workspace_mod.create("First", "d", root=ws_root)
    _stamp_meta(first, project_id=project_id, created_at="2020-01-01T00:00:00+00:00")
    _stamp_meta(second, project_id=project_id, created_at="2021-01-01T00:00:00+00:00")

    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    task_ids = [entry["task_id"] for entry in res.json()["workspaces"]]
    assert task_ids == [first.task_id, second.task_id]


def test_ledger_filters_by_project_id(
    client: TestClient, ws_root: Path
) -> None:
    proj_a = client.post("/api/projects", json={"title": "Project A"}).json()["id"]
    proj_b = client.post("/api/projects", json={"title": "Project B"}).json()["id"]
    ws_a = workspace_mod.create("In A", "d", root=ws_root)
    ws_b = workspace_mod.create("In B", "d", root=ws_root)
    workspace_mod.create("Unassigned", "d", root=ws_root)
    _stamp_project_id(ws_a, proj_a)
    _stamp_project_id(ws_b, proj_b)

    res = client.get(f"/api/projects/{proj_a}")
    task_ids = [entry["task_id"] for entry in res.json()["workspaces"]]
    assert task_ids == [ws_a.task_id]


def test_ledger_pr_number_none_when_no_matching_evidence(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "No PR Match"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create(
        "No PR Evidence",
        "d",
        root=ws_root,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[
                    workspace_mod.TaskDoDClause(text="unstamped clause"),
                    workspace_mod.TaskDoDClause(
                        text="merged manually", evidence="merged manually"
                    ),
                ],
            )
        ],
    )
    _stamp_project_id(ws, project_id)

    res = client.get(f"/api/projects/{project_id}")
    ledger = res.json()["workspaces"]
    assert len(ledger) == 1
    assert ledger[0]["pr_number"] is None


def test_derived_ledger_cannot_drift(
    client: TestClient, projects_root: Path, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Spine Project"})
    assert create_res.status_code == 201
    project_id = create_res.json()["id"]
    project_file = projects_root / project_id / "project.json"
    original_bytes = project_file.read_bytes()

    ws1 = workspace_mod.create("Workspace One", "d", root=ws_root)
    ws2 = workspace_mod.create(
        "Workspace Two",
        "d",
        root=ws_root,
        tasks=[
            workspace_mod.Task(
                id="two",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships the feature")],
            )
        ],
    )
    _stamp_project_id(ws1, project_id)
    _stamp_project_id(ws2, project_id)

    workspace_mod.transition(
        workspace_mod.read(ws1.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )
    workspace_mod.record_task_evidence(
        workspace_mod.read(ws2.task_id, root=ws_root), "two", "PR #7"
    )

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    ledger = detail.json()["workspaces"]
    assert len(ledger) == 2
    by_id = {entry["task_id"]: entry for entry in ledger}
    assert by_id[ws1.task_id]["status"] == "completed"
    assert by_id[ws2.task_id]["pr_number"] == 7
    assert by_id[ws1.task_id]["pr_number"] is None

    assert project_file.read_bytes() == original_bytes


def test_get_project_doc_returns_anchors(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Doc Spine"})
    project_id = create_res.json()["id"]

    res = client.get(f"/api/projects/{project_id}/doc")

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"vision", "scope", "decision_log", "discovered_facts"}
    assert body == {
        "vision": "",
        "scope": "",
        "decision_log": "",
        "discovered_facts": "",
    }


def test_get_project_doc_404_for_unknown_project(client: TestClient) -> None:
    res = client.get("/api/projects/nope/doc")
    assert res.status_code == 404


def test_get_project_doc_reflects_an_approved_amendment(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Amended"})
    project_id = create_res.json()["id"]

    propose_res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={
            "vision": "Ship the spine",
            "scope": "In: store. Out: UI.",
            "decision_entry": "Adopted the four-anchor doc.",
        },
    )
    assert propose_res.status_code == 201
    fingerprint = propose_res.json()["fingerprint"]

    approve_res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert approve_res.status_code == 200

    doc_res = client.get(f"/api/projects/{project_id}/doc")
    assert doc_res.status_code == 200
    body = doc_res.json()
    assert body["vision"] == "Ship the spine"
    assert body["scope"] == "In: store. Out: UI."
    assert body["decision_log"] == "Adopted the four-anchor doc."
    assert body["discovered_facts"] == ""


def test_propose_amendment_returns_fingerprint_and_persists_pending_record(
    client: TestClient,
) -> None:
    create_res = client.post("/api/projects", json={"title": "Proposed"})
    project_id = create_res.json()["id"]

    res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={"vision": "V", "scope": "S", "decision_entry": "D"},
    )

    assert res.status_code == 201
    body = res.json()
    assert body["project_id"] == project_id
    assert body["fingerprint"]
    assert body["proposed_at"]

    queue = server.app.state.queue
    pending = project_amendment_mod.get_pending_amendment(queue, body["fingerprint"])
    assert pending is not None
    assert pending.vision == "V"
    assert pending.scope == "S"
    assert pending.decision_entry == "D"


def test_propose_amendment_404_for_unknown_project(client: TestClient) -> None:
    res = client.post(
        "/api/projects/nope/amendments",
        json={"vision": "V", "scope": "S", "decision_entry": "D"},
    )
    assert res.status_code == 404


def test_propose_amendment_409_for_archived_project(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Archived"})
    project_id = create_res.json()["id"]
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={"vision": "V", "scope": "S", "decision_entry": "D"},
    )
    assert res.status_code == 409

    queue = server.app.state.queue
    assert project_amendment_mod.load_pending_amendments(queue) == {}


def test_propose_amendment_rejects_blank_vision_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Blank Vision"})
    project_id = create_res.json()["id"]
    res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={"vision": "", "scope": "S", "decision_entry": "D"},
    )
    assert res.status_code == 422


def test_propose_amendment_rejects_blank_scope_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Blank Scope"})
    project_id = create_res.json()["id"]
    res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={"vision": "V", "scope": "", "decision_entry": "D"},
    )
    assert res.status_code == 422


def test_propose_amendment_rejects_blank_decision_entry_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Blank Decision"})
    project_id = create_res.json()["id"]
    res = client.post(
        f"/api/projects/{project_id}/amendments",
        json={"vision": "V", "scope": "S", "decision_entry": ""},
    )
    assert res.status_code == 422


def test_get_project_ready_to_complete_true_for_empty_ledger(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Empty Ledger Ready"})
    project_id = create_res.json()["id"]
    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    assert res.json()["ready_to_complete"] is True


def test_get_project_ready_to_complete_false_when_workspace_active(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Not Ready"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Active Child", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    assert res.json()["ready_to_complete"] is False


def test_get_project_ready_to_complete_true_when_all_workspaces_terminal(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "All Terminal"})
    project_id = create_res.json()["id"]
    completed_ws = workspace_mod.create("Completed Child", "d", root=ws_root)
    archived_ws = workspace_mod.create("Archived Child", "d", root=ws_root)
    _stamp_project_id(completed_ws, project_id)
    _stamp_project_id(archived_ws, project_id)
    workspace_mod.transition(
        workspace_mod.read(completed_ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )
    workspace_mod.transition(
        workspace_mod.read(archived_ws.task_id, root=ws_root), workspace_mod.Status.ARCHIVED
    )

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    assert res.json()["ready_to_complete"] is True


def test_complete_project_404_for_unknown_id(client: TestClient) -> None:
    res = client.post("/api/projects/nope/complete")
    assert res.status_code == 404


def test_complete_project_succeeds_when_ready_and_stays_listed(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Ready To Ship"})
    project_id = create_res.json()["id"]

    res = client.post(f"/api/projects/{project_id}/complete")

    assert res.status_code == 200
    assert res.json()["status"] == "completed"
    list_res = client.get("/api/projects")
    assert project_id in [p["id"] for p in list_res.json()]


def test_complete_project_409_when_child_workspace_not_terminal(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Not Ready To Ship"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Still Active", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.post(f"/api/projects/{project_id}/complete")

    assert res.status_code == 409
    assert res.json()["detail"] == "project_not_ready"


def test_complete_project_force_true_blank_reason_422(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Forced Blank Reason"})
    project_id = create_res.json()["id"]

    res = client.post(
        f"/api/projects/{project_id}/complete", json={"force": True, "reason": "   "}
    )

    assert res.status_code == 422


def test_complete_project_force_true_with_reason_bypasses_readiness(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Forced Ship"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create("Still Active", "d", root=ws_root)
    _stamp_project_id(ws, project_id)

    res = client.post(
        f"/api/projects/{project_id}/complete",
        json={"force": True, "reason": "shipping despite an open workspace"},
    )

    assert res.status_code == 200
    assert res.json()["status"] == "completed"


def test_complete_project_second_call_409_already_completed(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "Double Complete"})
    project_id = create_res.json()["id"]
    first = client.post(f"/api/projects/{project_id}/complete")
    assert first.status_code == 200

    second = client.post(f"/api/projects/{project_id}/complete")

    assert second.status_code == 409
    assert second.json()["detail"] == "project_already_completed"


def test_complete_project_writes_summary_into_decision_log(
    client: TestClient, ws_root: Path
) -> None:
    create_res = client.post("/api/projects", json={"title": "Summary Ship"})
    project_id = create_res.json()["id"]
    ws = workspace_mod.create(
        "Shipped Task",
        "d",
        root=ws_root,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships", evidence="PR #42")],
            )
        ],
    )
    _stamp_project_id(ws, project_id)
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )
    before_doc = client.get(f"/api/projects/{project_id}/doc").json()

    complete_res = client.post(f"/api/projects/{project_id}/complete")
    assert complete_res.status_code == 200

    after_doc = client.get(f"/api/projects/{project_id}/doc").json()
    assert after_doc["discovered_facts"] == before_doc["discovered_facts"]
    assert after_doc["vision"] == before_doc["vision"]
    assert "Shipped Task" in after_doc["decision_log"]
    assert "PR #42" in after_doc["decision_log"]


def test_patch_project_rejects_status_completed_400(client: TestClient) -> None:
    create_res = client.post("/api/projects", json={"title": "No Backdoor"})
    project_id = create_res.json()["id"]

    res = client.patch(f"/api/projects/{project_id}", json={"status": "completed"})

    assert res.status_code == 400
    reloaded = client.get(f"/api/projects/{project_id}")
    assert reloaded.json()["status"] == "active"


_ROADMAP_MARKDOWN = "## First Item\nBody one.\n\n## Second Item\nBody two.\n"
_ROADMAP_MARKDOWN_3 = (
    "## Kickoff\nBody zero.\n\n## First Item\nBody one.\n\n## Second Item\nBody two.\n"
)


def _deliver_roadmap_item(ws_root: Path, project_id: str, item_id: str) -> workspace_mod.Workspace:
    ws = workspace_mod.create(
        "Delivering Work",
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships")],
            )
        ],
    )
    workspace_mod.record_task_evidence(ws, "solo", "PR #1")
    handover_mod.save_handover(
        ws,
        handover_mod.Handover(
            handover_id="h1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="done",
            pr_number=1,
        ),
    )
    return workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )


def test_preview_roadmap_parses_without_persisting(
    client: TestClient, projects_root: Path
) -> None:
    project_id = _create_project(client)

    res = _preview_roadmap(client, project_id, _ROADMAP_MARKDOWN)

    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["title"] for i in items] == ["First Item", "Second Item"]
    assert [i["text"] for i in items] == ["Body one.", "Body two."]
    assert not (projects_root / project_id / "roadmap.json").exists()

    again = _preview_roadmap(client, project_id, _ROADMAP_MARKDOWN)
    assert again.status_code == 200
    assert not (projects_root / project_id / "roadmap.json").exists()


def test_preview_roadmap_404_for_unknown_project(client: TestClient) -> None:
    res = _preview_roadmap(client, "does-not-exist", _ROADMAP_MARKDOWN)
    assert res.status_code == 404


def test_import_roadmap_201_seeds_ordered_items(client: TestClient, projects_root: Path) -> None:
    project_id = _create_project(client)

    res = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)

    assert res.status_code == 201
    items = res.json()["items"]
    assert [i["title"] for i in items] == ["First Item", "Second Item"]
    for item in items:
        assert item["id"].startswith("itm_")
    assert (projects_root / project_id / "roadmap.json").is_file()


def test_import_roadmap_404_for_unknown_project(client: TestClient) -> None:
    res = _import_roadmap(client, "does-not-exist", _ROADMAP_MARKDOWN)
    assert res.status_code == 404


def test_import_roadmap_second_call_409_and_does_not_duplicate(
    client: TestClient, projects_root: Path
) -> None:
    project_id = _create_project(client)
    first = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)
    assert first.status_code == 201

    second = _import_roadmap(client, project_id, "## Different\nOther.\n")

    assert second.status_code == 409
    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    assert [r["title"] for r in roadmap] == ["First Item", "Second Item"]


def test_get_project_roadmap_empty_before_import(client: TestClient) -> None:
    project_id = _create_project(client)
    res = client.get(f"/api/projects/{project_id}")
    assert res.json()["roadmap"] == []


def test_get_project_roadmap_reflects_order_and_defaults_not_started(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    assert [r["order"] for r in roadmap] == [0, 1]
    assert [r["status"] for r in roadmap] == ["not_started", "not_started"]
    assert all(r["merged_prs"] == [] for r in roadmap)


def test_get_project_roadmap_in_flight_when_stamped_workspace_is_active(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    workspace_mod.create(
        "Active Work", "d", root=ws_root, project_id=project_id, roadmap_item_id=item_id
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["status"] == "in_flight"


def test_get_project_roadmap_delivered_with_merged_pr(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    ws = workspace_mod.create(
        "Delivering Work",
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships")],
            )
        ],
    )
    workspace_mod.record_task_evidence(ws, "solo", "PR #99")
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["status"] == "delivered"
    assert entry["merged_prs"] == [99]


def test_get_project_roadmap_ignores_workspace_stamped_for_different_project(
    client: TestClient, ws_root: Path
) -> None:
    project_a = _create_project(client, "Project A")
    project_b = _create_project(client, "Project B")
    items = _import_roadmap(client, project_a, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    workspace_mod.create(
        "Cross Project", "d", root=ws_root, project_id=project_b, roadmap_item_id=item_id
    )

    roadmap = client.get(f"/api/projects/{project_a}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["status"] == "not_started"


def test_get_project_roadmap_status_never_persisted_to_roadmap_json(
    client: TestClient, projects_root: Path, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    ws = workspace_mod.create(
        "Delivering Work",
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships")],
            )
        ],
    )
    workspace_mod.record_task_evidence(ws, "solo", "PR #7")
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )
    client.get(f"/api/projects/{project_id}")

    raw = json.loads((projects_root / project_id / "roadmap.json").read_text(encoding="utf-8"))
    for raw_item in raw:
        assert "status" not in raw_item
        assert set(raw_item) == {"id", "title", "text"}


def test_get_project_roadmap_pr_zero_is_delivered(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    ws = workspace_mod.create(
        "PR Zero Delivery",
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[workspace_mod.TaskDoDClause(text="ships")],
            )
        ],
    )
    workspace_mod.record_task_evidence(ws, "solo", "PR #0")
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["status"] == "delivered"
    assert entry["merged_prs"] == [0]


def test_get_project_roadmap_merged_prs_includes_both_refs_in_one_clause(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]
    ws = workspace_mod.create(
        "Two Refs Delivering",
        "d",
        root=ws_root,
        project_id=project_id,
        roadmap_item_id=item_id,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[
                    workspace_mod.TaskDoDClause(
                        text="ships", evidence="superseded PR #1, landed as PR #2"
                    )
                ],
            )
        ],
    )
    workspace_mod.transition(
        workspace_mod.read(ws.task_id, root=ws_root), workspace_mod.Status.COMPLETED
    )

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["merged_prs"] == [1, 2]


def test_ledger_pr_number_picks_last_of_two_refs_in_one_clause(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client, "Two Refs Ledger")
    ws = workspace_mod.create(
        "Two Refs In One Clause",
        "d",
        root=ws_root,
        tasks=[
            workspace_mod.Task(
                id="solo",
                status=workspace_mod.TaskStatus.READY,
                dod_clauses=[
                    workspace_mod.TaskDoDClause(
                        text="ships", evidence="superseded PR #1, landed as PR #2"
                    )
                ],
            )
        ],
    )
    _stamp_project_id(ws, project_id)

    ledger = client.get(f"/api/projects/{project_id}").json()["workspaces"]
    assert len(ledger) == 1
    assert ledger[0]["pr_number"] == 2


def test_list_projects_roadmap_totals_zero_when_no_roadmap_imported(client: TestClient) -> None:
    _create_project(client, "No Roadmap")
    entry = client.get("/api/projects").json()[0]
    assert entry["roadmap_items_total"] == 0
    assert entry["roadmap_items_delivered"] == 0


def test_list_projects_roadmap_totals_matches_project_detail_roadmap(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    _deliver_roadmap_item(ws_root, project_id, items[0]["id"])

    list_res = client.get("/api/projects")
    entry = next(p for p in list_res.json() if p["id"] == project_id)

    detail_res = client.get(f"/api/projects/{project_id}")
    roadmap = detail_res.json()["roadmap"]

    assert entry["roadmap_items_total"] == len(roadmap) == 2
    assert entry["roadmap_items_delivered"] == sum(
        1 for r in roadmap if r["status"] == "delivered"
    ) == 1


def test_list_projects_roadmap_totals_all_delivered(client: TestClient, ws_root: Path) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, "## Only Item\nBody.\n").json()["items"]
    _deliver_roadmap_item(ws_root, project_id, items[0]["id"])

    entry = next(p for p in client.get("/api/projects").json() if p["id"] == project_id)
    assert entry["roadmap_items_total"] == 1
    assert entry["roadmap_items_delivered"] == 1


def test_propose_roadmap_reorder_returns_fingerprint(client: TestClient) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    ids = [i["id"] for i in items]

    res = client.post(
        f"/api/projects/{project_id}/roadmap/reorder", json={"ordered_ids": list(reversed(ids))}
    )

    assert res.status_code == 201
    body = res.json()
    assert body["project_id"] == project_id
    assert len(body["fingerprint"]) == 16


def test_propose_roadmap_reorder_404_unknown_project(client: TestClient) -> None:
    res = client.post(
        "/api/projects/does-not-exist/roadmap/reorder", json={"ordered_ids": ["itm_1"]}
    )
    assert res.status_code == 404


def test_propose_roadmap_reorder_409_when_project_archived(client: TestClient) -> None:
    project_id = _create_project(client)
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    res = client.post(
        f"/api/projects/{project_id}/roadmap/reorder", json={"ordered_ids": ["itm_1"]}
    )

    assert res.status_code == 409


def test_propose_roadmap_edit_returns_fingerprint(client: TestClient) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]

    res = client.post(
        f"/api/projects/{project_id}/roadmap/items/{item_id}", json={"title": "Renamed"}
    )

    assert res.status_code == 201
    body = res.json()
    assert body["project_id"] == project_id
    assert len(body["fingerprint"]) == 16


def test_propose_roadmap_edit_text_only_omits_title_from_payload(client: TestClient) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    item_id = items[0]["id"]

    res = client.post(
        f"/api/projects/{project_id}/roadmap/items/{item_id}", json={"text": "New body"}
    )
    assert res.status_code == 201
    fingerprint = res.json()["fingerprint"]

    approve_res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert approve_res.status_code == 200

    roadmap = client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["title"] == "First Item"
    assert entry["text"] == "New body"


def test_propose_roadmap_edit_rejects_body_with_neither_title_nor_text(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    res = client.post(f"/api/projects/{project_id}/roadmap/items/itm_1", json={})
    assert res.status_code == 422


def test_propose_roadmap_edit_404_unknown_project(client: TestClient) -> None:
    res = client.post(
        "/api/projects/does-not-exist/roadmap/items/itm_1", json={"title": "X"}
    )
    assert res.status_code == 404


def test_propose_roadmap_edit_409_when_project_archived(client: TestClient) -> None:
    project_id = _create_project(client)
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    res = client.post(
        f"/api/projects/{project_id}/roadmap/items/itm_1", json={"title": "X"}
    )

    assert res.status_code == 409


def test_get_next_item_proposal_404_unknown_project(client: TestClient) -> None:
    res = client.get("/api/projects/does-not-exist/roadmap/next-item-proposal")
    assert res.status_code == 404


def test_get_next_item_proposal_null_when_no_roadmap(client: TestClient) -> None:
    project_id = _create_project(client)
    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")
    assert res.status_code == 200
    assert res.json()["proposal"] is None


def test_get_next_item_proposal_returns_direct_dispatch_card(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    _deliver_roadmap_item(ws_root, project_id, items[0]["id"])

    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")

    assert res.status_code == 200
    proposal = res.json()["proposal"]
    assert proposal is not None
    assert proposal["type"] == "decision"
    assert proposal["project_id"] == project_id
    assert "Second Item" in proposal["title"]
    action = proposal["actions"][0]
    assert action["kind"] == "direct_dispatch"
    assert action["url"] == f"/api/projects/{project_id}/roadmap/advance"


def test_get_next_item_proposal_null_for_auto_continue_project(client: TestClient) -> None:
    project_id = _create_project(client)
    _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)
    client.patch(f"/api/projects/{project_id}", json={"autonomy": "auto_continue"})

    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")

    assert res.status_code == 200
    assert res.json()["proposal"] is None


def test_get_next_item_proposal_409_when_project_archived(client: TestClient) -> None:
    project_id = _create_project(client)
    _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")

    assert res.status_code == 409


def test_get_next_item_proposal_budget_state_normal_by_default(client: TestClient) -> None:
    project_id = _create_project(client)
    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")
    assert res.status_code == 200
    assert res.json()["budget_state"] == "normal"


def test_get_next_item_proposal_budget_state_approaching(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=85.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 100.0})

    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")

    assert res.status_code == 200
    body = res.json()
    assert body["budget_state"] == "approaching"
    assert body["proposal"] is not None


def test_get_next_item_proposal_budget_state_paused_nulls_proposal(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=100.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 1.0})

    res = client.get(f"/api/projects/{project_id}/roadmap/next-item-proposal")

    assert res.status_code == 200
    body = res.json()
    assert body["budget_state"] == "paused"
    assert body["proposal"] is None


def test_advance_project_roadmap_409_budget_paused_detail_distinct_from_no_next_item(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=100.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 1.0})

    budget_res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert budget_res.status_code == 409
    budget_detail = budget_res.json()["detail"]

    no_item_project_id = _create_project(client, title="No Roadmap")
    no_item_res = client.post(f"/api/projects/{no_item_project_id}/roadmap/advance")

    assert no_item_res.status_code == 409
    assert no_item_res.json()["detail"] != budget_detail


def test_advance_project_roadmap_409_budget_paused_writes_no_workspace_or_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    from alan.project_auto_continue import PROJECT_ADVANCE_AUDIT_FILE

    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=100.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 1.0})

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 409
    assert [ws.task_id for ws in workspace_mod.list_workspaces(root=ws_root)] == [prior.task_id]
    assert not (logs_dir / PROJECT_ADVANCE_AUDIT_FILE).exists()


def test_advance_project_roadmap_dispatches_when_budget_has_headroom(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=1.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 100.0})

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 201


def test_advance_project_roadmap_archived_check_precedes_budget_check(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    prior = _deliver_roadmap_item(ws_root, project_id, items[0]["id"])
    _write_cost(prior.path, cost=100.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 1.0, "status": "archived"})

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 409
    assert res.json()["detail"] == "project is archived"


def test_get_project_budget_normal_with_no_spend(client: TestClient) -> None:
    project_id = _create_project(client)

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    budget = res.json()["budget"]
    assert budget["state"] == "normal"
    assert budget["burn_usd"] == pytest.approx(0.0)
    assert budget["budget_usd"] is None
    assert budget["remaining_usd"] is None


def test_get_project_budget_paused_when_over_budget(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    ws = workspace_mod.create("Spendy", "d", root=ws_root, project_id=project_id)
    _write_cost(ws.path, cost=10.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 1.0})

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    budget = res.json()["budget"]
    assert budget["state"] == "paused"
    assert budget["burn_usd"] == pytest.approx(10.0)
    assert budget["budget_usd"] == pytest.approx(1.0)
    assert budget["remaining_usd"] == pytest.approx(-9.0)


def test_get_project_budget_approaching_below_hard_limit(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    ws = workspace_mod.create("Nearing", "d", root=ws_root, project_id=project_id)
    _write_cost(ws.path, cost=85.0)
    client.patch(f"/api/projects/{project_id}", json={"budget_usd": 100.0})

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    assert res.json()["budget"]["state"] == "approaching"


def test_get_project_budget_null_when_burn_resolution_fails_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import project as project_mod_real

    project_id = _create_project(client)
    monkeypatch.setattr(project_mod_real, "cached_read", lambda *a, **kw: None)

    res = client.get(f"/api/projects/{project_id}")

    assert res.status_code == 200
    assert res.json()["budget"] is None


def test_advance_project_roadmap_404_unknown_project(client: TestClient) -> None:
    res = client.post("/api/projects/does-not-exist/roadmap/advance")
    assert res.status_code == 404


def test_advance_project_roadmap_409_when_no_roadmap(client: TestClient) -> None:
    project_id = _create_project(client)
    res = client.post(f"/api/projects/{project_id}/roadmap/advance")
    assert res.status_code == 409


def test_advance_project_roadmap_409_when_project_archived(client: TestClient) -> None:
    project_id = _create_project(client)
    _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)
    client.patch(f"/api/projects/{project_id}", json={"status": "archived"})

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 409


def test_advance_project_roadmap_409_on_fresh_project_no_prior_work(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    _import_roadmap(client, project_id, _ROADMAP_MARKDOWN)

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 409


def test_advance_project_roadmap_dispatches_next_item_after_prior_delivered(
    client: TestClient, ws_root: Path
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN).json()["items"]
    _deliver_roadmap_item(ws_root, project_id, items[0]["id"])

    res = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert res.status_code == 201
    body = res.json()
    assert body["item_id"] == items[1]["id"]
    assert body["item_title"] == "Second Item"
    ws = workspace_mod.read(body["task_id"], root=ws_root)
    assert ws.meta.project_id == project_id
    assert ws.meta.roadmap_item_id == items[1]["id"]
    assert ws.meta.description == "Body two."


def test_advance_project_roadmap_repeat_taps_move_through_then_exhaust_roadmap(
    client: TestClient, ws_root: Path,
) -> None:
    project_id = _create_project(client)
    items = _import_roadmap(client, project_id, _ROADMAP_MARKDOWN_3).json()["items"]
    _deliver_roadmap_item(ws_root, project_id, items[0]["id"])

    first = client.post(f"/api/projects/{project_id}/roadmap/advance")
    second = client.post(f"/api/projects/{project_id}/roadmap/advance")
    third = client.post(f"/api/projects/{project_id}/roadmap/advance")

    assert first.status_code == 201
    assert first.json()["item_id"] == items[1]["id"]
    assert second.status_code == 201
    assert second.json()["item_id"] == items[2]["id"]
    assert third.status_code == 409

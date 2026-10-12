from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.dispatch as dispatch_mod
import alan.project_amendment as project_amendment_mod
from alan import server
from alan import workspace as workspace_mod
from alan.app import workspace_creation
from alan.app.workspace_create_models import (
    UNCOVERED_CLAUSES_DETAIL,
    CreateWorkspaceBody,
    TaskCreateEntry,
    reject_uncovered_clauses,
    reject_unseedable_teams,
)
from alan.dispatch import (
    OWNERLESS_DECOMPOSED_DETAIL,
    assert_team_dispatchable,
    owning_task_id,
    resolver_team_for_question,
)
from alan.engine_config import EngineConfig, Posture, RepoIdentity, register
from alan.errors import AppError
from alan.handover import Handover
from alan.workspace import (
    EngineSnapshot,
    Requirement,
    Status,
    Task,
    TaskStatus,
    stamp_engine_snapshot,
)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "projects"))
    with TestClient(server.app) as c:
        yield c


def _propose(project_id: str, **fields: Any) -> str:
    fingerprint, _record = project_amendment_mod.propose_amendment(
        server.app.state.queue, project_id, now=datetime.now(UTC), **fields
    )
    return fingerprint


def _create_project(client: TestClient) -> str:
    res = client.post("/api/projects", json={"title": "Raise Sites"})
    assert res.status_code == 201
    project_id: str = res.json()["id"]
    return project_id


def test_dispatch_owning_task_id_ownerless_decomposed_raises_404(ws_root: Path) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1")]
    ws = workspace_mod.create("Ownerless", "d", root=ws_root, tasks=tasks)
    blocking = Handover(
        handover_id="h9",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
    )
    with pytest.raises(AppError) as exc_info:
        owning_task_id(ws, ws.task_id, blocking=blocking)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == OWNERLESS_DECOMPOSED_DETAIL.format(
        task_id=ws.task_id, count=0
    )


def test_dispatch_resolver_team_for_question_unmapped_class_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        resolver_team_for_question("not_mapped", "implementation")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "no resolver mapped for question_class 'not_mapped'"


def test_dispatch_assert_task_known_unknown_task_raises_404(ws_root: Path) -> None:
    ws = workspace_mod.create("Unknown Task", "d", root=ws_root)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, "ghost", "implementation")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == f"task 'ghost' not found in workspace {ws.task_id!r}"


def test_dispatch_assert_team_dispatchable_archived_raises_409(ws_root: Path) -> None:
    ws = workspace_mod.create("Archived", "d", root=ws_root)
    ws = workspace_mod.transition(ws, Status.ARCHIVED)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "workspace is archived"


def test_dispatch_assert_posture_admits_withheld_team_raises_422(ws_root: Path) -> None:
    ws = workspace_mod.create("Posture", "d", root=ws_root)
    ws = stamp_engine_snapshot(ws, EngineSnapshot(posture=Posture.observe, transition_graph={}))
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        f"team 'implementation' is not admitted at posture {Posture.observe!r}"
    )


def test_dispatch_assert_on_roster_off_roster_team_raises_422(ws_root: Path) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"implementation": ["verification"], "verification": []},
        )
    )
    ws = workspace_mod.create("Roster", "d", root=ws_root, engine_key="acme/sprocket")
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "opportunity_scout")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == (
        "team 'opportunity_scout' is not on the roster for engine 'acme/sprocket'"
    )


def test_dispatch_assert_fix_ceiling_reached_raises_409(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("Ceiling", "d", root=ws_root)
    monkeypatch.setattr(
        dispatch_mod, "prior_handover_for_task", lambda *_a: SimpleNamespace(pr_number=7)
    )
    monkeypatch.setattr(dispatch_mod, "fix_cycle_ceiling_reached", lambda *_a: True)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation_fix")
    assert exc_info.value.status_code == 409
    assert str(exc_info.value.detail).startswith("fix-cycle ceiling reached for PR #7;")


def test_project_amendment_require_valid_fingerprint_raises_422() -> None:
    with pytest.raises(AppError) as exc_info:
        project_amendment_mod.require_valid_fingerprint("NOT-HEX")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "fingerprint must be a 16-character lowercase hex string"


def test_server_plan_endpoint_blank_body_returns_400(client: TestClient) -> None:
    res = client.post("/api/plan", json={"title": " ", "description": ""})
    assert res.status_code == 400
    assert res.json() == {"detail": "title or description required"}


def test_server_approve_amendment_unknown_fingerprint_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    res = client.post(f"/api/projects/{project_id}/amendments/{'a' * 16}/approve")
    assert res.status_code == 404
    assert res.json() == {"detail": "no pending amendment for this project/fingerprint"}


def test_server_approve_amendment_for_another_project_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    fingerprint = _propose(project_id, vision="v", scope="s", decision_entry="d")
    res = client.post(f"/api/projects/not-{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 404
    assert res.json() == {"detail": "no pending amendment for this project/fingerprint"}


def test_server_approve_amendment_lost_claim_race_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _create_project(client)
    fingerprint = _propose(project_id, vision="v", scope="s", decision_entry="d")
    monkeypatch.setattr(project_amendment_mod, "claim_pending_amendment", lambda *_a: None)
    res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 404
    assert res.json() == {"detail": "no pending amendment for this project/fingerprint"}


def test_server_approve_amendment_malformed_roadmap_payload_returns_409(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    fingerprint = _propose(project_id, kind="roadmap_reorder", payload={"ordered_ids": 5})
    res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 409
    assert res.json() == {"detail": "malformed roadmap amendment payload"}


def test_create_models_reject_uncovered_clauses_raises_400_with_structured_detail() -> None:
    requirement = Requirement(text="clause a")
    body = CreateWorkspaceBody(title="t", requirements=[requirement])
    tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
    with pytest.raises(AppError) as exc_info:
        reject_uncovered_clauses(body, tasks)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "error": UNCOVERED_CLAUSES_DETAIL,
        "uncovered_clause_ids": [requirement.id],
    }


def test_create_models_unknown_seed_team_raises_400() -> None:
    body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team="ghost-team")])
    with pytest.raises(AppError) as exc_info:
        reject_unseedable_teams(body, None)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "task 'a' names unknown team 'ghost-team'"


def test_create_models_internal_only_seed_team_raises_400() -> None:
    body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team="brief_refine")])
    with pytest.raises(AppError) as exc_info:
        reject_unseedable_teams(body, None)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "task 'a' names team 'brief_refine' which is not manually dispatchable "
        "(internal-only team)"
    )


def test_create_models_decomposer_reaching_seed_team_raises_400() -> None:
    body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team="research")])
    with pytest.raises(AppError) as exc_info:
        reject_unseedable_teams(body, None)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "task 'a' names team 'research' which can reach the decomposer step "
        "and would re-plan the whole item"
    )


def test_workspace_creation_unknown_team_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        workspace_creation.resolve_dispatchable_team("ghost-team")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "unknown team: ghost-team"


def test_workspace_creation_internal_team_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        workspace_creation.resolve_dispatchable_team("decomposer")
    assert exc_info.value.status_code == 400
    assert str(exc_info.value.detail).startswith("team 'decomposer' is internal;")


def test_workspace_creation_unknown_engine_key_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        workspace_creation.resolve_engine_key(
            "no/such-engine", None, engine_key_provided=True
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "unknown engine: 'no/such-engine'"

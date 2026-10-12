from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

import alan.dispatch_proposal as dispatch_proposal_mod
import alan.routes.answer as answer_routes
import alan.routes.build_chat as build_chat_routes
import alan.routes.explore_chat as explore_chat_routes
import alan.routes.workspaces as workspaces_routes
import alan.server as server_mod
from alan import workspace as workspace_mod
from alan.app.workspace_create_models import CreateWorkspaceBody
from alan.engine_config import Posture
from alan.events import PubSub
from alan.queue import Queue
from alan.router import TEAM_TRANSITION_POLICY
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot

HUMAN_DISPATCH_DOORS: dict[str, Callable[..., Any]] = {
    "build_chat.propose_build_dispatch": build_chat_routes.propose_build_dispatch,
    "explore_chat.propose_explore_dispatch": explore_chat_routes.propose_explore_dispatch,
}


def test_every_human_dispatch_door_funnels_through_the_shared_gated_path() -> None:
    for name, fn in HUMAN_DISPATCH_DOORS.items():
        source = inspect.getsource(fn)
        assert "run_dispatch_proposal" in source, (
            f"{name} must call _chat_router.run_dispatch_proposal — the ONE "
            "proposal-generation path (counter_case + scope_audit scan)"
        )


def test_dispatch_proposal_ready_collision_field_has_a_working_default() -> None:
    import alan.routes._chat_router as chat_router_mod

    field = chat_router_mod.DispatchProposalReady.model_fields["collision"]
    assert not field.is_required()


def test_dispatch_proposal_counter_case_is_required() -> None:
    field = dispatch_proposal_mod.DispatchProposal.model_fields["counter_case"]
    assert field.is_required(), (
        "counter_case must be non-optional on DispatchProposal — a status: "
        "ready proposal missing it must 422, not silently dispatch ungated"
    )


def test_only_two_human_dispatch_doors_are_registered() -> None:
    assert set(HUMAN_DISPATCH_DOORS) == {
        "build_chat.propose_build_dispatch",
        "explore_chat.propose_explore_dispatch",
    }


NON_GATED_RAW_ROUTES_RATIONALE = {
    "workspaces.dispatch_task_team": (
        "the raw dispatch endpoint (POST .../tasks/{id}/dispatch) — the gate "
        "lives at the ACTION layer (the chat propose flow that mints the "
        "counter_case shown to the human); recovery tooling and scripted "
        "dispatch must still be able to call this directly without a chat turn"
    ),
    "workspaces.create_workspace": (
        "raw workspace creation (POST /api/workspaces) — an optional `team` "
        "field lets this ONE call atomically dispatch the root task too, "
        "via the exact same dispatch_team_core + waiver path as the raw "
        "dispatch endpoint (a rollback guard deletes the workspace on any "
        "dispatch failure, so no orphan survives); the gate still lives at "
        "the ACTION layer (the chat propose flow that mints the "
        "counter_case shown to the human), so counter_case stays optional "
        "here exactly like DispatchBody — recovery tooling and scripted "
        "create-and-dispatch must still be able to call this directly "
        "without a chat turn. scan_predispatch runs exactly once per call "
        "either way, defensively, which is unconditional best-effort "
        "logging, not the commitment gate"
    ),
}


def test_non_gated_raw_routes_are_documented_and_stable() -> None:
    assert set(NON_GATED_RAW_ROUTES_RATIONALE) == {
        "workspaces.dispatch_task_team",
        "workspaces.create_workspace",
    }
    for name, rationale in NON_GATED_RAW_ROUTES_RATIONALE.items():
        assert len(rationale) > 20, f"{name} needs a real rationale, not a stub"


def test_raw_dispatch_body_counter_case_is_optional_not_required() -> None:
    field = workspaces_routes.DispatchBody.model_fields["counter_case"]
    assert not field.is_required()


def test_create_workspace_body_counter_case_is_optional_not_required() -> None:
    field = CreateWorkspaceBody.model_fields["counter_case"]
    assert not field.is_required()


OUT_OF_SCOPE_SYSTEM_ROUTES_RATIONALE = {
    "answer_question": (
        "resolves a blocking question on an already-dispatched task; not a "
        "new-scope commit"
    ),
    "complete_out_of_band_task": (
        "marks an existing task complete from an out-of-band PR; no team is "
        "freshly dispatched"
    ),
    "decomposer_injected_tasks": (
        "internal split of an already-approved workspace's scope; "
        "system-derived, not a fresh human commit"
    ),
    "dependency_sweep": (
        "autonomous weekly sweep; INTERNAL_ONLY_TEAMS and not "
        "user-dispatchable by construction"
    ),
}


def test_out_of_scope_system_routes_are_documented() -> None:
    assert len(OUT_OF_SCOPE_SYSTEM_ROUTES_RATIONALE) == 4
    for name, rationale in OUT_OF_SCOPE_SYSTEM_ROUTES_RATIONALE.items():
        assert len(rationale) > 10, f"{name} needs a real rationale, not a stub"


def test_answer_question_out_of_scope_reference_is_real_and_counter_case_free() -> None:
    assert "answer_question" in OUT_OF_SCOPE_SYSTEM_ROUTES_RATIONALE
    assert inspect.isfunction(answer_routes.answer_question)
    assert answer_routes.answer_question.__name__ == "answer_question"
    assert "counter_case" not in answer_routes.AnswerBody.model_fields


def _ws_with_posture(root: Path, posture: Posture) -> Workspace:
    ws = workspace_mod.create("ui_review dispatch gate", "d", root=root)
    snapshot = EngineSnapshot(
        posture=posture,
        transition_graph={k: list(v) for k, v in TEAM_TRANSITION_POLICY.items()},
    )
    return stamp_engine_snapshot(ws, snapshot)


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server_mod.app.dependency_overrides[server_mod._queue_dep] = lambda: queue
    server_mod.app.dependency_overrides[server_mod._pubsub_dep] = lambda: pubsub
    server_mod.app.dependency_overrides[server_mod._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server_mod.app, follow_redirects=True) as c:
            yield c
    finally:
        server_mod.app.dependency_overrides.clear()


def test_ui_review_dispatch_admitted_at_propose_posture(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.propose)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "ui_review"},
    )
    assert res.status_code == 202
    assert queue.list_jobs()


def test_ui_review_dispatch_rejected_below_propose_posture(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.observe)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "ui_review"},
    )
    assert res.status_code == 422
    assert "ui_review" in res.json()["detail"]
    assert not queue.list_jobs()

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
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.events import PubSub
from alan.handover import Handover, list_handovers, save_handover
from alan.queue import Queue
from alan.router import RouterDecision
from alan.routes.answer import _maybe_grant_fix_cycle_override
from alan.teams import TEAMS as _REAL_TEAMS
from alan.teams import _pr_state_paths
from alan.teams._spiral_handover import (
    FIX_CYCLE_HAND_RESOLVE_OPTION,
    FIX_CYCLE_OVERRIDE_OPTION,
    fix_cycle_ceiling_question,
)
from alan.workspace import Status, Workspace, create, read


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


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "answer-fix-cycle-state"))


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def _ceiling_handover(ws: Workspace, pr_number: int, count: int) -> Handover:
    question = fix_cycle_ceiling_question("inv_ceiling1", count, "coverage gap")
    return Handover(
        handover_id=f"handover_{ws.task_id}_ceiling",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_ceiling1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="fix-cycle ceiling reached",
        pr_number=pr_number,
        open_questions=[question],
    )


def test_override_option_grants_allowance_and_dispatches_fix(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Fix Ceiling Override", "d", root=ws_root)
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(701, DEFAULT_FORGE_TARGET)
    handover = _ceiling_handover(ws, 701, 6)
    save_handover(ws, handover)

    res = _post_answer(
        client, ws,
        {"question_id": handover.open_questions[0].id, "chosen_option": FIX_CYCLE_OVERRIDE_OPTION},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "implementation_fix"

    state = _pr_state_paths.load_fix_cycle_state(701, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT
    assert state.count == 7


def test_hand_resolve_option_dispatches_verification_without_granting(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Fix Ceiling Hand Resolve", "d", root=ws_root)
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(702, DEFAULT_FORGE_TARGET)
    handover = _ceiling_handover(ws, 702, 6)
    save_handover(ws, handover)

    res = _post_answer(
        client, ws,
        {
            "question_id": handover.open_questions[0].id,
            "chosen_option": FIX_CYCLE_HAND_RESOLVE_OPTION,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"

    state = _pr_state_paths.load_fix_cycle_state(702, DEFAULT_FORGE_TARGET)
    assert state.allowance == 0


def test_override_replay_does_not_double_grant(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Fix Ceiling Override Replay", "d", root=ws_root)
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(703, DEFAULT_FORGE_TARGET)
    handover = _ceiling_handover(ws, 703, 6)
    save_handover(ws, handover)

    body: dict[str, object] = {
        "question_id": handover.open_questions[0].id,
        "chosen_option": FIX_CYCLE_OVERRIDE_OPTION,
    }
    first = _post_answer(client, ws, body)
    assert first.status_code == 200
    second = _post_answer(client, ws, body)
    assert second.status_code == 200
    assert second.json()["already_answered"] is True

    state = _pr_state_paths.load_fix_cycle_state(703, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT


def test_maybe_grant_fix_cycle_override_skips_without_pr_number(ws_root: Path) -> None:
    ws = create("Fix Ceiling No PR", "d", root=ws_root)
    question = fix_cycle_ceiling_question("inv_ceiling1", 6, "coverage gap")
    blocking = _ceiling_handover(ws, 704, 6).model_copy(update={"pr_number": None})
    _maybe_grant_fix_cycle_override(
        ws=ws, question=question, blocking=blocking, answer=FIX_CYCLE_OVERRIDE_OPTION
    )
    assert not _pr_state_paths.fix_cycle_path(704, DEFAULT_FORGE_TARGET).exists()


def test_maybe_grant_fix_cycle_override_direct_replay_is_idempotent(ws_root: Path) -> None:
    ws = create("Fix Ceiling Direct Replay", "d", root=ws_root)
    question = fix_cycle_ceiling_question("inv_ceiling1", 6, "coverage gap")
    blocking = _ceiling_handover(ws, 705, 6)
    _maybe_grant_fix_cycle_override(
        ws=ws, question=question, blocking=blocking, answer=FIX_CYCLE_OVERRIDE_OPTION
    )
    _maybe_grant_fix_cycle_override(
        ws=ws, question=question, blocking=blocking, answer=FIX_CYCLE_OVERRIDE_OPTION
    )
    state = _pr_state_paths.load_fix_cycle_state(705, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT


def _verifier_handover(ws: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id="h-verif-round-trip",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verifier found a real defect",
        pr_number=pr_number,
    )


def test_park_handover_persists_and_is_answerable_without_manual_save(
    client: TestClient, ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws = create("Fix Ceiling Full Round Trip", "d", root=ws_root)
    handover = _verifier_handover(ws, 706)
    decision = RouterDecision(
        kind="enqueue", team="implementation_fix", reason="request changes"
    )
    d = Dispatcher(queue, dict(_REAL_TEAMS), pubsub=pubsub, workspaces_root=ws_root)
    for _ in range(7):
        d._apply_router_decision(ws, handover, decision=decision)  # noqa: SLF001

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status == Status.AWAITING_INPUT

    park_handover = next(h for h in list_handovers(reloaded) if h.open_questions)
    question_id = park_handover.open_questions[0].id

    res = _post_answer(
        client, reloaded,
        {"question_id": question_id, "chosen_option": FIX_CYCLE_OVERRIDE_OPTION},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "implementation_fix"

    state = _pr_state_paths.load_fix_cycle_state(706, DEFAULT_FORGE_TARGET)
    assert state.allowance == _pr_state_paths.FIX_CYCLE_OVERRIDE_GRANT

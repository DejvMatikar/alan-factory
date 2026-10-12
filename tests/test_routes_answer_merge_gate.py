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
from alan.handover import Handover, OpenQuestion, save_handover
from alan.merge_recovery import MergeBlockClass
from alan.park import Blocker, ParkReason
from alan.queue import Queue
from alan.teams import (
    _forge_errors,
    _pr_closing,
    _pr_freshness,
    _pr_post,
    _pr_ready,
    _pr_state_paths,
)
from alan.workspace import Status, Workspace, create, read, transition

_CONFLICT_BRANCH = "teams/answer-conflict-fix-inv_1"


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


def _merge_block_question(**overrides: object) -> OpenQuestion:
    base: dict[str, object] = dict(
        id="q1", question="how to unblock?", proposed_answer_space=["proceed", "wait"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
    )
    base.update(overrides)
    return OpenQuestion.model_validate(base)


def _escalation(
    task_id: str, *, pr_number: int | None, open_questions: list[OpenQuestion],
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=open_questions,
        pr_number=pr_number,
    )


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def test_answer_without_pr_number_skips_merge_gate_and_dispatches(
    client: TestClient, ws_root: Path, queue: Queue,
) -> None:
    ws = create("Answer No PR Number", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=None, open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()


def test_answer_recovers_mechanically_clearable_block_before_dispatch(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Answer Mechanically Recoverable", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=101, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: True)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT


def test_answer_reparks_with_real_blockers_when_block_neither_poll_nor_repair_worthy(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Answer Repark Stale Review", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=202, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.STALE_REVIEW.value
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    assert reloaded.meta.blockers
    assert any(b.kind == "review_decision" for b in reloaded.meta.blockers)


def test_answer_dispatches_anyway_when_block_is_poll_worthy(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Answer Poll Worthy Dispatch", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=303, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
            pending_checks=("ci/test",), failed_reasons=("pending checks: ci/test",),
        ),
    )

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()


def test_answer_dispatches_anyway_when_block_is_repair_worker_eligible(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Answer Repair Worker Dispatch", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=606, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
            failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",),
        ),
    )

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT


def test_answer_dirty_exhausted_ladder_routes_to_implementation_fix(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass

    ws = create("Answer Dirty Exhausted", "d", root=ws_root)
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=707, open_questions=[q]))
    ws = transition(
        ws, Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        blockers=[Blocker(kind="reason", name="reason", detail="already parked")],
    )
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="DIRTY", review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",), head_sha="deadbeef",
            head_ref_name=_CONFLICT_BRANCH,
        ),
    )
    monkeypatch.setattr(
        merge_recovery, "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, None),
    )

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "implementation_fix"
    jobs = [j for j in queue.list_jobs() if j.team == "implementation_fix"]
    assert jobs
    assert jobs[0].instructions is not None
    assert "deadbeef" in jobs[0].instructions
    assert (
        f"--force-with-lease={_CONFLICT_BRANCH}:deadbeef origin {_CONFLICT_BRANCH}"
        in jobs[0].instructions
    )
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT


def test_answer_dirty_exhausted_ladder_posture_denies_fix_falls_back_to_repark(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import merge_recovery
    from alan.engine_config import Posture
    from alan.merge_recovery import MergeBlockClass
    from alan.workspace import EngineSnapshot, stamp_engine_snapshot

    ws = create("Answer Dirty Exhausted Denied", "d", root=ws_root)
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={"pr_merge": []}),
    )
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=808, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="DIRTY", review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",), head_sha="deadbeef",
            head_ref_name=_CONFLICT_BRANCH,
        ),
    )
    monkeypatch.setattr(
        merge_recovery, "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, None),
    )

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.DIRTY.value


def test_answer_closing_link_transient_forge_error_does_not_500(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create(
        "Answer Closing Link Transient", "Ship it.\n\nDelivers-Issues: 12", root=ws_root,
    )
    q = _merge_block_question()
    save_handover(ws, _escalation(ws.task_id, pr_number=909, open_questions=[q]))
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        ),
    )

    def _raise(*_a: object, **_kw: object) -> frozenset[int]:
        raise _forge_errors.ForgeTransientError("gh timed out")

    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", _raise)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "proceed"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()


def test_answer_resolver_other_than_pr_merge_skips_merge_gate(
    client: TestClient, ws_root: Path, queue: Queue,
) -> None:
    ws = create("Answer Non Merge Resolver", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="judge parse failed", proposed_answer_space=["retry", "escalate"],
        question_class="verification_escalation", resolver_role="human", blocking=True,
    )
    handover = Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=[q],
        pr_number=404,
    )
    save_handover(ws, handover)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "retry"})

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"]["team"] == "verification"
    assert queue.list_jobs()

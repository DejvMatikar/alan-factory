from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import clause_withdrawal, control_plane, server
from alan.events import PubSub
from alan.handover import ClauseOutcome, Handover, OpenQuestion, save_handover
from alan.queue import Queue
from alan.routes import _answer_waiver
from alan.teams import _pr_post, _pr_ready, _pr_waiver_dismissal
from alan.workspace import Requirement, Workspace, create, read

_OPERATOR_ONLY_ID = clause_withdrawal.CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID
_CURRENT = clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_CURRENT
_LEGACY = clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_LEGACY
_OVERRIDE = clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING


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


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def _make_ws(ws_root: Path) -> tuple[Workspace, Requirement]:
    req = Requirement(text="needs a live operator measurement after merge")
    ws = create("operator-only-ws", "d", root=ws_root, requirements=[req])
    return ws, req


def _operator_only_outcome(req_id: str, *, task_id: str) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id,
        actionable_ids=(),
        clause_texts={req_id: "needs a live operator measurement after merge"},
        evidence={req_id: "OPERATOR_ONLY: needs a live traffic measurement"},
        judge_rationale="no fleet role can take this measurement",
        operator_only_ids=(req_id,),
    )


def _operator_only_question(
    *, options: list[str] | None = None, option_resolvers: dict[str, str] | None = None,
) -> OpenQuestion:
    opts = options or [_CURRENT, _OVERRIDE]
    return OpenQuestion(
        id=_OPERATOR_ONLY_ID,
        question="operator-only measurements found",
        proposed_answer_space=opts,
        recommended=opts[0],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers=option_resolvers if option_resolvers is not None else {},
    )


def _escalation(
    task_id: str, *, question: OpenQuestion, clause_outcome: ClauseOutcome | None = None,
    pr_number: int | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="operator-only escalation",
        open_questions=[question],
        clause_outcome=clause_outcome,
        pr_number=pr_number,
    )


def test_current_wording_withdraws_and_dispatches_pr_merge(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _operator_only_question()
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question,
            clause_outcome=_operator_only_outcome(req.id, task_id=ws.task_id),
        ),
    )

    res = _post_answer(client, ws, {"question_id": _OPERATOR_ONLY_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is False
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _OPERATOR_ONLY_ID


def test_override_wording_withdraws_and_dispatches_pr_merge(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _operator_only_question()
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question,
            clause_outcome=_operator_only_outcome(req.id, task_id=ws.task_id),
        ),
    )

    res = _post_answer(client, ws, {"question_id": _OPERATOR_ONLY_ID, "chosen_option": _OVERRIDE})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _OPERATOR_ONLY_ID


def test_legacy_wording_with_empty_persisted_option_resolvers_still_resolves(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _operator_only_question(
        options=[_LEGACY, _OVERRIDE], option_resolvers={},
    )
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question,
            clause_outcome=_operator_only_outcome(req.id, task_id=ws.task_id),
        ),
    )

    res = _post_answer(client, ws, {"question_id": _OPERATOR_ONLY_ID, "chosen_option": _LEGACY})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _OPERATOR_ONLY_ID


def test_answer_dismisses_the_standing_review_as_a_waiver(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _operator_only_question()
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question,
            clause_outcome=_operator_only_outcome(req.id, task_id=ws.task_id),
            pr_number=42,
        ),
    )
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: True)
    calls: list[int] = []

    def _fake_dismiss(*_a: object, **_kw: object) -> _pr_waiver_dismissal.WaiverDismissal:
        calls.append(1)
        return _pr_waiver_dismissal.WaiverDismissal("dismissed", (), False)

    monkeypatch.setattr(_answer_waiver, "dismiss_for_waiver", _fake_dismiss)

    res = _post_answer(client, ws, {"question_id": _OPERATOR_ONLY_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    assert calls == [1]


def test_with_unrecoverable_clause_ids_proceeds_and_withdraws_nothing(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _operator_only_question()
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=None))

    res = _post_answer(client, ws, {"question_id": _OPERATOR_ONLY_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None
    assert queue.list_jobs()

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None

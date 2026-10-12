from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

import alan.routes.answer as answer_mod
from alan import clause_withdrawal, control_plane, server
from alan.events import PubSub
from alan.handover import ClauseOutcome, Handover, OpenQuestion, list_handovers, save_handover
from alan.queue import Queue
from alan.workspace import Requirement, Workspace, create, read, update_requirements

_STALL_ID = "clause-stall-req-a"
_CURRENT = clause_withdrawal.OPTION_WITHDRAW_WORDING_CURRENT
_LEGACY = clause_withdrawal.OPTION_WITHDRAW_WORDING_LEGACY
_WAIVE = clause_withdrawal.OPTION_WAIVE_WORDING


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


def _clause_outcome(req_id: str, *, task_id: str = "stall-ws") -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id,
        actionable_ids=(req_id,),
        clause_texts={req_id: "the endpoint must be idempotent"},
        evidence={req_id: "not idempotent"},
        judge_rationale="no fleet role can fix this",
    )


def _stall_question(
    *, question_id: str = _STALL_ID, options: list[str] | None = None
) -> OpenQuestion:
    opts = options or [_CURRENT, _WAIVE]
    return OpenQuestion(
        id=question_id,
        question="acceptance clause req-a stalled",
        proposed_answer_space=opts,
        recommended=opts[0],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={opts[0]: "verification", opts[1]: "pr_merge"},
        option_effects={opts[0]: "pending_external", opts[1]: "closes"},
    )


def _escalation(
    task_id: str, *, question: OpenQuestion, clause_outcome: ClauseOutcome | None = None
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="clause stall escalation",
        open_questions=[question],
        clause_outcome=clause_outcome,
    )


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def _make_ws(
    ws_root: Path, *, req_text: str = "the endpoint must be idempotent"
) -> tuple[Workspace, Requirement]:
    req = Requirement(text=req_text)
    ws = create("stall-ws", "d", root=ws_root, requirements=[req])
    return ws, req


def _recorded_ruling(
    ws: Workspace, escalation: Handover, *, question: OpenQuestion, answer: str
) -> Handover:
    answered_question = question.model_copy(update={"answer": answer})
    ruling = Handover(
        handover_id=f"handover_{ws.task_id}_ruling",
        handover_chain_id=escalation.handover_chain_id,
        handover_type="revisit_amend",
        task_id=ws.task_id,
        from_team=escalation.from_team,
        from_invocation_id="inv_ruling",
        iteration=escalation.iteration + 1,
        revisit_of=escalation.handover_id,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="recorded ruling, pre-fix, no withdrawal effect",
        open_questions=[answered_question],
    )
    save_handover(ws, ruling)
    return ruling


def test_withdraw_option_withdraws_clause_and_dispatches_verification(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws, _escalation(ws.task_id, question=question, clause_outcome=_clause_outcome(req.id))
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is False
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"
    assert queue.list_jobs()

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    withdrawn = reloaded.meta.requirements[0]
    assert withdrawn.withdrawn_by_question_id == _STALL_ID


def test_legacy_wording_is_still_recognised_as_withdraw(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question(options=[_LEGACY, _WAIVE])
    save_handover(
        ws, _escalation(ws.task_id, question=question, clause_outcome=_clause_outcome(req.id))
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _LEGACY})
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is False
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_a_workspace_parked_on_the_old_wording_clears_with_a_single_answer(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior_verification = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_000",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior verification pass",
        clause_outcome=_clause_outcome(req.id),
    )
    save_handover(ws, prior_verification)

    question = OpenQuestion(
        id=_STALL_ID,
        question="acceptance clause req-a stalled",
        proposed_answer_space=[_LEGACY, _WAIVE],
        recommended=_LEGACY,
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={_LEGACY: "verification", _WAIVE: "pr_merge"},
        option_effects={_LEGACY: "pending_external", _WAIVE: "closes"},
    )
    escalation = Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_chain_id=chain_id,
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="clause stall escalation, pre-fix wording",
        open_questions=[question],
        clause_outcome=None,
    )
    save_handover(ws, escalation)

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _LEGACY})
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is False
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_waive_option_withdraws_clause_and_dispatches_pr_merge(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws, _escalation(ws.task_id, question=question, clause_outcome=_clause_outcome(req.id))
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _WAIVE})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_withdraw_option_with_unrecoverable_clause_ids_returns_422_and_writes_nothing(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=None))

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None
    assert not any(h.handover_type == "revisit_amend" for h in list_handovers(reloaded))


def test_waive_option_with_unrecoverable_clause_ids_proceeds_and_withdraws_nothing(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=None))

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _WAIVE})
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_multi_clause_stall_withdraws_every_named_clause(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    req_a = Requirement(text="clause a")
    req_b = Requirement(text="clause b")
    ws = create("stall-ws-multi", "d", root=ws_root, requirements=[req_a, req_b])
    group_id = "clause-stall-abcdef0123456789"
    question = _stall_question(question_id=group_id)
    outcome = ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(req_a.id, req_b.id),
        clause_texts={req_a.id: "clause a", req_b.id: "clause b"},
        evidence={req_a.id: "e-a", req_b.id: "e-b"},
        judge_rationale="neither clause can be satisfied",
    )
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=outcome))

    res = _post_answer(client, ws, {"question_id": group_id, "chosen_option": _CURRENT})
    assert res.status_code == 200

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    withdrawn_ids = {
        r.id for r in reloaded.meta.requirements if r.withdrawn_by_question_id == group_id
    }
    assert withdrawn_ids == {req_a.id, req_b.id}


def test_waiver_on_an_overlap_keyed_question_withdraws_every_currently_actionable_clause(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    req_a = Requirement(text="clause a")
    req_b = Requirement(text="clause b")
    ws = create("stall-ws-overlap", "d", root=ws_root, requirements=[req_a, req_b])
    overlap_id = f"clause-stall-{req_a.id}"
    question = _stall_question(question_id=overlap_id)
    outcome = ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(req_a.id, req_b.id),
        clause_texts={req_a.id: "clause a", req_b.id: "clause b"},
        evidence={req_a.id: "e-a", req_b.id: "e-b"},
        judge_rationale="only clause a repeated, but both are currently actionable",
    )
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=outcome))

    res = _post_answer(client, ws, {"question_id": overlap_id, "chosen_option": _WAIVE})
    assert res.status_code == 200

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    withdrawn_ids = {
        r.id for r in reloaded.meta.requirements if r.withdrawn_by_question_id == overlap_id
    }
    assert withdrawn_ids == {req_a.id, req_b.id}


def test_withdraw_option_with_stale_clause_ids_returns_422_and_writes_nothing(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question, clause_outcome=_clause_outcome("req-no-longer-there")
        ),
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None
    assert not any(h.handover_type == "revisit_amend" for h in list_handovers(reloaded))


def test_withdraw_option_with_empty_actionable_ids_returns_422_and_writes_nothing(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    outcome = ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(),
        clause_texts={},
        evidence={},
        judge_rationale="nothing actionable remained",
    )
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=outcome))

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].id == req.id
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_waive_option_with_stale_clause_ids_proceeds_and_withdraws_nothing(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws,
        _escalation(
            ws.task_id, question=question, clause_outcome=_clause_outcome("req-no-longer-there")
        ),
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _WAIVE})
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_withdraw_option_persists_closes_effect_onto_recorded_question_with_sibling_open(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    other_question = OpenQuestion(
        id="other-q",
        question="unrelated blocking question",
        proposed_answer_space=["yes", "no"],
        resolver_role="human",
        blocking=True,
    )
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    escalation = escalation.model_copy(update={"open_questions": [question, other_question]})
    save_handover(ws, escalation)

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is False
    assert body["dispatch"] is None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID

    latest = list_handovers(reloaded)[-1]
    [recorded_question] = [q for q in latest.open_questions if q.id == _STALL_ID]
    assert recorded_question.answer == _CURRENT
    assert recorded_question.option_effects[_CURRENT] == "closes"


def test_non_clause_stall_question_ignores_the_host_override(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question(question_id="verif-capability-boundary")
    save_handover(ws, _escalation(ws.task_id, question=question, clause_outcome=None))

    res = _post_answer(
        client, ws, {"question_id": "verif-capability-boundary", "chosen_option": _CURRENT}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["pending_external"] is True
    assert body["dispatch"] is None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_recorded_withdraw_ruling_withdraws_clause_and_dispatches_on_next_answer_call(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["pending_external"] is False
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"
    assert queue.list_jobs()

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_recorded_legacy_withdraw_ruling_withdraws_clause_and_dispatches(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question(options=[_LEGACY, _WAIVE])
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_LEGACY)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _LEGACY,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_repeating_the_recorded_ruling_replay_changes_nothing_further(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)
    answer_body: dict[str, object] = {
        "question_id": _STALL_ID,
        "chosen_option": _CURRENT,
        "handover_id": escalation.handover_id,
    }

    first = _post_answer(client, ws, answer_body)
    assert first.status_code == 200
    assert first.json()["dispatch"] is not None
    jobs_after_first = queue.list_jobs()
    assert jobs_after_first

    second = _post_answer(client, ws, answer_body)
    assert second.status_code == 200
    body = second.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is None

    assert queue.list_jobs() == jobs_after_first


def test_recorded_waive_ruling_does_not_replay_and_dispatches_nothing(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_WAIVE)

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _WAIVE})
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_non_clause_stall_recorded_ruling_dispatches_nothing(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question(question_id="verif-capability-boundary")
    escalation = _escalation(ws.task_id, question=question, clause_outcome=None)
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client, ws, {"question_id": "verif-capability-boundary", "chosen_option": _CURRENT}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_recorded_ruling_on_multi_clause_group_withdraws_every_clause(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    req_a = Requirement(text="clause a")
    req_b = Requirement(text="clause b")
    ws = create("stall-ws-multi-replay", "d", root=ws_root, requirements=[req_a, req_b])
    group_id = "clause-stall-abcdef0123456789"
    question = _stall_question(question_id=group_id)
    outcome = ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(req_a.id, req_b.id),
        clause_texts={req_a.id: "clause a", req_b.id: "clause b"},
        evidence={req_a.id: "e-a", req_b.id: "e-b"},
        judge_rationale="neither clause can be satisfied",
    )
    escalation = _escalation(ws.task_id, question=question, clause_outcome=outcome)
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": group_id,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    withdrawn_ids = {
        r.id for r in reloaded.meta.requirements if r.withdrawn_by_question_id == group_id
    }
    assert withdrawn_ids == {req_a.id, req_b.id}


def test_recorded_ruling_replay_withdraws_with_unanswered_sibling_question_and_dispatches_nothing(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    other_question = OpenQuestion(
        id="other-q",
        question="unrelated blocking question",
        proposed_answer_space=["yes", "no"],
        resolver_role="human",
        blocking=True,
    )
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    escalation = escalation.model_copy(update={"open_questions": [question, other_question]})
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is None

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_recorded_ruling_replay_with_unrecoverable_clause_ids_returns_422(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(ws.task_id, question=question, clause_outcome=None)
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_recorded_ruling_replay_with_stale_clause_ids_returns_422(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, _req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome("req-no-longer-there")
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_recorded_ruling_replay_already_withdrawn_by_another_question_is_a_no_op(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    withdrawn_req = req.model_copy(update={"withdrawn_by_question_id": "some-other-question"})
    update_requirements(ws, [withdrawn_req])

    res = _post_answer(
        client,
        ws,
        {
            "question_id": _STALL_ID,
            "chosen_option": _CURRENT,
            "handover_id": escalation.handover_id,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is None
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == "some-other-question"


def test_recorded_ruling_replay_recovers_clause_ids_when_blocking_handover_has_no_outcome(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior_verification = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_000",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior verification pass",
        clause_outcome=_clause_outcome(req.id),
    )
    save_handover(ws, prior_verification)

    question = _stall_question()
    escalation = Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_chain_id=chain_id,
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="clause stall escalation, no outcome carried",
        open_questions=[question],
        clause_outcome=None,
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    unrelated_chain_handover = Handover(
        handover_id=f"handover_{ws.task_id}_unrelated",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_unrelated",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="unrelated handover on a different chain",
    )
    save_handover(ws, unrelated_chain_handover)

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "verification"
    assert queue.list_jobs()

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_recorded_ruling_replay_stops_at_a_genuine_content_revisit_and_still_returns_422(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws, req = _make_ws(ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior_verification = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_000",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior verification pass",
        clause_outcome=_clause_outcome(req.id),
    )
    save_handover(ws, prior_verification)

    question = _stall_question()
    escalation = Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_chain_id=chain_id,
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="clause stall escalation, no outcome carried",
        open_questions=[question],
        clause_outcome=None,
    )
    save_handover(ws, escalation)

    genuine_content_revisit = Handover(
        handover_id=f"handover_{ws.task_id}_content_revisit",
        handover_chain_id=chain_id,
        handover_type="revisit_amend",
        task_id=ws.task_id,
        from_team="implementation",
        from_invocation_id="inv_content",
        iteration=3,
        revisit_of=escalation.handover_id,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="a real code amendment landed between the stall and the ruling",
        open_questions=[question],
    )
    save_handover(ws, genuine_content_revisit)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 422
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id is None


def test_a_reinstated_clause_is_rewithdrawn_with_question_provenance_by_the_next_answer(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws, _escalation(ws.task_id, question=question, clause_outcome=_clause_outcome(req.id))
    )
    control_headers = _control_headers()
    client.put(
        f"/api/workspaces/{ws.task_id}/clauses/{req.id}/withdrawal",
        json={"reason": "operator repair"},
        headers=control_headers,
    )
    client.request(
        "DELETE",
        f"/api/workspaces/{ws.task_id}/clauses/{req.id}/withdrawal",
        json={"reason": "operator reversal"},
        headers=control_headers,
    )

    res = _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})
    assert res.status_code == 200

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID


def test_a_replayed_question_withdrawal_can_no_longer_be_reversed(
    client: TestClient, ws_root: Path, queue: Queue  # noqa: ARG001
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    save_handover(
        ws, _escalation(ws.task_id, question=question, clause_outcome=_clause_outcome(req.id))
    )
    control_headers = _control_headers()
    client.put(
        f"/api/workspaces/{ws.task_id}/clauses/{req.id}/withdrawal",
        json={"reason": "operator repair"},
        headers=control_headers,
    )
    client.request(
        "DELETE",
        f"/api/workspaces/{ws.task_id}/clauses/{req.id}/withdrawal",
        json={"reason": "operator reversal"},
        headers=control_headers,
    )
    _post_answer(client, ws, {"question_id": _STALL_ID, "chosen_option": _CURRENT})

    res = client.request(
        "DELETE",
        f"/api/workspaces/{ws.task_id}/clauses/{req.id}/withdrawal",
        json={"reason": "operator reversal again"},
        headers=control_headers,
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "clause was withdrawn by a human answering a question"


def test_recorded_ruling_replay_concurrent_calls_dispatch_exactly_once(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws, req = _make_ws(ws_root)
    question = _stall_question()
    escalation = _escalation(
        ws.task_id, question=question, clause_outcome=_clause_outcome(req.id)
    )
    save_handover(ws, escalation)
    _recorded_ruling(ws, escalation, question=question, answer=_CURRENT)

    body = answer_mod.AnswerBody(
        question_id=_STALL_ID, chosen_option=_CURRENT, handover_id=escalation.handover_id
    )
    results: list[answer_mod.AnswerResponse] = []
    results_lock = threading.Lock()

    def _fire() -> None:
        result = answer_mod._answer_and_maybe_dispatch(  # noqa: SLF001
            ws=ws,
            task_id=ws.task_id,
            body=body,
            queue=queue,
            pubsub=pubsub,
            workspaces_root=ws_root,
        )
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=_fire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(results) == 5
    dispatched = [r for r in results if r.dispatch is not None]
    assert len(dispatched) == 1

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == _STALL_ID

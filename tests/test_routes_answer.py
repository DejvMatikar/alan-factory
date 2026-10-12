from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

import alan.project as project_mod
import alan.project_doc as project_doc_mod
from alan import control_plane, gold_label, server
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion, list_handovers, save_handover
from alan.park import ParkReason
from alan.queue import Queue
from alan.workspace import InterventionAction, Status, Workspace, create, read, transition


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def logs_root(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, pubsub: PubSub, projects_root: Path
) -> Iterator[TestClient]:
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


def _escalation(
    task_id: str,
    *,
    from_team: str,
    open_questions: list[OpenQuestion],
    handover_id: str | None = None,
) -> Handover:
    return Handover(
        handover_id=handover_id or f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=open_questions,
    )


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(
    client: TestClient,
    ws: Workspace,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(
            _answer_url(ws),
            json=body,
            headers=headers if headers is not None else _control_headers(),
        ),
    )


def test_answer_question_without_control_token_returns_401(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Guard 401", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = client.post(_answer_url(ws), json={"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 401


def test_answer_question_wrong_control_token_returns_403(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Guard 403", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(
        client, ws, {"question_id": "q1", "chosen_option": "a"},
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_answer_question_unknown_workspace_returns_404(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces/does-not-exist/tasks/does-not-exist/answer",
        json={"question_id": "q1", "chosen_option": "a"},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_answer_question_unknown_handover_id_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Bad Handover", "d", root=ws_root)
    res = _post_answer(
        client, ws, {"question_id": "q1", "chosen_option": "a", "handover_id": "nope"}
    )
    assert res.status_code == 404


def test_answer_question_no_handover_carries_question_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer No Question", "d", root=ws_root)
    unrelated = OpenQuestion(id="other", question="x?", resolver_role="human", blocking=True)
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[unrelated]))

    res = _post_answer(client, ws, {"question_id": "nonexistent", "chosen_option": "a"})
    assert res.status_code == 404


def test_answer_question_explicit_handover_missing_question_returns_404(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Handover No Q", "d", root=ws_root)
    other = OpenQuestion(id="other", question="x?", resolver_role="human", blocking=True)
    handover = _escalation(ws.task_id, from_team="verification", open_questions=[other])
    save_handover(ws, handover)

    res = _post_answer(
        client, ws,
        {"question_id": "q1", "chosen_option": "a", "handover_id": handover.handover_id},
    )
    assert res.status_code == 404


def test_answer_question_both_chosen_option_and_accept_recommendation_returns_422(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Both Set", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"], recommended="a",
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(
        client, ws, {"question_id": "q1", "chosen_option": "a", "accept_recommendation": True}
    )
    assert res.status_code == 422


def test_answer_question_neither_chosen_option_nor_accept_recommendation_returns_422(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Neither Set", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1"})
    assert res.status_code == 422


def test_answer_question_free_text_chosen_option_returns_422(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Free Text", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "free text answer"})
    assert res.status_code == 422


def test_answer_question_accept_recommendation_without_recommended_returns_422(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer No Rec", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "accept_recommendation": True})
    assert res.status_code == 422


def test_answer_question_records_answer_and_dispatches_single_emitter_class(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Dispatch", "d", root=ws_root)
    q = OpenQuestion(
        id="verif-judge-parse",
        question="judge failed to parse",
        proposed_answer_space=["retry", "review manually"],
        recommended="retry",
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "verif-judge-parse", "chosen_option": "retry"})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == "retry"
    assert body["already_answered"] is False
    assert body["dispatch"]["team"] == "verification"
    assert queue.list_jobs()

    revisit = [h for h in list_handovers(ws) if h.handover_type == "revisit_amend"]
    assert len(revisit) == 1
    assert revisit[0].open_questions[0].answer == "retry"


def test_answer_question_accept_recommendation_uses_recommended_value(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Accept Rec", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"], recommended="b",
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "accept_recommendation": True})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == "b"
    assert body["dispatch"]["team"] == "implementation_fix"


def test_answer_question_explicit_handover_id_is_honored(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Explicit Handover", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    handover = _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q])
    save_handover(ws, handover)

    res = _post_answer(
        client, ws,
        {"question_id": "q1", "chosen_option": "a", "handover_id": handover.handover_id},
    )
    assert res.status_code == 200


def test_answer_question_option_resolvers_routes_to_mapped_team_not_class_default(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Option Resolvers", "d", root=ws_root)
    q = OpenQuestion(
        id="non_actionable_feedback_abc12345",
        question="review feedback was non-actionable",
        proposed_answer_space=[
            "Re-dispatch implementation_fix once the flaky/environmental "
            "cause has cleared, so the same review can be attempted again.",
            "Resolve the ambiguity by hand (clarify scope, fix the "
            "environment issue), then re-dispatch verification directly.",
        ],
        recommended=(
            "Re-dispatch implementation_fix once the flaky/environmental "
            "cause has cleared, so the same review can be attempted again."
        ),
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={
            "Resolve the ambiguity by hand (clarify scope, fix the "
            "environment issue), then re-dispatch verification directly.": "verification",
        },
    )
    save_handover(
        ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q])
    )

    res = _post_answer(
        client, ws,
        {
            "question_id": "non_actionable_feedback_abc12345",
            "chosen_option": (
                "Resolve the ambiguity by hand (clarify scope, fix the "
                "environment issue), then re-dispatch verification directly."
            ),
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"]["team"] == "verification"


def test_answer_question_recommended_option_still_uses_class_default(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Option Resolvers Default", "d", root=ws_root)
    q = OpenQuestion(
        id="non_actionable_feedback_def67890",
        question="review feedback was non-actionable",
        proposed_answer_space=[
            "Re-dispatch implementation_fix once the flaky/environmental "
            "cause has cleared, so the same review can be attempted again.",
            "Resolve the ambiguity by hand (clarify scope, fix the "
            "environment issue), then re-dispatch verification directly.",
        ],
        recommended=(
            "Re-dispatch implementation_fix once the flaky/environmental "
            "cause has cleared, so the same review can be attempted again."
        ),
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={
            "Resolve the ambiguity by hand (clarify scope, fix the "
            "environment issue), then re-dispatch verification directly.": "verification",
        },
    )
    save_handover(
        ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q])
    )

    res = _post_answer(
        client, ws,
        {"question_id": "non_actionable_feedback_def67890", "accept_recommendation": True},
    )
    assert res.status_code == 200
    assert res.json()["dispatch"]["team"] == "implementation_fix"


def test_answer_question_option_resolvers_mapped_team_still_fenced_by_admission(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Option Resolvers Forbidden", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
        option_resolvers={"b": "decomposer"},
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "b"})
    assert res.status_code == 400
    assert queue.list_jobs() == []


def test_answer_question_dispatch_instructions_tell_worker_the_decision(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Instructions", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which db?", proposed_answer_space=["sqlite", "duckdb"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "sqlite"})
    assert res.status_code == 200

    [job] = queue.list_jobs()
    assert job.instructions is not None
    assert "q1" in job.instructions
    assert "which db?" in job.instructions
    assert "sqlite" in job.instructions
    assert "already decided" in job.instructions
    assert "do not re-ask or re-litigate" in job.instructions


def test_answer_question_orchestrated_completeness_resolves_to_emitting_team(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Orchestrated", "d", root=ws_root)
    q = OpenQuestion(
        id="low_completeness",
        question="complete enough?",
        proposed_answer_space=["accept", "expand scope"],
        question_class="orchestrated_completeness",
        resolver_role="human",
        blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="ui_review", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "low_completeness", "chosen_option": "accept"})
    assert res.status_code == 200
    assert res.json()["dispatch"]["team"] == "ui_review"


def test_answer_question_multi_question_earlier_answer_does_not_dispatch(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Multi", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", proposed_answer_space=["x", "y"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    q2 = OpenQuestion(
        id="q2", question="b?", proposed_answer_space=["x", "y"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(
        ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q1, q2])
    )

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "x"})
    assert first.status_code == 200
    assert first.json()["dispatch"] is None
    assert queue.list_jobs() == []

    second = _post_answer(client, ws, {"question_id": "q2", "chosen_option": "y"})
    assert second.status_code == 200
    assert second.json()["dispatch"] is not None
    assert queue.list_jobs()


def test_answer_question_fallback_then_explicit_root_handover_id_is_idempotent(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Multi Fallback Idempotent", "d", root=ws_root)
    q1 = OpenQuestion(
        id="q1", question="a?", proposed_answer_space=["x", "y"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    q2 = OpenQuestion(
        id="q2", question="b?", proposed_answer_space=["x", "y"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    escalation = _escalation(
        ws.task_id, from_team="implementation_fix", open_questions=[q1, q2]
    )
    save_handover(ws, escalation)

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "x"})
    assert first.status_code == 200
    assert first.json()["dispatch"] is None

    second = _post_answer(client, ws, {"question_id": "q2", "chosen_option": "y"})
    assert second.status_code == 200
    assert second.json()["dispatch"] is not None
    jobs_after_second = len(queue.list_jobs())

    third = _post_answer(
        client, ws,
        {
            "question_id": "q2",
            "chosen_option": "y",
            "handover_id": escalation.handover_id,
        },
    )
    assert third.status_code == 200
    assert third.json()["already_answered"] is True
    assert third.json()["answer"] == "y"
    assert third.json()["dispatch"] is None
    assert len(queue.list_jobs()) == jobs_after_second


def test_answer_question_double_post_is_idempotent_and_does_not_redispatch(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Idempotent", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="pr_merge", open_questions=[q]))

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert first.status_code == 200
    assert first.json()["already_answered"] is False
    assert first.json()["dispatch"] is not None
    jobs_after_first = len(queue.list_jobs())

    second = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert second.status_code == 200
    assert second.json()["already_answered"] is True
    assert second.json()["answer"] == "a"
    assert second.json()["dispatch"] is None
    assert len(queue.list_jobs()) == jobs_after_first


def test_answer_question_conflicting_second_post_returns_first_recorded_answer(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Conflict", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="pr_merge", open_questions=[q]))

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert first.status_code == 200

    second = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "b"})
    assert second.status_code == 200
    assert second.json()["already_answered"] is True
    assert second.json()["answer"] == "a"


def test_answer_question_inline_already_answered_short_circuits(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Inline Answered", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
        answer="a",
    )
    save_handover(ws, _escalation(ws.task_id, from_team="pr_merge", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "b"})
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is True
    assert body["answer"] == "a"
    assert body["dispatch"] is None
    assert queue.list_jobs() == []


def test_answer_question_resolver_outside_manually_dispatchable_teams_returns_400(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Bad Resolver", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="complete?", proposed_answer_space=["accept", "expand"],
        question_class="orchestrated_completeness", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="decomposer", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "accept"})
    assert res.status_code == 400
    assert queue.list_jobs() == []


def test_answer_question_bad_resolver_retry_reattempts_instead_of_sticking(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Bad Resolver Retry", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="complete?", proposed_answer_space=["accept", "expand"],
        question_class="orchestrated_completeness", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="decomposer", open_questions=[q]))

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "accept"})
    assert first.status_code == 400

    second = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "accept"})
    assert second.status_code == 400
    assert queue.list_jobs() == []
    assert not any(h.handover_type == "revisit_amend" for h in list_handovers(ws))


def test_answer_question_appends_decision_log_when_project_id_set(
    client: TestClient, ws_root: Path, queue: Queue, projects_root: Path
) -> None:
    project = project_mod.create("Answer Project", root=projects_root)
    ws = create("Answer With Project", "d", root=ws_root, project_id=project.id)
    q = OpenQuestion(
        id="q1", question="which db?", proposed_answer_space=["sqlite", "duckdb"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "sqlite"})
    assert res.status_code == 200

    project = project_mod.read(project.id, root=projects_root)
    _, path = project_doc_mod.ensure_doc(project, root=projects_root)
    sections = project_doc_mod.parse_anchors(path.read_text(encoding="utf-8"))
    assert "q1" in sections.decision_log
    assert "sqlite" in sections.decision_log


def test_answer_question_dangling_project_id_does_not_break_answering(
    client: TestClient, ws_root: Path, queue: Queue, projects_root: Path
) -> None:
    ws = create("Answer Dangling Project", "d", root=ws_root, project_id="no-such-project")
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None


def test_answer_question_records_gold_label_row(
    client: TestClient, ws_root: Path, queue: Queue, logs_root: Path
) -> None:
    ws = create("Answer Gold Label", "d", root=ws_root)
    q = OpenQuestion(
        id="verif-judge-parse",
        question="judge failed to parse",
        proposed_answer_space=["retry", "review manually"],
        recommended="retry",
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "verif-judge-parse", "chosen_option": "retry"})
    assert res.status_code == 200

    path = logs_root / gold_label.GOLD_LABEL_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == ws.task_id
    assert row["project_id"] is None
    assert row["question_id"] == "verif-judge-parse"
    assert row["question_class"] == "verification_escalation"
    assert row["options"] == ["retry", "review manually"]
    assert row["recommended"] == "retry"
    assert row["chosen"] == "retry"
    assert row["agreed_with_recommendation"] is True
    assert row["time_to_answer_seconds"] >= 0


def test_answer_question_gold_label_row_carries_project_id(
    client: TestClient, ws_root: Path, queue: Queue, projects_root: Path, logs_root: Path
) -> None:
    project = project_mod.create("Gold Label Project", root=projects_root)
    ws = create("Answer Gold Label Project", "d", root=ws_root, project_id=project.id)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"], recommended="a",
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "b"})
    assert res.status_code == 200

    path = logs_root / gold_label.GOLD_LABEL_FILE
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["project_id"] == project.id
    assert row["chosen"] == "b"
    assert row["recommended"] == "a"
    assert row["agreed_with_recommendation"] is False


def test_answer_question_idempotent_repeat_post_does_not_double_write_gold_label(
    client: TestClient, ws_root: Path, queue: Queue, logs_root: Path
) -> None:
    ws = create("Answer Gold Label Idempotent", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="pr_merge", open_questions=[q]))

    first = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert first.status_code == 200
    second = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert second.status_code == 200
    assert second.json()["already_answered"] is True

    path = logs_root / gold_label.GOLD_LABEL_FILE
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_answer_question_gold_label_write_failure_does_not_break_answer(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Answer Gold Label Swallow", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("gold label store unavailable")

    monkeypatch.setattr(gold_label, "append_jsonl_audit", boom)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == "a"
    assert body["dispatch"] is not None


def test_answer_question_records_answer_intervention(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Intervention", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.ANSWER]


def test_answer_question_intervention_survives_dispatch_wake(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Intervention Wake", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))
    transition(ws, Status.AWAITING_INPUT)

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"})
    assert res.status_code == 200
    assert res.json()["dispatch"] is not None
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status == Status.ACTIVE
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.ANSWER]


def test_answer_question_idempotent_repeat_does_not_double_record_intervention(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Intervention Repeat", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    assert _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"}).status_code == 200
    assert _post_answer(client, ws, {"question_id": "q1", "chosen_option": "a"}).status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert len(reloaded.meta.interventions) == 1


def test_answer_question_422_records_no_intervention(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Answer Intervention 422", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        question_class="fix_escalation", resolver_role="human", blocking=True,
    )
    save_handover(ws, _escalation(ws.task_id, from_team="implementation_fix", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": "q1", "chosen_option": "free text"})
    assert res.status_code == 422
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


_PENDING_EXTERNAL_OPTION = "Complete the out-of-fleet step yourself, then re-dispatch verification."
_WAIVE_OPTION = "Waive this finding and proceed to merge."


def _capability_boundary_question(**overrides: object) -> OpenQuestion:
    base: dict[str, object] = dict(
        id="verif-capability-boundary",
        question="needs vault access",
        proposed_answer_space=[_PENDING_EXTERNAL_OPTION, _WAIVE_OPTION],
        recommended=_PENDING_EXTERNAL_OPTION,
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={_PENDING_EXTERNAL_OPTION: "verification", _WAIVE_OPTION: "pr_merge"},
        option_effects={_PENDING_EXTERNAL_OPTION: "pending_external", _WAIVE_OPTION: "closes"},
    )
    base.update(overrides)
    return OpenQuestion.model_validate(base)


def test_answer_question_pending_external_starts_no_work(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Pending External", "d", root=ws_root)
    q = _capability_boundary_question()
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))
    transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION)

    res = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["already_answered"] is False
    assert body["dispatch"] is None
    assert body["pending_external"] is True
    assert queue.list_jobs() == []

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status == Status.AWAITING_INPUT
    assert reloaded.meta.park_reason == ParkReason.QUESTION

    revisit = [h for h in list_handovers(ws) if h.handover_type == "revisit_amend"]
    assert len(revisit) == 1
    assert revisit[0].open_questions[0].answer == _PENDING_EXTERNAL_OPTION


def test_answer_question_pending_external_skips_resolver_validation(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.routes.answer as answer_mod

    calls: list[object] = []
    original = answer_mod._validate_resolver

    def spy(*args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(answer_mod, "_validate_resolver", spy)

    ws = create("Answer Pending External No Validate", "d", root=ws_root)
    q = _capability_boundary_question()
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert calls == []
    assert queue.list_jobs() == []


def test_answer_question_pending_external_non_dispatchable_resolver_raises_no_error(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Pending External Bad Resolver", "d", root=ws_root)
    q = _capability_boundary_question(
        option_resolvers={_PENDING_EXTERNAL_OPTION: "decomposer", _WAIVE_OPTION: "pr_merge"},
    )
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert body["pending_external"] is True
    assert queue.list_jobs() == []


def test_answer_question_pending_external_repeat_post_is_idempotent(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Pending External Repeat", "d", root=ws_root)
    q = _capability_boundary_question()
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    first = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert first.status_code == 200
    assert first.json()["already_answered"] is False
    assert first.json()["pending_external"] is True

    second = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert second.status_code == 200
    assert second.json()["already_answered"] is True
    assert second.json()["pending_external"] is True
    assert second.json()["dispatch"] is None
    assert queue.list_jobs() == []


def test_answer_question_pending_external_still_records_gold_label_and_decision_log(
    client: TestClient, ws_root: Path, queue: Queue, logs_root: Path, projects_root: Path
) -> None:
    project = project_mod.create("Pending External Project", root=projects_root)
    ws = create("Answer Pending External Logging", "d", root=ws_root, project_id=project.id)
    q = _capability_boundary_question()
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(
        client, ws, {"question_id": q.id, "chosen_option": _PENDING_EXTERNAL_OPTION}
    )
    assert res.status_code == 200

    path = logs_root / gold_label.GOLD_LABEL_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["chosen"] == _PENDING_EXTERNAL_OPTION

    project = project_mod.read(project.id, root=projects_root)
    _, doc_path = project_doc_mod.ensure_doc(project, root=projects_root)
    sections = project_doc_mod.parse_anchors(doc_path.read_text(encoding="utf-8"))
    assert q.id in sections.decision_log

    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.ANSWER]


def test_answer_question_waive_option_still_dispatches(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Answer Waive Dispatches", "d", root=ws_root)
    q = _capability_boundary_question()
    save_handover(ws, _escalation(ws.task_id, from_team="verification", open_questions=[q]))

    res = _post_answer(client, ws, {"question_id": q.id, "chosen_option": _WAIVE_OPTION})
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert body["pending_external"] is False
    assert queue.list_jobs()

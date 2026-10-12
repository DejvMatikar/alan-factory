from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan import workspace as workspace_mod
from alan.handover import (
    ClauseOutcome,
    Decision,
    Handover,
    OpenQuestion,
    record_answer,
    save_handover,
)
from alan.teams import _fence, _fix_gate_checks, _pr_threads, _sonar_fix_block
from alan.teams import implementation_fix as fix_mod
from alan.teams._base import UNTRUSTED_CONTENT_NOTICE
from alan.workspace import Workspace

HEADING = "## Failing acceptance clauses"
REVIEW_BODY = "REVIEW-BODY-SENTINEL"
_DECISION = Decision(
    id="d1", statement="s", rationale="r", confidence=0.5, reversibility="medium"
)


def _ws(root: Path) -> Workspace:
    return workspace_mod.create("Fix WS", "d", root=root)


def _outcome(task_id: str) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id,
        actionable_ids=("req-a",),
        clause_texts={"req-a": "the clause as written"},
        evidence={"req-a": "auditor evidence"},
    )


def _prior(
    ws: Workspace,
    outcome: ClauseOutcome | None,
    *,
    open_questions: list[OpenQuestion] | None = None,
) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=REVIEW_BODY,
        decisions=[_DECISION],
        clause_outcome=outcome,
        open_questions=open_questions or [],
    )


def _thread() -> _pr_threads.ReviewThread:
    return _pr_threads.ReviewThread(
        thread_id="t1",
        author_login="reviewer",
        body="please rename this",
        path="alan/x.py",
        line=3,
        is_outdated=False,
    )


def _blocks(
    ws: Workspace,
    prior: Handover,
    task_id: str | None = None,
    threads: tuple[_pr_threads.ReviewThread, ...] = (),
) -> list[str]:
    return fix_mod._mid_blocks(
        prior, threads, _sonar_fix_block.NO_SONAR_FINDINGS,
        _fix_gate_checks.FailingChecks(), None, ws, task_id,
    )


def _joined(
    ws: Workspace,
    prior: Handover,
    task_id: str | None = None,
    threads: tuple[_pr_threads.ReviewThread, ...] = (),
) -> str:
    return "".join(_blocks(ws, prior, task_id, threads))


def _question() -> OpenQuestion:
    return OpenQuestion(id="q1", question="Which way?", resolver_role="human", blocking=True)


def _answered(ws: Workspace, outcome: ClauseOutcome | None) -> Handover:
    blocking = _prior(ws, outcome, open_questions=[_question()])
    save_handover(ws, blocking)
    return record_answer(ws, blocking, "q1", "go ahead", from_team="human")


def test_mid_blocks_includes_failing_clause_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    text = _joined(ws, _prior(ws, _outcome(ws.task_id)))
    assert HEADING in text
    assert "- req-a: the clause as written" in text
    assert "auditor evidence" not in text


def test_failing_clause_block_sits_outside_the_judge_review_fence(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    text = _joined(ws, _prior(ws, _outcome(ws.task_id)))
    fenced_review = _fence.fence(REVIEW_BODY)
    notice_at = text.index(UNTRUSTED_CONTENT_NOTICE)
    fence_end = text.index(fenced_review) + len(fenced_review)
    assert notice_at < fence_end
    assert text.index(HEADING) >= fence_end


def test_failing_clause_block_precedes_the_unresolved_threads_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    text = _joined(ws, _prior(ws, _outcome(ws.task_id)), threads=(_thread(),))
    assert text.index(HEADING) < text.index("## Unresolved PR threads")


def test_prior_without_clause_outcome_yields_no_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    text = _joined(ws, _prior(ws, None))
    assert HEADING not in text
    assert REVIEW_BODY in text


def test_answer_recording_handover_inherits_clause_outcome_and_renders(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path / "ws")
    answered = _answered(ws, _outcome(ws.task_id))
    assert answered.handover_type == "revisit_amend"
    assert "- req-a: the clause as written" in _joined(ws, answered)


def test_answer_recording_handover_without_outcome_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    answered = _answered(ws, None)
    assert answered.clause_outcome is None
    assert HEADING not in _joined(ws, answered)


def test_decomposed_subtask_matching_task_id_renders_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    prior = _prior(ws, _outcome("subtask-1"))
    assert "- req-a: the clause as written" in _joined(ws, prior, task_id="subtask-1")


def test_decomposed_subtask_mismatched_task_id_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    prior = _prior(ws, _outcome("subtask-1"))
    assert HEADING not in _joined(ws, prior, task_id="subtask-2")


def test_undecomposed_dispatch_through_mid_blocks_renders_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    prior = _prior(ws, _outcome(ws.task_id))
    assert "- req-a: the clause as written" in _joined(ws, prior, task_id=None)

from __future__ import annotations

from pathlib import Path

from alan import router as router_mod
from alan import workspace as workspace_mod
from alan.events import PubSub
from alan.handover import Decision, save_handover, unanswered_blocking_question_ids
from alan.queue import Queue
from alan.routes import answer as answer_mod
from alan.teams import _fix_forward, _pr_handover
from alan.teams._declared_blocker import (
    BLOCKER_UNFOUNDED_OPTION,
    BLOCKING_MARK,
    HAND_RESOLVE_OPTION,
    WORKER_STEP_OPTION,
)

_SYNTHESIS_WITH_MARK = (
    "## Summary\nfixed the thing\n\n"
    "## Open questions\n"
    f"- {BLOCKING_MARK} branch protection blocks the required force-push\n"
)
_SYNTHESIS_WITHOUT_MARK = (
    "## Summary\nfixed the thing\n\n"
    "## Open questions\n"
    "- purely informational, nothing to decide\n"
)


def _ws(ws_root: Path, name: str) -> workspace_mod.Workspace:
    return workspace_mod.create(name, "d", root=ws_root)


def test_pr_handover_forward_handover_carries_the_declared_blocker(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, "declared-blocker-impl")
    ctx = _pr_handover.HandoverContext.build(
        ws, None, "inv_00000001",
        team_name="implementation", default_forbidden=(), next_team=("verification",),
    )
    handover = _pr_handover.forward_handover(
        ctx, _SYNTHESIS_WITH_MARK, ["abc1234 fix the bug"], None, decision_prefix="impl",
    )
    assert len(handover.open_questions) == 1
    question = handover.open_questions[0]
    assert question.blocking is True
    assert question.resolver_role == "human"
    assert question.recommended == HAND_RESOLVE_OPTION


def test_pr_handover_forward_handover_carries_no_question_without_the_mark(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, "declared-blocker-impl-clean")
    ctx = _pr_handover.HandoverContext.build(
        ws, None, "inv_00000002",
        team_name="implementation", default_forbidden=(), next_team=("verification",),
    )
    handover = _pr_handover.forward_handover(
        ctx, _SYNTHESIS_WITHOUT_MARK, ["abc1234 fix the bug"], None, decision_prefix="impl",
    )
    assert handover.open_questions == []


def test_fix_forward_build_forward_handover_carries_the_declared_blocker(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, "declared-blocker-fix")
    decision = Decision(
        id="fix-scenarios", statement="s", rationale="r", confidence=0.8, reversibility="medium",
    )
    handover = _fix_forward._build_forward_handover(
        ws, None, "inv_00000003", _SYNTHESIS_WITH_MARK, None,
        decision=decision, test_scenarios=[],
    )
    assert len(handover.open_questions) == 1
    assert handover.open_questions[0].blocking is True


def test_fix_forward_build_forward_handover_carries_no_question_without_the_mark(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, "declared-blocker-fix-clean")
    decision = Decision(
        id="fix-scenarios", statement="s", rationale="r", confidence=0.8, reversibility="medium",
    )
    handover = _fix_forward._build_forward_handover(
        ws, None, "inv_00000004", _SYNTHESIS_WITHOUT_MARK, None,
        decision=decision, test_scenarios=[],
    )
    assert handover.open_questions == []


def test_declared_blocker_question_holds_the_route_at_await_human(ws_root: Path) -> None:
    ws = _ws(ws_root, "declared-blocker-router")
    ctx = _pr_handover.HandoverContext.build(
        ws, None, "inv_00000005",
        team_name="implementation", default_forbidden=(), next_team=("verification",),
    )
    handover = _pr_handover.forward_handover(
        ctx, _SYNTHESIS_WITH_MARK, ["abc1234 fix the bug"], None, decision_prefix="impl",
    )

    decision = router_mod.select_next_team(handover, {})

    assert decision.kind == "await_human"
    question = handover.open_questions[0]
    assert decision.blockers[0].name == question.id


def test_declared_blocker_question_is_unanswered_and_blocks_dispatch(ws_root: Path) -> None:
    ws = _ws(ws_root, "declared-blocker-dispatch")
    ctx = _pr_handover.HandoverContext.build(
        ws, None, "inv_00000006",
        team_name="implementation", default_forbidden=(), next_team=("verification",),
    )
    handover = _pr_handover.forward_handover(
        ctx, _SYNTHESIS_WITH_MARK, ["abc1234 fix the bug"], None, decision_prefix="impl",
    )

    unanswered = unanswered_blocking_question_ids(handover, ws)

    assert unanswered == [handover.open_questions[0].id]


def test_declared_blocker_options_are_not_copied_from_generic_fix_escalation_wording() -> None:
    from alan.teams._fix_escalation import GENERIC_OPTIONS

    assert HAND_RESOLVE_OPTION not in GENERIC_OPTIONS
    assert BLOCKER_UNFOUNDED_OPTION not in GENERIC_OPTIONS
    assert WORKER_STEP_OPTION not in GENERIC_OPTIONS


def test_worker_step_answer_dispatches_the_fix_team_with_the_option_text_as_instruction(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws = _ws(ws_root, "declared-blocker-worker-step")
    ctx = _pr_handover.HandoverContext.build(
        ws, None, "inv_00000007",
        team_name="implementation", default_forbidden=(), next_team=("verification",),
    )
    handover = _pr_handover.forward_handover(
        ctx, _SYNTHESIS_WITH_MARK, ["abc1234 fix the bug"], None, decision_prefix="impl",
    )
    save_handover(ws, handover)
    question = handover.open_questions[0]

    response = answer_mod._answer_and_maybe_dispatch(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        ws=ws, task_id=ws.task_id,
        body=answer_mod.AnswerBody(question_id=question.id, chosen_option=WORKER_STEP_OPTION),
        queue=queue, pubsub=pubsub, workspaces_root=ws_root,
    )

    assert response.dispatch is not None
    assert response.dispatch.team == "implementation_fix"
    [job] = queue.list_jobs()
    assert job.instructions is not None
    assert f"A: {WORKER_STEP_OPTION}" in job.instructions
    assert "do not re-ask or re-litigate" in job.instructions

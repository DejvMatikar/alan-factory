from __future__ import annotations

from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_recipes as recipes_mod
from alan.handover import OpenQuestion
from alan.park import Blocker
from alan.teams import _fix_escalation as fix_escalation_mod
from alan.teams import _pr_post as pr_post_mod
from alan.teams import _verification_gates as verification_gates_mod
from alan.teams import verification as verif_mod
from alan.teams._pr_branch import VerifierIdentity
from alan.workspace import Workspace
from tests import _unblock_support as support

_RECIPE = next(r for r in recipes_mod.RECIPES if r.name == "non_actionable_green_head")
_QUESTION_ID = "non_actionable_feedback_inv_008"
_HEAD = "abc"
_REASON = "both failing clauses need evidence from the pipeline, not from this checkout"
_PR_MERGE_OPTION = "Re-dispatch pr_merge — the block is external gate state, not code."


@pytest.fixture(autouse=True)
def _verifier_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return support.parked_ws(
        tmp_path, blockers=[Blocker(kind="reason", name=_QUESTION_ID, detail=_REASON)]
    )


def _emitted_question() -> OpenQuestion:
    return fix_escalation_mod.non_actionable_question(None, "inv_008", _REASON)


def _parked(
    ws: Workspace,
    *,
    question: OpenQuestion | None = None,
    pr_number: int | None = 123,
    review_stream: str | None = "",
    **context_overrides: object,
) -> recipes_mod.RecipeContext:
    q = question if question is not None else _emitted_question()
    latest = support.handover(
        ws, from_team="implementation_fix", questions=[q], pr_number=pr_number
    )
    fields: dict[str, object] = {
        "latest": latest,
        "pr_number": pr_number,
        "ready": support.readiness(head_sha=_HEAD, review_decision="CHANGES_REQUESTED"),
        "recipe_deps": support.deps(review_stream=review_stream),
    }
    fields.update(context_overrides)
    return support.context(ws, **fields)  # type: ignore[arg-type]


def _verifier_rejections(count: int, *, commit_id: str = _HEAD) -> str:
    return "\n".join(
        support.review_line(commit_id=commit_id, review_id=index) for index in range(count)
    )


def test_matches_the_question_the_fix_team_emits(ws: Workspace) -> None:
    assert _RECIPE.match(_parked(ws)) is True


def test_matches_the_gate_cause_variant_of_the_question(ws: Workspace) -> None:
    q = support.question(
        question_id=_QUESTION_ID,
        question_class="fix_escalation",
        options=(_PR_MERGE_OPTION, fix_escalation_mod.RESOLVE_BY_HAND),
        resolvers={
            _PR_MERGE_OPTION: "pr_merge",
            fix_escalation_mod.RESOLVE_BY_HAND: "verification",
        },
    )
    assert _RECIPE.match(_parked(ws, question=q)) is True


def test_rejects_a_park_without_a_pull_request(ws: Workspace) -> None:
    assert _RECIPE.match(_parked(ws, pr_number=None)) is False


def test_rejects_a_park_without_a_question(ws: Workspace) -> None:
    assert _RECIPE.match(support.context(ws, latest=support.handover(ws))) is False


def test_rejects_another_question_id(ws: Workspace) -> None:
    q = support.question(
        question_id="fix_cycle_ceiling_inv_008",
        options=("keep fixing", fix_escalation_mod.RESOLVE_BY_HAND),
    )
    assert _RECIPE.match(_parked(ws, question=q)) is False


def test_rejects_a_prefixed_question_that_does_not_offer_the_by_hand_option(
    ws: Workspace,
) -> None:
    q = support.question(question_id=_QUESTION_ID, options=("option one", "option two"))
    assert _RECIPE.match(_parked(ws, question=q)) is False


def test_a_real_park_is_matched_by_this_recipe_alone(ws: Workspace) -> None:
    assert [r.name for r in recipes_mod.match_recipes(_parked(ws))] == [
        "non_actionable_green_head"
    ]


def test_probe_waits_while_the_forge_is_unavailable(ws: Workspace) -> None:
    assert _RECIPE.probe(_parked(ws, ready=None)).state == "not_yet"


def test_probe_waits_while_a_check_is_pending(ws: Workspace) -> None:
    ctx = _parked(ws, ready=support.readiness(head_sha=_HEAD, pending_checks=("e2e",)))
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet("checks pending: e2e")


def test_probe_waits_while_the_head_has_no_check_results(ws: Workspace) -> None:
    ctx = _parked(
        ws, ready=support.readiness(head_sha=_HEAD, check_count=0, passing_checks=())
    )
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet("no check results at the head yet")


def test_probe_waits_when_a_green_read_carries_no_head_commit(ws: Workspace) -> None:
    ctx = _parked(ws, ready=support.readiness(head_sha=""), review_stream=_verifier_rejections(2))
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet(
        "the forge reported no head commit to bound the retry against"
    )


def test_probe_declines_a_failing_head(ws: Workspace) -> None:
    ctx = _parked(ws, ready=support.readiness(head_sha=_HEAD, failing_checks=("e2e",)))
    assert _RECIPE.probe(ctx) == recipes_mod.declined("checks failing: e2e")


def test_probe_declines_a_conflicted_branch(ws: Workspace) -> None:
    ctx = _parked(ws, ready=support.readiness(head_sha=_HEAD, merge_state_status="DIRTY"))
    assert _RECIPE.probe(ctx).state == "declined"


def test_probe_declines_an_unresolved_human_thread(ws: Workspace) -> None:
    ctx = _parked(
        ws, recipe_deps=support.deps(feedback=support.snapshot(human_unresolved=1))
    )
    assert _RECIPE.probe(ctx).state == "declined"


def test_probe_waits_while_feedback_is_unavailable(ws: Workspace) -> None:
    ctx = _parked(
        ws, recipe_deps=support.deps(feedback=support.snapshot(ok=False, failure_reason="503"))
    )
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet("feedback unavailable: 503")


def test_probe_waits_while_the_review_stream_is_unavailable(ws: Workspace) -> None:
    assert _RECIPE.probe(_parked(ws, review_stream=None)) == recipes_mod.not_yet(
        "review stream unavailable"
    )


def test_probe_declines_when_a_human_requests_changes(ws: Workspace) -> None:
    stream = support.review_line(login="human-reviewer", commit_id=_HEAD)
    assert _RECIPE.probe(_parked(ws, review_stream=stream)) == recipes_mod.declined(
        "a human review requests changes"
    )


def test_probe_is_ready_after_one_verifier_rejection_at_the_head(ws: Workspace) -> None:
    assert _RECIPE.probe(_parked(ws, review_stream=_verifier_rejections(1))).state == "ready"


def test_probe_is_ready_when_the_only_verifier_rejection_predates_the_head(
    ws: Workspace,
) -> None:
    stream = _verifier_rejections(3, commit_id="older")
    assert _RECIPE.probe(_parked(ws, review_stream=stream)).state == "ready"


def test_probe_declines_after_two_verifier_rejections_at_the_head(ws: Workspace) -> None:
    probe = _RECIPE.probe(_parked(ws, review_stream=_verifier_rejections(2)))
    assert probe == recipes_mod.declined(
        "the verifier already rejected this head twice; a third pass adds nothing"
    )


def test_two_actionable_downgrades_at_an_unmoved_head_decline_the_retry(
    ws: Workspace,
) -> None:
    downgrade = verification_gates_mod._downgrade_output(  # noqa: SLF001
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n",
        support.readiness(failing_checks=("sonar",)),
        ("sonar",),
    )
    assert verif_mod.github_review_event(downgrade) == "REQUEST_CHANGES"
    probe = _RECIPE.probe(_parked(ws, review_stream=_verifier_rejections(2)))
    assert probe == recipes_mod.declined(
        "the verifier already rejected this head twice; a third pass adds nothing"
    )


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], 0),
        ([support.review_line(commit_id=_HEAD)], 1),
        ([support.review_line(commit_id=_HEAD), support.review_line(commit_id=_HEAD)], 2),
        ([support.review_line(commit_id="older")], 0),
        ([support.review_line(commit_id=_HEAD, state="DISMISSED")], 0),
        ([support.review_line(commit_id=_HEAD, state="APPROVED")], 0),
        ([support.review_line(login="human-reviewer", commit_id=_HEAD)], 0),
        ([support.review_line(login=None, commit_id=_HEAD)], 0),
    ],
)
def test_verifier_rejections_at_head_counts_only_live_verifier_rejections_at_that_commit(
    lines: list[str], expected: int
) -> None:
    reviews = pr_post_mod.parse_review_objects("\n".join(lines))
    verifier = VerifierIdentity(support.VERIFIER)
    assert recipes_mod.verifier_rejections_at_head(reviews, verifier, _HEAD) == expected


def test_plan_answers_the_by_hand_option_towards_verification(ws: Workspace) -> None:
    plan = _RECIPE.plan(_parked(ws))
    assert (plan.kind, plan.team, plan.option) == (
        "answer", "verification", fix_escalation_mod.RESOLVE_BY_HAND,
    )
    assert plan.bar_lowering is False
    assert plan.forge_write is None


def test_the_planned_answer_resolves_to_the_planned_team(ws: Workspace) -> None:
    plan = _RECIPE.plan(_parked(ws))
    assert plan.option is not None
    assert (
        dispatch_mod.resolver_team_for_answer(
            _emitted_question(), "implementation_fix", plan.option
        )
        == plan.team
    )

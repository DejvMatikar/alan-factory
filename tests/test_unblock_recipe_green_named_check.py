from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_recipes as recipes_mod
from alan.handover import OpenQuestion
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.teams import _verification_gates as verification_gates_mod
from alan.teams import verification as verif_mod
from alan.workspace import Workspace
from tests import _unblock_support as support

_RECIPE = next(r for r in recipes_mod.RECIPES if r.name == "green_named_check_blocker")
_HAND_RESOLVED = next(r for r in recipes_mod.RECIPES if r.name == "hand_resolved_declared_blocker")
_HEAD = "abc"
_TEXT = "backend must be green before this can merge"
_ALL_GREEN = ("backend", "frontend")
_NO_HEAD = "the forge reported no head commit to bound the retry against"
_CEILING = "the verifier already rejected this head twice; a third pass adds nothing"
_NAMES_NO_CHECK = "the declared blocker names no check on this pull request"
_MULTI_LINE = "the declared blocker spans more than one line"


@pytest.fixture(autouse=True)
def _verifier_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return support.parked_ws(tmp_path, blockers=[])


def _blocker_question(text: str) -> OpenQuestion:
    return support.question(
        question_id="declared_blocker_abcdef12",
        question_class="fix_escalation",
        text=text,
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        resolvers={declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "verification"},
    )


def _green(**overrides: object) -> pr_ready_mod.PrReadiness:
    fields: dict[str, object] = {"head_sha": _HEAD, "passing_checks": _ALL_GREEN}
    fields.update(overrides)
    return support.readiness(**fields)  # type: ignore[arg-type]


def _parked(
    ws: Workspace,
    *,
    text: str = _TEXT,
    ready: pr_ready_mod.ReadinessResult | None = None,
    review_stream: str | None = "",
    pr_number: int | None = 123,
) -> recipes_mod.RecipeContext:
    latest = support.handover(
        ws,
        from_team="implementation_fix",
        questions=[_blocker_question(text)],
        pr_number=pr_number,
    )
    return support.context(
        ws,
        latest=latest,
        pr_number=pr_number,
        ready=ready if ready is not None else _green(),
        recipe_deps=support.deps(review_stream=review_stream),
    )


def _verifier_rejections(count: int, *, commit_id: str = _HEAD) -> str:
    return "\n".join(
        support.review_line(commit_id=commit_id, review_id=index) for index in range(count)
    )


def _never_read(_pr: int, _forge: object) -> str | None:
    raise AssertionError("the review stream must not be read")


def _stream_forbidden(ctx: recipes_mod.RecipeContext) -> recipes_mod.RecipeContext:
    return replace(ctx, deps=replace(ctx.deps, review_stream=_never_read))


def test_a_declared_blocker_park_matches(ws: Workspace) -> None:
    assert _RECIPE.match(_parked(ws)) is True


def test_a_park_without_a_pull_request_does_not_match(ws: Workspace) -> None:
    assert _RECIPE.match(_parked(ws, pr_number=None)) is False


def test_a_park_without_a_question_does_not_match(ws: Workspace) -> None:
    assert _RECIPE.match(support.context(ws, latest=support.handover(ws))) is False


def test_another_question_id_does_not_match(ws: Workspace) -> None:
    latest = support.handover(ws, questions=[support.question()])
    assert _RECIPE.match(support.context(ws, latest=latest)) is False


def test_the_matcher_is_the_one_the_hand_resolved_recipe_uses() -> None:
    assert _RECIPE.match is _HAND_RESOLVED.match


def test_the_recipe_sits_after_the_head_moved_recipe_and_the_base_claim_recipe() -> None:
    names = [recipe.name for recipe in recipes_mod.RECIPES]
    assert names.index("declared_blocker") < names.index("hand_resolved_declared_blocker")
    assert names.index("hand_resolved_declared_blocker") < names.index("green_named_check_blocker")


@pytest.mark.parametrize("ready", [None, pr_ready_mod.ReadinessUnavailable("forge unavailable")])
def test_probe_waits_while_the_forge_is_unusable(
    ws: Workspace, ready: pr_ready_mod.ReadinessResult | None
) -> None:
    ctx = replace(_parked(ws), readiness=ready)
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet("forge unavailable")


def test_probe_waits_while_the_head_has_no_check_results(ws: Workspace) -> None:
    ready = support.readiness(head_sha=_HEAD, check_count=0, passing_checks=())
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.not_yet(
        "no check results at the head yet"
    )


def test_probe_declines_a_blocker_of_two_non_empty_lines(ws: Workspace) -> None:
    ctx = _parked(ws, text="backend is red\nfrontend is red")
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_MULTI_LINE)


def test_probe_reads_a_single_line_surrounded_by_blank_lines(ws: Workspace) -> None:
    ctx = _parked(ws, text=f"\n  {_TEXT}  \n\n")
    assert _RECIPE.probe(ctx).state == "ready"


def test_probe_declines_a_blocker_naming_no_check_the_pull_request_has(ws: Workspace) -> None:
    ctx = _parked(ws, text="the docs check must be green")
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_NAMES_NO_CHECK)


def test_probe_declines_when_results_exist_but_no_check_is_listed(ws: Workspace) -> None:
    ready = support.readiness(head_sha=_HEAD, check_count=1, passing_checks=())
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(_NAMES_NO_CHECK)


def test_probe_declines_a_park_whose_question_is_missing(ws: Workspace) -> None:
    ctx = replace(_parked(ws), question=None)
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_NAMES_NO_CHECK)


def test_probe_reads_check_names_case_sensitively(ws: Workspace) -> None:
    ctx = _parked(ws, text="BACKEND must be green")
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_NAMES_NO_CHECK)


def test_probe_lists_every_named_check_that_is_red(ws: Workspace) -> None:
    ready = _green(passing_checks=(), failing_checks=("frontend", "backend", "e2e"))
    ctx = _parked(ws, text="backend and frontend gate this", ready=ready)
    assert _RECIPE.probe(ctx) == recipes_mod.declined(
        "a check this blocker names is red: backend, frontend"
    )


def test_probe_treats_a_check_name_inside_a_longer_word_as_named(ws: Workspace) -> None:
    ready = _green(passing_checks=("build-docs",), failing_checks=("build",))
    ctx = _parked(ws, text="build-docs must be green", ready=ready)
    assert _RECIPE.probe(ctx) == recipes_mod.declined("a check this blocker names is red: build")


def test_probe_declines_when_a_named_check_is_red(ws: Workspace) -> None:
    ready = _green(passing_checks=("frontend",), failing_checks=("backend",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(
        "a check this blocker names is red: backend"
    )


def test_probe_waits_when_a_named_check_is_pending(ws: Workspace) -> None:
    ready = _green(passing_checks=("frontend",), pending_checks=("backend",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.not_yet(
        "a check this blocker names is pending: backend"
    )


def test_a_named_red_check_outranks_another_pending_check(ws: Workspace) -> None:
    ready = _green(passing_checks=(), failing_checks=("backend",), pending_checks=("e2e",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(
        "a check this blocker names is red: backend"
    )


def test_a_named_pending_check_outranks_another_red_check(ws: Workspace) -> None:
    ready = _green(passing_checks=(), pending_checks=("backend",), failing_checks=("e2e",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.not_yet(
        "a check this blocker names is pending: backend"
    )


def test_probe_declines_when_another_check_is_red(ws: Workspace) -> None:
    ready = _green(failing_checks=("e2e",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(
        "another check on the pull request is red: e2e"
    )


def test_probe_waits_when_another_check_is_pending(ws: Workspace) -> None:
    ready = _green(pending_checks=("e2e",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.not_yet(
        "another check on the pull request is pending: e2e"
    )


def test_another_red_check_outranks_another_pending_check(ws: Workspace) -> None:
    ready = _green(failing_checks=("e2e",), pending_checks=("lint",))
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(
        "another check on the pull request is red: e2e"
    )


def test_the_three_decline_reasons_are_pairwise_distinct(ws: Workspace) -> None:
    named_red = _RECIPE.probe(
        _parked(ws, ready=_green(passing_checks=(), failing_checks=("backend",)))
    )
    other_red = _RECIPE.probe(_parked(ws, ready=_green(failing_checks=("e2e",))))
    names_none = _RECIPE.probe(_parked(ws, text="the docs check must be green"))
    states = {named_red.state, other_red.state, names_none.state}
    reasons = {named_red.reason, other_red.reason, names_none.reason}
    assert states == {"declined"}
    assert len(reasons) == 3


@pytest.mark.parametrize(
    ("merge_state", "reason"),
    [
        ("BLOCKED", "merge state is BLOCKED"),
        ("", "merge state is unknown"),
        ("DIRTY", "merge state is DIRTY"),
        ("UNSTABLE", "merge state is UNSTABLE"),
    ],
)
def test_probe_declines_a_merge_state_that_is_not_green(
    ws: Workspace, merge_state: str, reason: str
) -> None:
    ready = _green(merge_state_status=merge_state)
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.declined(reason)


@pytest.mark.parametrize("merge_state", ["CLEAN", "HAS_HOOKS"])
def test_probe_is_ready_on_every_green_merge_state(ws: Workspace, merge_state: str) -> None:
    ready = _green(merge_state_status=merge_state)
    assert _RECIPE.probe(_parked(ws, ready=ready)) == recipes_mod.ready(
        "every check the declared blocker names is green at the current head"
    )


def test_probe_waits_when_a_green_read_carries_no_head_commit(ws: Workspace) -> None:
    ctx = _stream_forbidden(_parked(ws, ready=_green(head_sha="")))
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet(_NO_HEAD)


def test_probe_declines_a_green_park_with_no_pull_request_to_read_reviews_of(
    ws: Workspace,
) -> None:
    assert _RECIPE.probe(_parked(ws, pr_number=None)) == recipes_mod.declined("no pull request")


def test_probe_waits_while_the_review_stream_is_unavailable(ws: Workspace) -> None:
    assert _RECIPE.probe(_parked(ws, review_stream=None)) == recipes_mod.not_yet(
        "review stream unavailable"
    )


def test_probe_declines_when_a_human_requests_changes(ws: Workspace) -> None:
    stream = support.review_line(login="human-reviewer", commit_id=_HEAD)
    assert _RECIPE.probe(_parked(ws, review_stream=stream)) == recipes_mod.declined(
        "a human review requests changes"
    )


@pytest.mark.parametrize("count", [2, 3])
def test_probe_declines_once_the_verifier_has_rejected_the_head_twice(
    ws: Workspace, count: int
) -> None:
    ctx = _parked(ws, review_stream=_verifier_rejections(count))
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_CEILING)


def test_two_actionable_downgrades_at_an_unmoved_head_decline_the_retry(
    ws: Workspace,
) -> None:
    downgrade = verification_gates_mod._downgrade_output(  # noqa: SLF001
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n",
        support.readiness(failing_checks=("sonar",)),
        ("sonar",),
    )
    assert verif_mod.github_review_event(downgrade) == "REQUEST_CHANGES"
    ctx = _parked(ws, review_stream=_verifier_rejections(2))
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_CEILING)


@pytest.mark.parametrize(
    "stream",
    [
        "",
        _verifier_rejections(1),
        _verifier_rejections(2, commit_id="older"),
        _verifier_rejections(1) + "\n" + _verifier_rejections(1, commit_id="older"),
    ],
    ids=["no-reviews", "one-at-head", "two-at-a-stale-commit", "one-at-head-one-stale"],
)
def test_probe_is_ready_below_the_verifier_rejection_ceiling(ws: Workspace, stream: str) -> None:
    assert _RECIPE.probe(_parked(ws, review_stream=stream)).state == "ready"


def test_the_retry_bound_is_evaluated_after_the_merge_state(ws: Workspace) -> None:
    ctx = _parked(
        ws, ready=_green(merge_state_status="DIRTY"), review_stream=_verifier_rejections(2)
    )
    assert _RECIPE.probe(ctx) == recipes_mod.declined("merge state is DIRTY")


@pytest.mark.parametrize(
    "ready",
    [
        None,
        pr_ready_mod.ReadinessUnavailable("forge unavailable"),
        support.readiness(head_sha=_HEAD, check_count=0, passing_checks=()),
        _green(failing_checks=("backend",)),
        _green(pending_checks=("backend",)),
        _green(failing_checks=("e2e",)),
        _green(pending_checks=("e2e",)),
        _green(merge_state_status="BLOCKED"),
    ],
    ids=[
        "no-readiness",
        "forge-down",
        "no-results",
        "named-red",
        "named-pending",
        "other-red",
        "other-pending",
        "merge-state",
    ],
)
def test_no_outcome_before_the_retry_bound_reads_the_review_stream(
    ws: Workspace, ready: pr_ready_mod.ReadinessResult | None
) -> None:
    ctx = replace(_stream_forbidden(_parked(ws)), readiness=ready)
    assert _RECIPE.probe(ctx).state != "ready"


@pytest.mark.parametrize("text", ["backend\nfrontend", "the docs check"])
def test_a_blocker_text_decline_never_reads_the_review_stream(ws: Workspace, text: str) -> None:
    ctx = _stream_forbidden(_parked(ws, text=text))
    assert _RECIPE.probe(ctx).state == "declined"


def test_plan_answers_the_blocker_does_not_hold_option_towards_verification(
    ws: Workspace,
) -> None:
    plan = _RECIPE.plan(_parked(ws))
    assert (plan.kind, plan.team, plan.option) == (
        "answer",
        "verification",
        declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
    )
    assert plan.forge_write is None
    assert plan.bar_lowering is False
    assert "green_named_check_blocker" in plan.instructions


def test_the_planned_answer_resolves_to_the_planned_team(ws: Workspace) -> None:
    ctx = _parked(ws)
    plan = _RECIPE.plan(ctx)
    assert ctx.question is not None
    assert plan.option is not None
    assert (
        dispatch_mod.resolver_team_for_answer(ctx.question, "implementation_fix", plan.option)
        == plan.team
    )

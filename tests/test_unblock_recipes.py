from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_recipes as recipes_mod
from alan.engine_config import ForgeTarget
from alan.handover import ArtifactRef
from alan.park import Blocker, ParkReason
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _pr_merge_escalation as pr_merge_escalation_mod
from alan.teams import _pr_post as pr_post_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.teams import _pr_state_paths as pr_state_paths_mod
from alan.teams._pr_branch import VerifierIdentity
from alan.workspace import Status, Workspace
from tests import _unblock_support as support
from tests._pr_branch_support import _WIDGET_FORGE

_RECIPES = {recipe.name: recipe for recipe in recipes_mod.RECIPES}
_VERIFIER = VerifierIdentity(support.VERIFIER)


@pytest.fixture(autouse=True)
def _verifier_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return support.parked_ws(tmp_path)


def _blocker(name: str, detail: str = "d") -> Blocker:
    return Blocker(kind="reason", name=name, detail=detail)


def test_probe_constructors_carry_state_and_reason() -> None:
    assert recipes_mod.ready("r") == recipes_mod.Probe("ready", "r")
    assert recipes_mod.not_yet("n") == recipes_mod.Probe("not_yet", "n")
    assert recipes_mod.declined("d") == recipes_mod.Probe("declined", "d")


def test_blocking_question_is_none_without_a_handover() -> None:
    assert recipes_mod.blocking_question(None) is None


def test_blocking_question_skips_answered_non_blocking_and_team_questions(ws: Workspace) -> None:
    ho = support.handover(
        ws,
        questions=[
            support.question(question_id="answered", answer="x"),
            support.question(question_id="advisory", blocking=False),
            support.question(question_id="team", resolver_role="verification"),
            support.question(question_id="human"),
        ],
    )
    found = recipes_mod.blocking_question(ho)
    assert found is not None
    assert found.id == "human"


def test_single_blocker_requires_exactly_one(tmp_path: Path) -> None:
    none = support.parked_ws(tmp_path, title="none", blockers=[])
    two = support.parked_ws(tmp_path, title="two", blockers=[_blocker("a"), _blocker("b")])
    one = support.parked_ws(tmp_path, title="one", blockers=[_blocker("only")])
    assert recipes_mod.single_blocker(none) is None
    assert recipes_mod.single_blocker(two) is None
    assert recipes_mod.single_blocker(one) == _blocker("only")


def test_other_author_requested_changes_uses_the_latest_state_per_login() -> None:
    reviews = tuple(
        pr_post_mod.parse_review_objects(
            "\n".join(
                [
                    support.review_line(login="human", state="CHANGES_REQUESTED"),
                    support.review_line(login="human", state="APPROVED"),
                    support.review_line(login=None),
                    support.review_line(login=support.VERIFIER),
                ]
            )
        )
    )
    assert recipes_mod.other_author_requested_changes(reviews, _VERIFIER) is False


def test_other_author_requested_changes_detects_a_standing_request() -> None:
    reviews = pr_post_mod.parse_review_objects(
        support.review_line(login="human", state="CHANGES_REQUESTED")
    )
    assert recipes_mod.other_author_requested_changes(reviews, _VERIFIER) is True


def test_review_login_rejects_non_string_logins() -> None:
    assert recipes_mod._review_login({"user": {"login": 5}}) is None  # noqa: SLF001
    assert recipes_mod._review_login({"user": "x"}) is None  # noqa: SLF001


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], "none"),
        ([support.review_line(commit_id="old")], "stale"),
        ([support.review_line(commit_id="abc")], "current"),
        ([support.review_line(commit_id="old"), support.review_line(commit_id="abc")], "current"),
        ([support.review_line(state="APPROVED", commit_id="old")], "none"),
        ([support.review_line(login="human", commit_id="old")], "none"),
    ],
)
def test_verifier_review_position(lines: list[str], expected: str) -> None:
    reviews = pr_post_mod.parse_review_objects("\n".join(lines))
    assert recipes_mod.verifier_review_position(reviews, _VERIFIER, "abc") == expected


def test_match_recipes_keeps_table_order(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws))
    assert [r.name for r in recipes_mod.match_recipes(ctx)] == ["stale_review", "green_not_merging"]


def test_stale_review_does_not_match_without_a_pr(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws, pr_number=None), pr_number=None)
    assert _RECIPES["stale_review"].match(ctx) is False


def test_stale_review_matches_a_merge_block_park_classified_stale(tmp_path: Path) -> None:
    parked = support.parked_ws(
        tmp_path,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
        blockers=[_blocker("review_decision"), _blocker("merge_state_status")],
    )
    ctx = support.context(parked, latest=support.handover(parked))
    assert _RECIPES["stale_review"].match(ctx) is True


def test_stale_review_does_not_match_an_unrelated_blocker(tmp_path: Path) -> None:
    parked = support.parked_ws(tmp_path, blockers=[_blocker("pr-merge-conflict")])
    ctx = support.context(parked, latest=support.handover(parked))
    assert _RECIPES["stale_review"].match(ctx) is False


def _stale_ctx(
    ws: Workspace,
    *,
    ready: object = None,
    stream: str | None = "",
    **deps_kw: object,
) -> recipes_mod.RecipeContext:
    readiness = (
        ready if ready is not None else support.readiness(review_decision="CHANGES_REQUESTED")
    )
    return support.context(
        ws,
        latest=support.handover(ws),
        ready=readiness,  # type: ignore[arg-type]
        recipe_deps=support.deps(review_stream=stream, **deps_kw),  # type: ignore[arg-type]
    )


def test_stale_review_probe_waits_without_readiness(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws), ready=None)
    assert _RECIPES["stale_review"].probe(ctx).state == "not_yet"


def test_stale_review_probe_waits_on_pending_checks(ws: Workspace) -> None:
    ctx = _stale_ctx(
        ws, ready=support.readiness(review_decision="CHANGES_REQUESTED", pending_checks=("e2e",))
    )
    assert _RECIPES["stale_review"].probe(ctx) == recipes_mod.not_yet("checks pending: e2e")


@pytest.mark.parametrize(
    "ready",
    [
        support.readiness(review_decision=""),
        support.readiness(review_decision="CHANGES_REQUESTED", failing_checks=("x",)),
        support.readiness(review_decision="CHANGES_REQUESTED", merge_state_status="DIRTY"),
    ],
)
def test_stale_review_probe_declines_when_not_a_lone_review(ws: Workspace, ready: object) -> None:
    ctx = _stale_ctx(ws, ready=ready)
    assert _RECIPES["stale_review"].probe(ctx).state == "declined"


def test_stale_review_probe_accepts_a_blocked_state_with_nothing_pending(ws: Workspace) -> None:
    ctx = _stale_ctx(
        ws,
        ready=support.readiness(review_decision="CHANGES_REQUESTED", merge_state_status="BLOCKED"),
        stream=support.review_line(commit_id="old"),
    )
    assert _RECIPES["stale_review"].probe(ctx).state == "ready"


def test_stale_review_probe_declines_on_human_feedback(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, feedback=support.snapshot(human_unresolved=1))
    assert _RECIPES["stale_review"].probe(ctx).state == "declined"


def test_stale_review_probe_waits_when_feedback_is_unavailable(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, feedback=support.snapshot(ok=False, failure_reason="rate"))
    assert _RECIPES["stale_review"].probe(ctx) == recipes_mod.not_yet("feedback unavailable: rate")


def test_stale_review_probe_waits_when_the_review_stream_is_unavailable(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, stream=None)
    assert _RECIPES["stale_review"].probe(ctx) == recipes_mod.not_yet("review stream unavailable")


def test_stale_review_probe_declines_a_human_review(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, stream=support.review_line(login="human"))
    assert _RECIPES["stale_review"].probe(ctx) == recipes_mod.declined(
        "a human review requests changes"
    )


def test_stale_review_probe_declines_a_current_head_verifier_review(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, stream=support.review_line(commit_id="abc"))
    assert _RECIPES["stale_review"].probe(ctx).state == "declined"


def test_stale_review_probe_declines_without_any_verifier_review(ws: Workspace) -> None:
    ctx = _stale_ctx(ws, stream="")
    assert _RECIPES["stale_review"].probe(ctx) == recipes_mod.declined(
        "no verifier review requests changes"
    )


def test_stale_review_plan_dismisses_then_dispatches_pr_merge(ws: Workspace) -> None:
    calls: list[tuple[int, str, ForgeTarget]] = []
    ctx = _stale_ctx(ws, dismiss_calls=calls)
    plan = _RECIPES["stale_review"].plan(ctx)
    assert (plan.kind, plan.team, plan.option, plan.bar_lowering) == (
        "dispatch",
        "pr_merge",
        None,
        False,
    )
    assert plan.forge_write is not None
    assert plan.forge_write() is True
    assert calls == [(123, ws.task_id, _WIDGET_FORGE)]


def test_stale_review_plan_defaults_a_missing_pr_to_zero(ws: Workspace) -> None:
    calls: list[tuple[int, str, ForgeTarget]] = []
    ctx = support.context(
        ws,
        latest=support.handover(ws, pr_number=None),
        pr_number=None,
        recipe_deps=support.deps(dismiss_calls=calls),
    )
    plan = _RECIPES["stale_review"].plan(ctx)
    assert plan.forge_write is not None
    plan.forge_write()
    assert calls[0][0] == 0


def _escalated(
    tmp_path: Path, *, detail: str = recipes_mod.NO_COMMITS_PREFIX + " — escalating."
) -> Workspace:
    return support.parked_ws(
        tmp_path,
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=[_blocker(recipes_mod.ESCALATION_BLOCKER, detail)],
    )


def test_no_commit_escalation_matches_the_escalation_shape(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(ws, latest=support.handover(ws, handover_type="escalation"))
    assert _RECIPES["no_commit_escalation"].match(ctx) is True


def test_no_commit_escalation_rejects_a_question_park(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ho = support.handover(ws, questions=[support.question()])
    assert _RECIPES["no_commit_escalation"].match(support.context(ws, latest=ho)) is False


def test_no_commit_escalation_rejects_other_escalations(tmp_path: Path) -> None:
    ws = _escalated(tmp_path, detail="worker crashed")
    assert (
        _RECIPES["no_commit_escalation"].match(support.context(ws, latest=support.handover(ws)))
        is False
    )


def test_no_commit_escalation_rejects_awaiting_input(ws: Workspace) -> None:
    assert (
        _RECIPES["no_commit_escalation"].match(support.context(ws, latest=support.handover(ws)))
        is False
    )


def test_no_commit_escalation_rejects_without_a_blocker(tmp_path: Path) -> None:
    ws = support.parked_ws(
        tmp_path, status=Status.ESCALATED, park_reason=ParkReason.ESCALATED, blockers=[]
    )
    assert (
        _RECIPES["no_commit_escalation"].match(support.context(ws, latest=support.handover(ws)))
        is False
    )


@pytest.mark.parametrize(
    ("ready", "state", "reason"),
    [
        (pr_ready_mod.ReadinessUnavailable("forge unavailable"), "not_yet", "forge unavailable"),
        (
            support.readiness(check_count=0, passing_checks=()),
            "not_yet",
            "no check results at the head yet",
        ),
        (support.readiness(pending_checks=("a",)), "not_yet", "checks pending: a"),
        (support.readiness(failing_checks=("b",)), "declined", "checks failing: b"),
        (
            support.readiness(merge_state_status="DIRTY"),
            "declined",
            "merge conflict; the reconcile actor owns DIRTY branches",
        ),
    ],
)
def test_no_commit_escalation_probe_reads_readiness(
    tmp_path: Path, ready: object, state: str, reason: str
) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(ws, latest=support.handover(ws), ready=ready)  # type: ignore[arg-type]
    assert _RECIPES["no_commit_escalation"].probe(ctx) == recipes_mod.Probe(state, reason)  # type: ignore[arg-type]


def test_no_commit_escalation_probe_declines_a_human_comment(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(),
        recipe_deps=support.deps(feedback=support.snapshot(human_items=1)),
    )
    assert _RECIPES["no_commit_escalation"].probe(ctx).state == "declined"


def test_no_commit_escalation_probe_declines_a_human_review(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(review_decision="CHANGES_REQUESTED"),
        recipe_deps=support.deps(review_stream=support.review_line(login="human")),
    )
    assert _RECIPES["no_commit_escalation"].probe(ctx).state == "declined"


def test_no_commit_escalation_probe_is_ready_past_a_verifier_review(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(review_decision="CHANGES_REQUESTED"),
        recipe_deps=support.deps(review_stream=support.review_line(commit_id="abc")),
    )
    assert _RECIPES["no_commit_escalation"].probe(ctx).state == "ready"


def test_no_commit_escalation_probe_is_ready_when_green(tmp_path: Path) -> None:
    ws = _escalated(tmp_path)
    ctx = support.context(ws, latest=support.handover(ws), ready=support.readiness())
    assert _RECIPES["no_commit_escalation"].probe(ctx).state == "ready"


def test_no_commit_escalation_plan_escalates_to_pr_merge_on_the_second_attempt(
    tmp_path: Path,
) -> None:
    ws = _escalated(tmp_path)
    first = _RECIPES["no_commit_escalation"].plan(support.context(ws, latest=support.handover(ws)))
    second = _RECIPES["no_commit_escalation"].plan(
        support.context(ws, latest=support.handover(ws), attempts_fp=1)
    )
    assert (first.kind, first.team) == ("dispatch", "verification")
    assert second.team == "pr_merge"


def _declared(
    ws: Workspace, text: str = "test_x fails on base fed07dd5, independent of this PR"
) -> recipes_mod.RecipeContext:
    q = support.question(
        question_id="declared_blocker_inv_005",
        question_class="fix_escalation",
        text=text,
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        resolvers={declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "verification"},
    )
    return support.context(
        ws, latest=support.handover(ws, from_team="implementation_fix", questions=[q])
    )


def test_declared_blocker_matches_its_question_id(ws: Workspace) -> None:
    assert _RECIPES["declared_blocker"].match(_declared(ws)) is True


def test_declared_blocker_rejects_other_questions(ws: Workspace) -> None:
    assert (
        _RECIPES["declared_blocker"].match(
            support.context(ws, latest=support.handover(ws, questions=[support.question()]))
        )
        is False
    )


def test_declared_blocker_rejects_without_a_question(ws: Workspace) -> None:
    assert (
        _RECIPES["declared_blocker"].match(support.context(ws, latest=support.handover(ws)))
        is False
    )


def test_declared_blocker_probe_declines_a_non_ci_claim(ws: Workspace) -> None:
    ctx = _declared(ws, text="needs a production secret")
    assert _RECIPES["declared_blocker"].probe(ctx).state == "declined"


def test_declared_blocker_probe_declines_without_a_question(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws))
    assert _RECIPES["declared_blocker"].probe(ctx).state == "declined"


@pytest.mark.parametrize(
    "ci",
    [None, support.main_ci(status="stale"), support.main_ci(red_count=None)],
)
def test_declared_blocker_probe_waits_for_fresh_main_ci(ws: Workspace, ci: object) -> None:
    ctx = _declared(ws)
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "deps": support.deps(ci=ci)})  # type: ignore[arg-type]
    assert _RECIPES["declared_blocker"].probe(ctx) == recipes_mod.not_yet(
        "main-branch CI status is not fresh"
    )


def test_declared_blocker_probe_declines_when_main_is_red(ws: Workspace) -> None:
    ctx = _declared(ws)
    ctx = recipes_mod.RecipeContext(
        **{**ctx.__dict__, "deps": support.deps(ci=support.main_ci(red_count=2))}
    )
    assert _RECIPES["declared_blocker"].probe(ctx).state == "declined"


def test_declared_blocker_probe_is_ready_when_main_is_green(ws: Workspace) -> None:
    ctx = _declared(ws)
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "deps": support.deps(ci=support.main_ci())})
    assert _RECIPES["declared_blocker"].probe(ctx).state == "ready"


def test_declared_blocker_plan_answers_the_unfounded_option(ws: Workspace) -> None:
    plan = _RECIPES["declared_blocker"].plan(_declared(ws))
    assert (plan.kind, plan.team, plan.option) == (
        "answer",
        "verification",
        declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
    )


def _artifacts(*shas: str) -> list[ArtifactRef]:
    return [ArtifactRef(path=sha, description=f"{sha} msg") for sha in shas]


def _hand_resolved(
    ws: Workspace,
    *,
    commits: tuple[str, ...] = ("49ce49f", "90af47e", "bfd826c", "dd4b85f"),
    from_team: str = "implementation",
    question_id: str = "declared_blocker_inv_004",
    head_sha: str = "362fc55",
) -> recipes_mod.RecipeContext:
    q = support.question(
        question_id=question_id,
        question_class="fix_escalation",
        text="[BLOCKING] needs a human to run scripts/x.py",
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        resolvers={declared_blocker_mod.HAND_RESOLVE_OPTION: "implementation_fix"},
    )
    ho = support.handover(ws, from_team=from_team, questions=[q], artifacts=_artifacts(*commits))
    return support.context(ws, latest=ho, ready=support.readiness(head_sha=head_sha))


def test_hand_resolved_matches_the_same_context_as_declared_blocker(ws: Workspace) -> None:
    assert _RECIPES["hand_resolved_declared_blocker"].match(_declared(ws)) is True


def test_match_recipes_orders_declared_blocker_before_hand_resolved(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, blockers=[])
    ctx = _declared(ws)
    assert [r.name for r in recipes_mod.match_recipes(ctx)] == [
        "declared_blocker",
        "hand_resolved_declared_blocker",
        "green_named_check_blocker",
    ]


def test_hand_resolved_probe_waits_without_readiness(ws: Workspace) -> None:
    ctx = _hand_resolved(ws)
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "readiness": None})
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.not_yet(
        "forge unavailable"
    )


def test_hand_resolved_probe_waits_when_gh_is_unavailable(ws: Workspace) -> None:
    ctx = _hand_resolved(ws)
    unavailable = pr_ready_mod.ReadinessUnavailable("forge unavailable")
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "readiness": unavailable})
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.not_yet(
        "forge unavailable"
    )


def test_hand_resolved_probe_waits_without_a_head_sha(ws: Workspace) -> None:
    ctx = _hand_resolved(ws)
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "readiness": support.readiness(head_sha="")})
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.not_yet(
        "forge unavailable"
    )


def test_hand_resolved_probe_declines_without_a_blocking_handover(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws), ready=support.readiness(head_sha="abc"))
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.declined(
        "the park recorded no worker commits to compare the head against"
    )


def test_hand_resolved_probe_declines_without_park_commits(ws: Workspace) -> None:
    ctx = _hand_resolved(ws, commits=(), from_team="implementation_fix")
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.declined(
        "the park recorded no worker commits to compare the head against"
    )


def test_hand_resolved_probe_declines_when_the_artifacts_are_not_commit_shaped(
    ws: Workspace,
) -> None:
    q = support.question(
        question_id="declared_blocker_inv_006",
        question_class="fix_escalation",
        text="[BLOCKING] needs a human",
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
    )
    ho = support.handover(
        ws,
        from_team="implementation_fix",
        questions=[q],
        artifacts=[ArtifactRef(path="alan/unblock_recipes.py", description="a file, not a sha")],
    )
    ctx = support.context(ws, latest=ho, ready=support.readiness(head_sha="362fc55"))
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.declined(
        "the park recorded no worker commits to compare the head against"
    )


def test_hand_resolved_probe_declines_when_the_head_matches_a_worker_commit(
    ws: Workspace,
) -> None:
    ctx = _hand_resolved(
        ws,
        commits=("b412c46", "a188ecb", "54b76df"),
        from_team="implementation_fix",
        question_id="declared_blocker_inv_005",
        head_sha="b412c46",
    )
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.declined(
        "the head still matches a worker commit from the park"
    )


def test_hand_resolved_probe_tolerates_abbreviated_commits(ws: Workspace) -> None:
    ctx = _hand_resolved(ws, commits=("49ce49f",), head_sha="49ce49f7abc123def456")
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx).state == "declined"


def test_hand_resolved_probe_declines_when_the_fleet_moved_the_head(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = pr_state_paths_mod.SystemHeadMarker(head_sha="deadbeef", rung="rebase", forwarded=True)
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: marker)
    ctx = _hand_resolved(ws, head_sha="deadbeef")
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.declined(
        "the fleet's own rebase machinery moved the head"
    )


def test_hand_resolved_probe_is_ready_when_the_marker_is_stale(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = pr_state_paths_mod.SystemHeadMarker(head_sha="olderhead", rung="rebase")
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: marker)
    ctx = _hand_resolved(ws, head_sha="362fc55")
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx).state == "ready"


def test_hand_resolved_probe_is_ready_when_a_human_pushed_past_the_park(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: None)
    ctx = _hand_resolved(ws)
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx) == recipes_mod.ready(
        "the head moved to a commit no fleet actor produced since the park"
    )


def test_hand_resolved_probe_ignores_failing_and_pending_checks_at_the_new_head(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: None)
    ctx = _hand_resolved(ws)
    ctx = recipes_mod.RecipeContext(
        **{
            **ctx.__dict__,
            "readiness": support.readiness(
                head_sha="362fc55", failing_checks=("backend",), pending_checks=("e2e",)
            ),
        }
    )
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx).state == "ready"


def test_hand_resolved_plan_answers_the_hand_resolve_option(ws: Workspace) -> None:
    plan = _RECIPES["hand_resolved_declared_blocker"].plan(_hand_resolved(ws))
    assert (plan.kind, plan.team, plan.option) == (
        "answer",
        "implementation_fix",
        declared_blocker_mod.HAND_RESOLVE_OPTION,
    )


def test_hand_resolved_reparks_at_the_fix_pass_own_commits_decline(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: None)
    ctx = _hand_resolved(
        ws,
        commits=("b412c46", "a188ecb", "54b76df"),
        from_team="implementation_fix",
        question_id="declared_blocker_inv_005",
        head_sha="b412c46",
    )
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx).state == "declined"


def test_hand_resolved_reparks_at_a_commit_less_fix_pass_decline(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: None)
    ctx = _hand_resolved(ws, commits=(), from_team="implementation_fix", head_sha="b412c46")
    assert _RECIPES["hand_resolved_declared_blocker"].probe(ctx).state == "declined"


def _crashed(
    tmp_path: Path, *, team: str | None = None, pending_first_hop: str | None = None
) -> Workspace:
    return support.parked_ws(
        tmp_path,
        park_reason=ParkReason.CRASH,
        blockers=[_blocker("reason", "restart")],
        team=team,
        pending_first_hop=pending_first_hop,
    )


def test_crash_park_matches_only_crash_parks(tmp_path: Path, ws: Workspace) -> None:
    assert _RECIPES["crash_park"].match(support.context(_crashed(tmp_path), pr_number=None)) is True
    assert _RECIPES["crash_park"].match(support.context(ws, latest=support.handover(ws))) is False


def test_crash_team_prefers_the_proposed_next_hop(tmp_path: Path) -> None:
    ws = _crashed(tmp_path)
    ho = support.handover(ws, from_team="verification", suggestions=["implementation_fix"])
    assert (
        recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None))
        == "implementation_fix"
    )


def test_crash_team_falls_back_to_the_handover_author(tmp_path: Path) -> None:
    ws = _crashed(tmp_path)
    ho = support.handover(ws, from_team="verification", suggestions=["research"])
    assert recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None)) == "verification"


def test_crash_team_falls_back_to_the_workspace_team(tmp_path: Path) -> None:
    ws = _crashed(tmp_path, team="implementation_plan")
    ho = support.handover(ws, from_team="decomposer")
    assert (
        recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None))
        == "implementation_plan"
    )


def test_crash_team_is_none_without_any_dispatchable_team(tmp_path: Path) -> None:
    assert recipes_mod.crash_team(support.context(_crashed(tmp_path), pr_number=None)) is None


def test_crash_team_resolves_the_pending_first_hop_after_brief_refine(tmp_path: Path) -> None:
    ws = _crashed(tmp_path, pending_first_hop="implementation_plan")
    ho = support.handover(ws, from_team="brief_refine")
    assert (
        recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None))
        == "implementation_plan"
    )


def test_crash_team_declines_brief_refine_without_a_pending_first_hop(tmp_path: Path) -> None:
    ws = _crashed(tmp_path)
    ho = support.handover(ws, from_team="brief_refine")
    assert recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None)) is None


def test_crash_team_declines_a_pending_first_hop_naming_dependency_sweep(tmp_path: Path) -> None:
    ws = _crashed(tmp_path, pending_first_hop="dependency_sweep")
    ho = support.handover(ws, from_team="brief_refine")
    assert recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None)) is None


def test_crash_team_declines_a_pending_first_hop_naming_brief_refine_itself(
    tmp_path: Path,
) -> None:
    ws = _crashed(tmp_path, pending_first_hop="brief_refine")
    ho = support.handover(ws, from_team="brief_refine")
    assert recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None)) is None


def test_crash_team_ignores_the_pending_first_hop_from_another_team(tmp_path: Path) -> None:
    ws = _crashed(tmp_path, pending_first_hop="implementation_plan")
    ho = support.handover(ws, from_team="verification", suggestions=["research"])
    assert recipes_mod.crash_team(support.context(ws, latest=ho, pr_number=None)) == "verification"


def test_crash_team_declines_a_pending_first_hop_without_any_handover(tmp_path: Path) -> None:
    ws = _crashed(tmp_path, pending_first_hop="implementation_plan")
    assert recipes_mod.crash_team(support.context(ws, pr_number=None)) is None


def test_crash_park_probe_declines_without_a_team(tmp_path: Path) -> None:
    ctx = support.context(_crashed(tmp_path), pr_number=None)
    assert _RECIPES["crash_park"].probe(ctx).state == "declined"


def test_crash_park_probe_names_a_missing_fleet_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: False)
    ctx = support.context(_crashed(tmp_path, team="verification"), pr_number=None)
    probe = _RECIPES["crash_park"].probe(ctx)
    assert probe.state == "declined"
    assert recipes_mod.FLEET_GRANT_MISSING in probe.reason


def test_crash_park_probe_and_plan_dispatch_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    ctx = support.context(_crashed(tmp_path, team="verification"), pr_number=None)
    assert _RECIPES["crash_park"].probe(ctx) == recipes_mod.ready("fresh dispatch of verification")
    plan = _RECIPES["crash_park"].plan(ctx)
    assert (plan.kind, plan.team) == ("dispatch", "verification")


def test_crash_park_probe_and_plan_dispatch_the_pending_first_hop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    ws = _crashed(tmp_path, pending_first_hop="implementation_plan")
    ho = support.handover(ws, from_team="brief_refine")
    ctx = support.context(ws, latest=ho, pr_number=None)
    assert _RECIPES["crash_park"].probe(ctx) == recipes_mod.ready(
        "fresh dispatch of implementation_plan"
    )
    plan = _RECIPES["crash_park"].plan(ctx)
    assert (plan.kind, plan.team) == ("dispatch", "implementation_plan")
    assert "crash_park" in plan.instructions


def test_crash_park_probe_names_a_missing_fleet_grant_for_the_pending_first_hop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: False)
    ws = _crashed(tmp_path, pending_first_hop="implementation_plan")
    ho = support.handover(ws, from_team="brief_refine")
    ctx = support.context(ws, latest=ho, pr_number=None)
    probe = _RECIPES["crash_park"].probe(ctx)
    assert probe.state == "declined"
    assert recipes_mod.FLEET_GRANT_MISSING in probe.reason


def test_crash_park_plan_defaults_to_verification_without_a_team(tmp_path: Path) -> None:
    assert (
        _RECIPES["crash_park"].plan(support.context(_crashed(tmp_path), pr_number=None)).team
        == "verification"
    )


def test_green_not_merging_matches_merge_parks_except_delivered(tmp_path: Path) -> None:
    timeout = support.parked_ws(tmp_path, title="t")
    failed = support.parked_ws(tmp_path, title="f", blockers=[_blocker("pr-merge-failed")])
    other = support.parked_ws(tmp_path, title="o", blockers=[_blocker("escalation")])
    recipe = _RECIPES["green_not_merging"]
    assert recipe.match(support.context(timeout, latest=support.handover(timeout))) is True
    assert recipe.match(support.context(failed, latest=support.handover(failed))) is True
    assert recipe.match(support.context(other, latest=support.handover(other))) is False
    assert (
        recipe.match(
            support.context(timeout, latest=support.handover(timeout, from_team="deploy_verify"))
        )
        is False
    )
    assert (
        recipe.match(
            support.context(
                timeout, latest=support.handover(timeout, pr_number=None), pr_number=None
            )
        )
        is False
    )


def test_green_not_merging_skips_codeowners_refusal(tmp_path: Path) -> None:
    recipe = _RECIPES["green_not_merging"]
    for kind in pr_merge_escalation_mod.OWNERSHIP_KINDS:
        ws = support.parked_ws(tmp_path, title=kind, blockers=[_blocker(f"pr-merge-{kind}")])
        assert recipe.match(support.context(ws, latest=support.handover(ws))) is False


def test_green_not_merging_probe_declines_a_review_request(ws: Workspace) -> None:
    ctx = support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(review_decision="CHANGES_REQUESTED"),
    )
    assert _RECIPES["green_not_merging"].probe(ctx) == recipes_mod.declined(
        "a review requests changes"
    )


def test_green_not_merging_probe_declines_a_blocked_state(ws: Workspace) -> None:
    ctx = support.context(
        ws, latest=support.handover(ws), ready=support.readiness(merge_state_status="BLOCKED")
    )
    assert _RECIPES["green_not_merging"].probe(ctx) == recipes_mod.declined(
        "merge state is BLOCKED"
    )


def test_green_not_merging_probe_names_an_unknown_state(ws: Workspace) -> None:
    ctx = support.context(
        ws, latest=support.handover(ws), ready=support.readiness(merge_state_status="")
    )
    assert _RECIPES["green_not_merging"].probe(ctx) == recipes_mod.declined(
        "merge state is unknown"
    )


def test_green_not_merging_probe_waits_on_pending(ws: Workspace) -> None:
    ctx = support.context(
        ws, latest=support.handover(ws), ready=support.readiness(pending_checks=("a",))
    )
    assert _RECIPES["green_not_merging"].probe(ctx).state == "not_yet"


def test_green_not_merging_probe_declines_human_feedback(ws: Workspace) -> None:
    ctx = support.context(
        ws,
        latest=support.handover(ws),
        ready=support.readiness(),
        recipe_deps=support.deps(feedback=support.snapshot(human_unresolved=2)),
    )
    assert _RECIPES["green_not_merging"].probe(ctx).state == "declined"


def test_green_not_merging_probe_and_plan(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws), ready=support.readiness())
    assert _RECIPES["green_not_merging"].probe(ctx) == recipes_mod.ready(
        "the pull request is mergeable"
    )
    plan = _RECIPES["green_not_merging"].plan(ctx)
    assert (plan.kind, plan.team, plan.option) == ("dispatch", "pr_merge", None)


def _transient(tmp_path: Path, *, timestamp: datetime | None = None) -> recipes_mod.RecipeContext:
    ws = support.parked_ws(tmp_path, blockers=[_blocker("pr-merge-failed")])
    q = support.question(
        question_id="pr-merge-failed",
        text="PR #123 failed checks: Backend tests + coverage.",
        options=("fix it", recipes_mod.TRANSIENT_RETRY_OPTION),
    )
    return support.context(
        ws,
        latest=support.handover(ws, questions=[q], timestamp=timestamp),
        ready=support.readiness(passing_checks=("Backend tests + coverage",)),
        now=100.0,
    )


def _anchor(monkeypatch: pytest.MonkeyPatch, **fields: object) -> None:
    base: dict[str, object] = {"head_sha": "abc", "count": 1, "lifetime": 1, "checks": ()}
    base.update(fields)
    state = pr_state_paths_mod.GateRerunState(**base)  # type: ignore[arg-type]
    monkeypatch.setattr(pr_state_paths_mod, "load_gate_rerun_state", lambda *_a, **_k: state)


def test_transient_failed_check_matches_only_the_failed_shape(
    tmp_path: Path, ws: Workspace
) -> None:
    assert _RECIPES["transient_failed_check"].match(_transient(tmp_path)) is True
    assert (
        _RECIPES["transient_failed_check"].match(support.context(ws, latest=support.handover(ws)))
        is False
    )
    mismatch = support.parked_ws(tmp_path, title="m", blockers=[_blocker("pr-merge-timeout")])
    q = support.question(question_id="pr-merge-failed")
    assert (
        _RECIPES["transient_failed_check"].match(
            support.context(mismatch, latest=support.handover(mismatch, questions=[q]))
        )
        is False
    )


def test_named_checks_prefers_the_anchor_then_the_allowlist() -> None:
    anchored = pr_state_paths_mod.GateRerunState(head_sha="abc", count=1, lifetime=1, checks=("x",))
    bare = pr_state_paths_mod.GateRerunState(head_sha="abc", count=1, lifetime=1)
    assert recipes_mod.named_checks("anything", anchored) == frozenset({"x"})
    assert recipes_mod.named_checks("failed: Backend tests + coverage", bare) == frozenset(
        {"Backend tests + coverage"}
    )
    assert recipes_mod.named_checks("failed: something else", bare) == frozenset()


def test_transient_probe_declines_after_the_watch_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch)
    ctx = _transient(tmp_path, timestamp=datetime.fromtimestamp(0.0, tz=UTC) - timedelta(hours=2))
    ctx = recipes_mod.RecipeContext(**{**ctx.__dict__, "now": recipes_mod.WATCH_WINDOW_S * 10})
    assert _RECIPES["transient_failed_check"].probe(ctx).state == "declined"


@pytest.mark.parametrize("fields", [{"head_sha": None}, {"count": 0}])
def test_transient_probe_declines_without_a_rerun_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fields: dict[str, object]
) -> None:
    _anchor(monkeypatch, **fields)
    assert _RECIPES["transient_failed_check"].probe(_transient(tmp_path)).state == "declined"


def test_transient_probe_waits_without_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch)
    ctx = recipes_mod.RecipeContext(**{**_transient(tmp_path).__dict__, "readiness": None})
    assert _RECIPES["transient_failed_check"].probe(ctx) == recipes_mod.not_yet("forge unavailable")


def test_transient_probe_declines_when_the_head_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch, head_sha="older")
    assert _RECIPES["transient_failed_check"].probe(_transient(tmp_path)) == recipes_mod.declined(
        "the head moved since the gate rerun"
    )


def test_transient_probe_declines_without_named_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch)
    ctx = recipes_mod.RecipeContext(**{**_transient(tmp_path).__dict__, "question": None})
    assert _RECIPES["transient_failed_check"].probe(ctx) == recipes_mod.declined(
        "no named check to watch"
    )


@pytest.mark.parametrize(
    "ready",
    [
        support.readiness(check_count=0, passing_checks=()),
        support.readiness(check_count=0, passing_checks=("Backend tests + coverage",)),
        support.readiness(failing_checks=("Backend tests + coverage",)),
        support.readiness(passing_checks=("other",)),
    ],
)
def test_transient_probe_waits_until_the_named_checks_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ready: object
) -> None:
    _anchor(monkeypatch)
    ctx = recipes_mod.RecipeContext(**{**_transient(tmp_path).__dict__, "readiness": ready})
    assert _RECIPES["transient_failed_check"].probe(ctx) == recipes_mod.not_yet(
        "the rerun checks are not green yet"
    )


def test_transient_probe_and_plan_answer_the_retry_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch)
    ctx = _transient(tmp_path)
    assert _RECIPES["transient_failed_check"].probe(ctx).state == "ready"
    plan = _RECIPES["transient_failed_check"].plan(ctx)
    assert (plan.kind, plan.team, plan.option) == (
        "answer",
        "pr_merge",
        recipes_mod.TRANSIENT_RETRY_OPTION,
    )


def test_transient_probe_without_a_blocking_handover_skips_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchor(monkeypatch, checks=("Backend tests + coverage",))
    ctx = recipes_mod.RecipeContext(**{**_transient(tmp_path).__dict__, "blocking": None})
    assert _RECIPES["transient_failed_check"].probe(ctx).state == "ready"


def test_human_feedback_probe_declines_without_a_pull_request(ws: Workspace) -> None:
    ctx = support.context(
        ws, latest=support.handover(ws), pr_number=None, ready=support.readiness()
    )
    assert _RECIPES["green_not_merging"].probe(ctx) == recipes_mod.declined("no pull request")


def test_reviews_or_probe_declines_without_a_pull_request(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws), pr_number=None)
    assert recipes_mod._reviews_or_probe(ctx) == recipes_mod.declined(  # noqa: SLF001
        "no pull request"
    )


def _answered(
    ws: Workspace,
    *,
    answer: str = "option one",
    resolvers: dict[str, str] | None = None,
    effects: dict[str, str] | None = None,
    question_class: str | None = "merge_block_escalation",
    handover_type: str = "revisit_amend",
) -> recipes_mod.RecipeContext:
    q = support.question(
        question_class=question_class, answer=answer, resolvers=resolvers, effects=effects
    )
    return support.context(
        ws, latest=support.handover(ws, questions=[q], handover_type=handover_type)
    )


def test_answered_question_requires_a_revisit_amend_with_an_answer(ws: Workspace) -> None:
    assert recipes_mod.answered_question(None) is None
    assert (
        recipes_mod.answered_question(support.handover(ws, questions=[support.question()])) is None
    )
    amended = support.handover(
        ws, questions=[support.question(answer="x")], handover_type="revisit_amend"
    )
    found = recipes_mod.answered_question(amended)
    assert found is not None
    assert found.answer == "x"
    team_only = support.handover(
        ws,
        questions=[support.question(answer="x", resolver_role="verification")],
        handover_type="revisit_amend",
    )
    assert recipes_mod.answered_question(team_only) is None


def test_answered_not_dispatched_matches_only_resumable_parks(
    ws: Workspace, tmp_path: Path
) -> None:
    recipe = _RECIPES["answered_not_dispatched"]
    assert recipe.match(_answered(ws)) is True
    assert recipe.match(_answered(ws, handover_type="forward")) is False
    reparked = support.parked_ws(tmp_path, title="reparked", park_reason=ParkReason.MERGE_BLOCK)
    assert recipe.match(_answered(reparked)) is False


def test_answered_not_dispatched_probe_declines_without_an_answer(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws))
    assert _RECIPES["answered_not_dispatched"].probe(ctx) == recipes_mod.declined(
        "no recorded answer to resume"
    )


def test_answered_not_dispatched_probe_declines_a_walled_answer(ws: Workspace) -> None:
    ctx = _answered(ws, effects={"option one": "pending_external"})
    assert _RECIPES["answered_not_dispatched"].probe(ctx).state == "declined"


def test_answered_not_dispatched_probe_declines_without_a_resolver(ws: Workspace) -> None:
    ctx = _answered(ws, question_class=None)
    assert _RECIPES["answered_not_dispatched"].probe(ctx) == recipes_mod.declined(
        "the recorded answer has no resolver"
    )


def test_answered_not_dispatched_probe_declines_a_team_outside_the_allowlist(
    ws: Workspace,
) -> None:
    ctx = _answered(ws, resolvers={"option one": "research"})
    assert _RECIPES["answered_not_dispatched"].probe(ctx) == recipes_mod.declined(
        "research is outside the auto-continue allowlist"
    )


def test_answered_not_dispatched_probe_declines_a_rejected_team(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: False)
    probe = _RECIPES["answered_not_dispatched"].probe(_answered(ws))
    assert probe.state == "declined"
    assert recipes_mod.FLEET_GRANT_MISSING in probe.reason


def test_answered_not_dispatched_probe_and_plan_resume_the_resolver(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    ctx = _answered(ws)
    assert _RECIPES["answered_not_dispatched"].probe(ctx) == recipes_mod.ready(
        "resuming the recorded answer's dispatch of pr_merge"
    )
    plan = _RECIPES["answered_not_dispatched"].plan(ctx)
    assert (plan.kind, plan.team) == ("dispatch", "pr_merge")


def test_answered_not_dispatched_plan_defaults_without_an_answer(ws: Workspace) -> None:
    ctx = support.context(ws, latest=support.handover(ws))
    assert _RECIPES["answered_not_dispatched"].plan(ctx).team == "verification"

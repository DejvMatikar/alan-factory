from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from typing import Literal, cast

from alan import dispatch as dispatch_mod
from alan.engine_config import EngineConfig, ForgeTarget
from alan.errors import AppError
from alan.handover import Handover, OpenQuestion
from alan.loop_guard import SpiralKind
from alan.park import WAIVER_WITHHELD_BLOCKER, Blocker, ParkReason
from alan.proposal_items import propose_team
from alan.repo_status import MainCiField
from alan.teams import AUTO_CONTINUE_TEAMS, MANUALLY_DISPATCHABLE_TEAMS
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _fix_escalation as fix_escalation_mod
from alan.teams import _pr_feedback as pr_feedback_mod
from alan.teams import _pr_flake_ledger as pr_flake_ledger_mod
from alan.teams import _pr_handover as pr_handover_mod
from alan.teams import _pr_merge_escalation as pr_merge_escalation_mod
from alan.teams import _pr_post as pr_post_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.teams import _pr_state_paths as pr_state_paths_mod
from alan.teams._pr_branch import VerifierIdentity, is_verifier, verifier_identity
from alan.teams._pr_json import _JsonObject  # pyright: ignore[reportPrivateUsage]
from alan.workspace import Status, Workspace

ProbeState = Literal["ready", "not_yet", "declined"]
PlanKind = Literal["answer", "dispatch"]

NO_COMMITS_PREFIX = "implementation_fix produced no commits"
EXTERNAL_GATE_SPIRAL_KIND: SpiralKind = "external_gate_blocked"
EXTERNAL_GATE_SPIRAL_PREFIX = f"spiral:{EXTERNAL_GATE_SPIRAL_KIND}: "
ESCALATION_BLOCKER = "escalation"
FLEET_GRANT_MISSING = "fleet_grant_missing"
TRANSIENT_FAILED_QUESTION = "pr-merge-failed"
TRANSIENT_RETRY_OPTION = pr_merge_escalation_mod.FAILED_OPTIONS[1]
WATCH_WINDOW_S = 5400.0
VERIFIER_REJECTIONS_AT_HEAD_CEILING = 2

_STALE_REVIEW_BLOCKERS: frozenset[str] = frozenset({"pr-merge-timeout", "pr-merge-unresolved"})
_RESUMABLE_PARKS: frozenset[ParkReason] = frozenset({ParkReason.QUESTION, ParkReason.ESCALATED})
_STALE_MERGE_STATES: frozenset[str] = frozenset({"CLEAN", "HAS_HOOKS", ""})
_GREEN_MERGE_STATES: frozenset[str] = frozenset({"CLEAN", "HAS_HOOKS"})
_CHANGES_REQUESTED = "CHANGES_REQUESTED"
_FORGE_UNAVAILABLE = "forge unavailable"
_BASE_CLAIM_MARKERS: tuple[str, ...] = (
    "on base", "on main", "origin/main", "pre-existing", "independent of", "main branch",
    "upstream", "regardless of this",
)


@dataclass(frozen=True)
class Probe:
    state: ProbeState
    reason: str


@dataclass(frozen=True)
class Deps:
    review_stream: Callable[[int, ForgeTarget], str | None]
    feedback: Callable[[int, ForgeTarget], pr_feedback_mod.FeedbackSnapshot]
    main_ci: Callable[[EngineConfig], MainCiField | None]
    dismiss_stale_reviews: Callable[[int, str, ForgeTarget], bool]


@dataclass(frozen=True)
class RecipeContext:
    ws: Workspace
    latest: Handover | None
    blocking: Handover | None
    question: OpenQuestion | None
    blocker: Blocker | None
    pr_number: int | None
    forge_target: ForgeTarget
    engine_cfg: EngineConfig
    readiness: pr_ready_mod.ReadinessResult | None
    now: float
    deps: Deps
    attempts_fp: int = 0
    park_head: str | None = None


@dataclass(frozen=True)
class Plan:
    kind: PlanKind
    team: str
    instructions: str
    option: str | None = None
    forge_write: Callable[[], bool] | None = None
    bar_lowering: bool = False


@dataclass(frozen=True)
class Recipe:
    name: str
    title: str
    match: Callable[[RecipeContext], bool]
    probe: Callable[[RecipeContext], Probe]
    plan: Callable[[RecipeContext], Plan]


def ready(reason: str) -> Probe:
    return Probe("ready", reason)


def not_yet(reason: str) -> Probe:
    return Probe("not_yet", reason)


def declined(reason: str) -> Probe:
    return Probe("declined", reason)


def blocking_question(handover: Handover | None) -> OpenQuestion | None:
    if handover is None:
        return None
    for question in handover.open_questions:
        if question.blocking and question.answer is None and question.resolver_role == "human":
            return question
    return None


def answered_question(handover: Handover | None) -> OpenQuestion | None:
    if handover is None or handover.handover_type != "revisit_amend":
        return None
    for question in handover.open_questions:
        if question.blocking and question.answer is not None and question.resolver_role == "human":
            return question
    return None


def single_blocker(ws: Workspace) -> Blocker | None:
    blockers = ws.meta.blockers
    return blockers[0] if len(blockers) == 1 else None


def _from_team(ctx: RecipeContext) -> str | None:
    return ctx.latest.from_team if ctx.latest is not None else None


def _usable_readiness(ctx: RecipeContext) -> pr_ready_mod.PrReadiness | Probe:
    readiness = ctx.readiness
    if not isinstance(readiness, pr_ready_mod.PrReadiness):
        return not_yet(_FORGE_UNAVAILABLE)
    if pr_ready_mod.results_absent(readiness):
        return not_yet("no check results at the head yet")
    if readiness.pending_checks:
        return not_yet(f"checks pending: {', '.join(readiness.pending_checks)}")
    if readiness.failing_checks:
        return declined(f"checks failing: {', '.join(readiness.failing_checks)}")
    if readiness.merge_state_status == "DIRTY":
        return declined("merge conflict; the reconcile actor owns DIRTY branches")
    return readiness


def _human_feedback_probe(ctx: RecipeContext) -> Probe | None:
    if ctx.pr_number is None:
        return declined("no pull request")
    snapshot = ctx.deps.feedback(ctx.pr_number, ctx.forge_target)
    if not snapshot.ok:
        return not_yet(f"feedback unavailable: {snapshot.failure_reason}")
    if pr_feedback_mod.has_unresolved_human(
        snapshot.thread_info, snapshot.blocking_comment_info
    ):
        return declined("an unresolved human review thread or comment blocks the merge")
    return None


def _review_login(review: _JsonObject) -> str | None:
    user = review.get("user")
    if not isinstance(user, dict):
        return None
    login = cast(_JsonObject, user).get("login")
    return login if isinstance(login, str) else None


def _review_state(review: _JsonObject) -> str:
    return str(review.get("state") or "").upper()


def other_author_requested_changes(
    reviews: tuple[_JsonObject, ...], verifier: VerifierIdentity | None
) -> bool:
    latest: dict[str, str] = {}
    for review in reviews:
        login = _review_login(review)
        if login is not None and not is_verifier(verifier, login):
            latest[login] = _review_state(review)
    return _CHANGES_REQUESTED in latest.values()


def verifier_review_position(
    reviews: tuple[_JsonObject, ...], verifier: VerifierIdentity | None, head_sha: str
) -> Literal["stale", "current", "none"]:
    position: Literal["stale", "current", "none"] = "none"
    for review in reviews:
        if not is_verifier(verifier, _review_login(review)):
            continue
        if _review_state(review) != _CHANGES_REQUESTED:
            continue
        if review.get("commit_id") == head_sha:
            return "current"
        position = "stale"
    return position


def _reviews_or_probe(ctx: RecipeContext) -> tuple[_JsonObject, ...] | Probe:
    if ctx.pr_number is None:
        return declined("no pull request")
    stdout = ctx.deps.review_stream(ctx.pr_number, ctx.forge_target)
    if stdout is None:
        return not_yet("review stream unavailable")
    reviews = pr_post_mod.parse_review_objects(stdout)
    if other_author_requested_changes(reviews, verifier_identity()):
        return declined("a human review requests changes")
    return reviews


def _stale_review_only(readiness: pr_ready_mod.PrReadiness) -> bool:
    if readiness.review_decision != _CHANGES_REQUESTED or readiness.failing_checks:
        return False
    if readiness.merge_state_status in _STALE_MERGE_STATES:
        return True
    return readiness.merge_state_status == "BLOCKED" and not readiness.pending_checks


def _waiver_withheld(ws: Workspace) -> bool:
    return any(b.name == WAIVER_WITHHELD_BLOCKER for b in ws.meta.blockers)


def _match_stale_review(ctx: RecipeContext) -> bool:
    if ctx.pr_number is None or _waiver_withheld(ctx.ws):
        return False
    if ctx.blocker is not None and ctx.blocker.name in _STALE_REVIEW_BLOCKERS:
        return True
    return (
        ctx.ws.meta.park_reason == ParkReason.MERGE_BLOCK
        and ctx.ws.meta.merge_block_class == "stale_review"
    )


def _stale_readiness(ctx: RecipeContext) -> pr_ready_mod.PrReadiness | Probe:
    readiness = ctx.readiness
    if not isinstance(readiness, pr_ready_mod.PrReadiness):
        return not_yet(_FORGE_UNAVAILABLE)
    if readiness.pending_checks:
        return not_yet(f"checks pending: {', '.join(readiness.pending_checks)}")
    if not _stale_review_only(readiness):
        return declined("the block is not a lone CHANGES_REQUESTED review")
    return readiness


def _probe_stale_review(ctx: RecipeContext) -> Probe:
    readiness = _stale_readiness(ctx)
    if isinstance(readiness, Probe):
        return readiness
    human = _human_feedback_probe(ctx)
    if human is not None:
        return human
    reviews = _reviews_or_probe(ctx)
    if isinstance(reviews, Probe):
        return reviews
    position = verifier_review_position(reviews, verifier_identity(), readiness.head_sha)
    if position == "stale":
        return ready("the verifier's CHANGES_REQUESTED review predates the current head")
    if position == "current":
        return declined("the verifier rejected the current head: a verdict, not staleness")
    return declined("no verifier review requests changes")


def _plan_stale_review(ctx: RecipeContext) -> Plan:
    pr_number = ctx.pr_number or 0
    task_id = ctx.ws.task_id
    forge_target = ctx.forge_target

    def _dismiss() -> bool:
        return ctx.deps.dismiss_stale_reviews(pr_number, task_id, forge_target)

    return Plan(
        kind="dispatch",
        team="pr_merge",
        instructions=(
            "Automated unblocker (stale_review): the verifier's CHANGES_REQUESTED review predated"
            " the current head and was dismissed. Re-dispatching pr_merge to re-evaluate the merge."
        ),
        forge_write=_dismiss,
    )


def _match_no_commits(ctx: RecipeContext) -> bool:
    if ctx.ws.meta.status != Status.ESCALATED or ctx.pr_number is None:
        return False
    if ctx.question is not None:
        return False
    return (
        ctx.blocker is not None
        and ctx.blocker.name == ESCALATION_BLOCKER
        and ctx.blocker.detail.startswith(NO_COMMITS_PREFIX)
    )


def _match_external_gate_spiral(ctx: RecipeContext) -> bool:
    if ctx.ws.meta.status != Status.ESCALATED or ctx.pr_number is None:
        return False
    if ctx.question is not None:
        return False
    return (
        ctx.blocker is not None
        and ctx.blocker.kind == "reason"
        and ctx.blocker.name == "reason"
        and ctx.blocker.detail.startswith(EXTERNAL_GATE_SPIRAL_PREFIX)
    )


def _probe_no_commits(ctx: RecipeContext) -> Probe:
    readiness = _usable_readiness(ctx)
    if isinstance(readiness, Probe):
        return readiness
    human = _human_feedback_probe(ctx)
    if human is not None:
        return human
    if readiness.review_decision == _CHANGES_REQUESTED:
        reviews = _reviews_or_probe(ctx)
        if isinstance(reviews, Probe):
            return reviews
    return ready("the fix pass found its findings already fixed at the head")


def _plan_no_commits(ctx: RecipeContext) -> Plan:
    team = "pr_merge" if ctx.attempts_fp >= 1 else "verification"
    return Plan(
        kind="dispatch",
        team=team,
        instructions=(
            f"Automated unblocker (no_commit_escalation): implementation_fix escalated with no"
            f" commits because its findings were already fixed at the head. Re-dispatching {team}"
            " to judge the branch as it stands."
        ),
    )


def _match_declared_blocker(ctx: RecipeContext) -> bool:
    return (
        ctx.pr_number is not None
        and ctx.question is not None
        and ctx.question.id.startswith("declared_blocker_")
    )


def _main_ci_probe(main_ci: MainCiField | None) -> Probe:
    if main_ci is None or main_ci.provenance.status != "fresh" or main_ci.red_count is None:
        return not_yet("main-branch CI status is not fresh")
    if main_ci.red_count > 0:
        return declined("the default branch is red; the declared blocker may hold")
    return ready("the default branch's CI is green, so the base-branch claim does not hold")


def _probe_declared_blocker(ctx: RecipeContext) -> Probe:
    text = ctx.question.question.lower() if ctx.question is not None else ""
    if not any(marker in text for marker in _BASE_CLAIM_MARKERS):
        return declined("the declared blocker is not a claim about the base branch's CI")
    return _main_ci_probe(ctx.deps.main_ci(ctx.engine_cfg))


def _plan_declared_blocker(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="answer",
        team="verification",
        option=declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        instructions=(
            "Automated unblocker (declared_blocker): the declared pre-existing failure does not"
            " reproduce on the default branch, whose CI is green. Verify the branch as it stands."
        ),
    )


def _park_commits(ctx: RecipeContext) -> frozenset[str]:
    artifacts = ctx.blocking.artifacts_produced if ctx.blocking is not None else []
    shas = (pr_handover_mod.commit_sha(artifact.path) for artifact in artifacts)
    return frozenset(sha for sha in shas if sha)


def _hand_resolved_head(commits: frozenset[str], head_sha: str) -> bool:
    return not any(head_sha.startswith(sha) for sha in commits)


def _park_epoch(blocking: Handover | None) -> float:
    if blocking is None:
        return float("inf")
    parked_at = blocking.timestamp_utc
    if parked_at.tzinfo is None:
        parked_at = parked_at.replace(tzinfo=UTC)
    return parked_at.timestamp()


def _unknown_head_after_park(
    marker: pr_state_paths_mod.SystemHeadMarker, blocking: Handover | None
) -> bool:
    return (
        marker.head_sha is None
        and marker.written_at is not None
        and marker.written_at >= _park_epoch(blocking)
    )


def _fleet_moved(ctx: RecipeContext, head_sha: str) -> bool:
    marker = pr_state_paths_mod.load_system_head(ctx.pr_number or 0, ctx.forge_target)
    if marker is None:
        return False
    return marker.head_sha == head_sha or _unknown_head_after_park(marker, ctx.blocking)


def _probe_park_without_commits(ctx: RecipeContext, head_sha: str) -> Probe:
    if ctx.park_head is None or ctx.park_head == head_sha:
        return declined("the park recorded no worker commits to compare the head against")
    if _fleet_moved(ctx, head_sha):
        return declined("the fleet's own rebase machinery moved the head")
    return ready("the head moved since the park to a commit no fleet actor marked")


def _probe_hand_resolved(ctx: RecipeContext) -> Probe:
    readiness = ctx.readiness
    if not isinstance(readiness, pr_ready_mod.PrReadiness) or not readiness.head_sha:
        return not_yet(_FORGE_UNAVAILABLE)
    commits = _park_commits(ctx)
    if not commits:
        return _probe_park_without_commits(ctx, readiness.head_sha)
    if not _hand_resolved_head(commits, readiness.head_sha):
        return declined("the head still matches a worker commit from the park")
    if _fleet_moved(ctx, readiness.head_sha):
        return declined("the fleet's own rebase machinery moved the head")
    return ready("the head moved to a commit no fleet actor produced since the park")


def _plan_hand_resolved(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="answer",
        team="implementation_fix",
        option=declared_blocker_mod.HAND_RESOLVE_OPTION,
        instructions=(
            "Automated unblocker (hand_resolved_declared_blocker): the pull request head moved"
            " to a commit no fleet actor produced since the park, so the declared blocker's"
            " human step looks done. Re-dispatching implementation_fix to resume the fix."
        ),
    )


def _green_blocker_readiness(ctx: RecipeContext) -> pr_ready_mod.PrReadiness | Probe:
    readiness = ctx.readiness
    if not isinstance(readiness, pr_ready_mod.PrReadiness):
        return not_yet(_FORGE_UNAVAILABLE)
    if pr_ready_mod.results_absent(readiness):
        return not_yet("no check results at the head yet")
    return readiness


def _single_line(text: str) -> bool:
    return sum(1 for line in text.splitlines() if line.strip()) <= 1


def _blocker_named_checks(
    readiness: pr_ready_mod.PrReadiness, question: OpenQuestion | None
) -> frozenset[str] | Probe:
    text = question.question if question is not None else ""
    if not _single_line(text):
        return declined("the declared blocker spans more than one line")
    universe = (*readiness.failing_checks, *readiness.pending_checks, *readiness.passing_checks)
    named = frozenset(name for name in universe if name in text)
    if not named:
        return declined("the declared blocker names no check on this pull request")
    return named


def _named_then_other_checks(
    readiness: pr_ready_mod.PrReadiness, named: frozenset[str]
) -> Probe | None:
    red = sorted(named.intersection(readiness.failing_checks))
    if red:
        return declined(f"a check this blocker names is red: {', '.join(red)}")
    pending = sorted(named.intersection(readiness.pending_checks))
    if pending:
        return not_yet(f"a check this blocker names is pending: {', '.join(pending)}")
    if readiness.failing_checks:
        others = ", ".join(readiness.failing_checks)
        return declined(f"another check on the pull request is red: {others}")
    if readiness.pending_checks:
        others = ", ".join(readiness.pending_checks)
        return not_yet(f"another check on the pull request is pending: {others}")
    return None


def _head_retry_probe(ctx: RecipeContext, head_sha: str) -> Probe:
    if not head_sha:
        return not_yet("the forge reported no head commit to bound the retry against")
    reviews = _reviews_or_probe(ctx)
    if isinstance(reviews, Probe):
        return reviews
    rejections = verifier_rejections_at_head(reviews, verifier_identity(), head_sha)
    if rejections >= VERIFIER_REJECTIONS_AT_HEAD_CEILING:
        return declined("the verifier already rejected this head twice; a third pass adds nothing")
    return ready("every check the declared blocker names is green at the current head")


def _probe_green_named_check(ctx: RecipeContext) -> Probe:
    readiness = _green_blocker_readiness(ctx)
    if isinstance(readiness, Probe):
        return readiness
    named = _blocker_named_checks(readiness, ctx.question)
    if isinstance(named, Probe):
        return named
    unsettled = _named_then_other_checks(readiness, named)
    if unsettled is not None:
        return unsettled
    if readiness.merge_state_status not in _GREEN_MERGE_STATES:
        return declined(f"merge state is {readiness.merge_state_status or 'unknown'}")
    return _head_retry_probe(ctx, readiness.head_sha)


def _plan_green_named_check(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="answer",
        team="verification",
        option=declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        instructions=(
            "Automated unblocker (green_named_check_blocker): every check the declared blocker"
            " names is green at the current head, nothing else is red or pending and the branch"
            " is mergeable. Verify the branch as it stands."
        ),
    )


def _match_non_actionable(ctx: RecipeContext) -> bool:
    return (
        ctx.pr_number is not None
        and ctx.question is not None
        and ctx.question.id.startswith(fix_escalation_mod.QUESTION_ID_PREFIX)
        and fix_escalation_mod.RESOLVE_BY_HAND in ctx.question.proposed_answer_space
    )


def verifier_rejections_at_head(
    reviews: tuple[_JsonObject, ...], verifier: VerifierIdentity | None, head_sha: str
) -> int:
    return sum(
        1 for review in reviews
        if is_verifier(verifier, _review_login(review))
        and _review_state(review) == _CHANGES_REQUESTED
        and review.get("commit_id") == head_sha
    )


def _probe_non_actionable(ctx: RecipeContext) -> Probe:
    readiness = _usable_readiness(ctx)
    if isinstance(readiness, Probe):
        return readiness
    if not readiness.head_sha:
        return not_yet("the forge reported no head commit to bound the retry against")
    human = _human_feedback_probe(ctx)
    if human is not None:
        return human
    reviews = _reviews_or_probe(ctx)
    if isinstance(reviews, Probe):
        return reviews
    rejections = verifier_rejections_at_head(reviews, verifier_identity(), readiness.head_sha)
    if rejections >= VERIFIER_REJECTIONS_AT_HEAD_CEILING:
        return declined("the verifier already rejected this head twice; a third pass adds nothing")
    return ready("every required check is green at the head the fix pass could not act on")


def _plan_non_actionable(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="answer",
        team="verification",
        option=fix_escalation_mod.RESOLVE_BY_HAND,
        instructions=(
            "Automated unblocker (non_actionable_green_head): implementation_fix found nothing"
            " in its checkout it could change, and every required check is now green at the"
            " head. Verify the branch as it stands, citing the green checks where a clause's"
            " evidence is a command only the pipeline runs."
        ),
    )


def _match_crash_park(ctx: RecipeContext) -> bool:
    return (
        ctx.ws.meta.status == Status.AWAITING_INPUT
        and ctx.ws.meta.park_reason == ParkReason.CRASH
    )


def crash_team(ctx: RecipeContext) -> str | None:
    proposed = propose_team(ctx.latest)
    if proposed in AUTO_CONTINUE_TEAMS:
        return proposed
    from_team = _from_team(ctx)
    if from_team in MANUALLY_DISPATCHABLE_TEAMS:
        return from_team
    if from_team == "brief_refine" and ctx.ws.meta.pending_first_hop in MANUALLY_DISPATCHABLE_TEAMS:
        return ctx.ws.meta.pending_first_hop
    team = ctx.ws.meta.team
    return team if team in MANUALLY_DISPATCHABLE_TEAMS else None


def _probe_crash_park(ctx: RecipeContext) -> Probe:
    team = crash_team(ctx)
    if team is None:
        return declined(
            "no dispatchable team resolves from the handover chain or the pending first hop"
        )
    if not dispatch_mod.team_is_dispatchable(ctx.ws, team):
        return declined(f"{FLEET_GRANT_MISSING}: {team} is not admitted at this posture")
    return ready(f"fresh dispatch of {team}")


def _plan_crash_park(ctx: RecipeContext) -> Plan:
    team = crash_team(ctx) or "verification"
    return Plan(
        kind="dispatch",
        team=team,
        instructions=(
            f"Automated unblocker (crash_park): a service restart interrupted the previous run."
            f" Re-dispatching {team} fresh; nothing from the interrupted invocation is resumed."
        ),
    )


def _match_green_not_merging(ctx: RecipeContext) -> bool:
    if ctx.pr_number is None or ctx.blocker is None or _from_team(ctx) == "deploy_verify":
        return False
    if ctx.blocker.name.removeprefix("pr-merge-") in pr_merge_escalation_mod.OWNERSHIP_KINDS:
        return False
    return ctx.blocker.name.startswith("pr-merge-")


def _probe_green_not_merging(ctx: RecipeContext) -> Probe:
    readiness = _usable_readiness(ctx)
    if isinstance(readiness, Probe):
        return readiness
    if readiness.review_decision == _CHANGES_REQUESTED:
        return declined("a review requests changes")
    if readiness.merge_state_status not in _GREEN_MERGE_STATES:
        return declined(f"merge state is {readiness.merge_state_status or 'unknown'}")
    return _human_feedback_probe(ctx) or ready("the pull request is mergeable")


def _plan_green_not_merging(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="dispatch",
        team="pr_merge",
        instructions=(
            "Automated unblocker (green_not_merging): every check is green, no review blocks and"
            " the branch is mergeable. Re-dispatching pr_merge to complete the merge."
        ),
    )


def _plan_external_gate_spiral(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="dispatch",
        team="pr_merge",
        instructions=(
            "Automated unblocker (external_gate_spiral): the spiral detector parked this pull"
            " request on an external gate, and every check is now green with no review blocking"
            " and the branch mergeable. Re-dispatching pr_merge to complete the merge."
        ),
    )


def _match_transient_failed_check(ctx: RecipeContext) -> bool:
    return (
        ctx.pr_number is not None
        and ctx.question is not None
        and ctx.question.id == TRANSIENT_FAILED_QUESTION
        and ctx.blocker is not None
        and ctx.blocker.name == TRANSIENT_FAILED_QUESTION
    )


def named_checks(question_text: str, anchor: pr_state_paths_mod.GateRerunState) -> frozenset[str]:
    if anchor.checks:
        return frozenset(anchor.checks)
    return frozenset(
        name for name in pr_flake_ledger_mod.SHARED_FLAKY_ALLOWLIST if name in question_text
    )


def _named_checks_green(readiness: pr_ready_mod.PrReadiness, names: frozenset[str]) -> bool:
    if pr_ready_mod.results_absent(readiness) or readiness.check_count <= 0:
        return False
    if readiness.failing_checks or readiness.pending_checks:
        return False
    return all(
        pr_ready_mod.resolve_named_check_outcome(name, readiness) == pr_ready_mod.CheckOutcome.PASS
        for name in names
    )


def _rerun_anchor(ctx: RecipeContext) -> pr_state_paths_mod.GateRerunState | Probe:
    blocking = ctx.blocking
    if blocking is not None and ctx.now - blocking.timestamp_utc.timestamp() > WATCH_WINDOW_S:
        return declined("the watch window after the park elapsed")
    anchor = pr_state_paths_mod.load_gate_rerun_state(ctx.pr_number or 0, ctx.forge_target)
    if anchor.head_sha is None or anchor.count < 1:
        return declined("no gate rerun is anchored at a head for this pull request")
    return anchor


def _anchored_readiness(
    ctx: RecipeContext, anchor: pr_state_paths_mod.GateRerunState
) -> pr_ready_mod.PrReadiness | Probe:
    readiness = ctx.readiness
    if not isinstance(readiness, pr_ready_mod.PrReadiness):
        return not_yet(_FORGE_UNAVAILABLE)
    if readiness.head_sha != anchor.head_sha:
        return declined("the head moved since the gate rerun")
    return readiness


def _probe_transient_failed_check(ctx: RecipeContext) -> Probe:
    anchor = _rerun_anchor(ctx)
    if isinstance(anchor, Probe):
        return anchor
    readiness = _anchored_readiness(ctx, anchor)
    if isinstance(readiness, Probe):
        return readiness
    names = named_checks(ctx.question.question if ctx.question is not None else "", anchor)
    if not names:
        return declined("no named check to watch")
    if not _named_checks_green(readiness, names):
        return not_yet("the rerun checks are not green yet")
    return ready("the failing check went green at the same head")


def _plan_transient_failed_check(_ctx: RecipeContext) -> Plan:
    return Plan(
        kind="answer",
        team="pr_merge",
        option=TRANSIENT_RETRY_OPTION,
        instructions=(
            "Automated unblocker (transient_failed_check): the previously failing check now"
            " reports green at the same head commit. Re-dispatching pr_merge to re-evaluate."
        ),
    )


def _match_answered(ctx: RecipeContext) -> bool:
    return ctx.ws.meta.park_reason in _RESUMABLE_PARKS and answered_question(ctx.latest) is not None


def _resume_team(ctx: RecipeContext) -> str | Probe:
    latest, question = ctx.latest, answered_question(ctx.latest)
    if latest is None or question is None:
        return declined("no recorded answer to resume")
    answer = question.answer or ""
    if question.option_effects.get(answer) == "pending_external":
        return declined("the recorded answer waits on an out-of-fleet step")
    try:
        return dispatch_mod.resolver_team_for_answer(question, latest.from_team, answer)
    except AppError:
        return declined("the recorded answer has no resolver")


def _probe_answered(ctx: RecipeContext) -> Probe:
    team = _resume_team(ctx)
    if isinstance(team, Probe):
        return team
    if team not in AUTO_CONTINUE_TEAMS:
        return declined(f"{team} is outside the auto-continue allowlist")
    if not dispatch_mod.team_is_dispatchable(ctx.ws, team):
        return declined(f"{FLEET_GRANT_MISSING}: {team} is not admitted at this posture")
    return ready(f"resuming the recorded answer's dispatch of {team}")


def _plan_answered(ctx: RecipeContext) -> Plan:
    team = _resume_team(ctx)
    resolved = team if isinstance(team, str) else "verification"
    return Plan(
        kind="dispatch",
        team=resolved,
        instructions=(
            f"Automated unblocker (answered_not_dispatched): the parked question was answered but"
            f" its dispatch never ran. Dispatching {resolved} for the recorded answer."
        ),
    )


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        "answered_not_dispatched", "an answered question was never dispatched",
        _match_answered, _probe_answered, _plan_answered,
    ),
    Recipe(
        "stale_review", "a stale verifier review blocked the merge",
        _match_stale_review, _probe_stale_review, _plan_stale_review,
    ),
    Recipe(
        "no_commit_escalation", "a correct no-commit fix pass escalated",
        _match_no_commits, _probe_no_commits, _plan_no_commits,
    ),
    Recipe(
        "declared_blocker", "a declared base-branch blocker does not hold",
        _match_declared_blocker, _probe_declared_blocker, _plan_declared_blocker,
    ),
    Recipe(
        "hand_resolved_declared_blocker", "a declared blocker's head moved past the park",
        _match_declared_blocker, _probe_hand_resolved, _plan_hand_resolved,
    ),
    Recipe(
        "green_named_check_blocker", "a declared blocker's named checks are green",
        _match_declared_blocker, _probe_green_named_check, _plan_green_named_check,
    ),
    Recipe(
        "non_actionable_green_head", "a fix pass with nothing to fix parked a green head",
        _match_non_actionable, _probe_non_actionable, _plan_non_actionable,
    ),
    Recipe(
        "crash_park", "a restart-interrupted run was parked",
        _match_crash_park, _probe_crash_park, _plan_crash_park,
    ),
    Recipe(
        "green_not_merging", "a mergeable pull request sat parked",
        _match_green_not_merging, _probe_green_not_merging, _plan_green_not_merging,
    ),
    Recipe(
        "external_gate_spiral", "an external-gate spiral park's gate cleared",
        _match_external_gate_spiral, _probe_green_not_merging, _plan_external_gate_spiral,
    ),
    Recipe(
        "transient_failed_check", "a failing check went green after the park",
        _match_transient_failed_check, _probe_transient_failed_check,
        _plan_transient_failed_check,
    ),
)


def match_recipes(ctx: RecipeContext) -> list[Recipe]:
    return [recipe for recipe in RECIPES if recipe.match(ctx)]


__all__ = [
    "ESCALATION_BLOCKER",
    "EXTERNAL_GATE_SPIRAL_KIND",
    "EXTERNAL_GATE_SPIRAL_PREFIX",
    "FLEET_GRANT_MISSING",
    "NO_COMMITS_PREFIX",
    "RECIPES",
    "TRANSIENT_FAILED_QUESTION",
    "TRANSIENT_RETRY_OPTION",
    "WATCH_WINDOW_S",
    "Deps",
    "Plan",
    "PlanKind",
    "Probe",
    "ProbeState",
    "Recipe",
    "RecipeContext",
    "answered_question",
    "blocking_question",
    "crash_team",
    "declined",
    "match_recipes",
    "named_checks",
    "not_yet",
    "other_author_requested_changes",
    "ready",
    "single_blocker",
    "verifier_review_position",
]

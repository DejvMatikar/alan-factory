from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from alan import dispatch as dispatch_mod
from alan import handover as handover_mod
from alan import lifecycle
from alan import merge_gate as merge_gate_mod
from alan import repo_status as repo_status_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import workspace as workspace_mod
from alan.dispatch import EnqueueWorkflowFn
from alan.engine_config import (
    EngineConfig,
    ForgeTarget,
    RepoIdentity,
    forge_target_for_workspace,
    live_hidden,
)
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion
from alan.incident import DESIGN_FAMILY_TEAMS, STUCK_WS_STATUSES, incident_fingerprint
from alan.park import Blocker
from alan.queue import JobStatus, Queue
from alan.teams import TEAMS
from alan.teams import _forge_errors as forge_errors_mod
from alan.teams import _pr_feedback as pr_feedback_mod
from alan.teams import _pr_forge as pr_forge_mod
from alan.teams import _pr_post as pr_post_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.teams import unblock_issue as issue_mod
from alan.unblock_ledger import REPROBE_CAP, UNBLOCK_WATCH_META_KEY, WatchEntry, load_watch
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex, WorkspaceRow

logger = logging.getLogger("alan.unblock")

DISABLE_ENV = "ALAN_UNBLOCK_DISABLE"
SWEEP_INTERVAL_S = 60.0
BASE_INTERVAL_S = 300.0
MAX_INTERVAL_S = 1200.0
OBSERVE_DELAY_S = 120.0
REPROBE_INTERVAL_S = MAX_INTERVAL_S
MAX_FORGE_CANDIDATES_PER_TICK = 3
ISSUE_DRAIN_PER_TICK = 5
UNBLOCK_SKIP_REASON = (
    "unblock sweep: recipe dispatch; the merge gate runs on the dispatched invocation itself"
)
NEEDS_HUMAN_SHAPE = "needs_human"
NEEDS_HUMAN_TITLE = "the unblocker could not clear a stall"
HOLDER_HUMAN = "human_operator"
HOLDER_NOBODY = "nobody"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_INTERNAL_TEAMS: frozenset[str] = frozenset({"triage", "dependency_sweep"})
_FORGE_UNAVAILABLE = (forge_errors_mod.ForgeRateLimited, forge_errors_mod.ForgeTransientError)


class LockFor(Protocol):
    def __call__(self, workspace_id: str) -> AbstractContextManager[object]: ...  # pragma: no cover


class ReadReadiness(Protocol):
    def __call__(  # pragma: no cover
        self, pr_number: int, task_id: str, *, forge_target: ForgeTarget
    ) -> pr_ready_mod.ReadinessResult: ...


class IssueClient(Protocol):
    def ensure_shape_issue(  # pragma: no cover
        self, title: str, body: str, *, forge_target: ForgeTarget
    ) -> int | None: ...

    def comment(  # pragma: no cover
        self, number: int, body: str, *, forge_target: ForgeTarget
    ) -> bool: ...


@dataclass(frozen=True)
class Candidate:
    row: WorkspaceRow
    ws: Workspace
    latest: Handover | None
    blocking: Handover | None
    question: OpenQuestion | None
    blocker: Blocker | None
    pr_number: int | None
    forge_target: ForgeTarget
    engine_cfg: EngineConfig
    identity: str
    park_class: str = ""
    head: str | None = None


@dataclass(frozen=True)
class SweepContext:
    queue: Queue
    pubsub: PubSub
    workspaces_root: Path
    now: float
    lock_for: LockFor
    enqueue_workflow: EnqueueWorkflowFn
    read_readiness: ReadReadiness
    deps: recipes_mod.Deps


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in _TRUTHY


def _default_read_readiness(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget
) -> pr_ready_mod.ReadinessResult:
    return pr_ready_mod.readiness_or_unavailable(pr_number, task_id, forge_target=forge_target)


def default_deps(
    queue: Queue, workspace_index: WorkspaceIndex, pubsub: PubSub
) -> recipes_mod.Deps:
    def _review_stream(pr_number: int, forge_target: ForgeTarget) -> str | None:
        return pr_post_mod.fetch_review_stream(pr_number, forge_target=forge_target)

    def _feedback(pr_number: int, forge_target: ForgeTarget) -> pr_feedback_mod.FeedbackSnapshot:
        return pr_feedback_mod.read_feedback(pr_number, forge_target=forge_target)

    def _main_ci(cfg: EngineConfig) -> repo_status_mod.MainCiField | None:
        status = repo_status_mod.get_repo_status(
            cfg.repo, queue=queue, workspace_index=workspace_index, pubsub=pubsub
        )
        return None if status is None else status.main_ci

    def _dismiss(pr_number: int, task_id: str, forge_target: ForgeTarget) -> bool:
        return pr_post_mod.dismiss_stale_verifier_reviews(
            pr_number, task_id, forge_target=forge_target
        )

    return recipes_mod.Deps(
        review_stream=_review_stream, feedback=_feedback, main_ci=_main_ci,
        dismiss_stale_reviews=_dismiss,
    )


def _save_watch(queue: Queue, watch: dict[str, WatchEntry]) -> None:
    payload = {task_id: entry.__dict__ for task_id, entry in watch.items()}
    queue.set_meta(UNBLOCK_WATCH_META_KEY, json.dumps(payload, sort_keys=True))


def stuck_rows(rows: Iterable[WorkspaceRow]) -> list[WorkspaceRow]:
    return [row for row in rows if row.status in STUCK_WS_STATUSES]


def _identity(ws: Workspace, latest: Handover | None) -> str:
    handover_id = latest.handover_id if latest is not None else "none"
    return f"{handover_id}@{ws.meta.updated_at.isoformat()}"


def _awaiting_harvest(ws: Workspace, latest: Handover | None, team: str | None) -> bool:
    if ws.meta.status != Status.AWAITING_INPUT or team not in DESIGN_FAMILY_TEAMS:
        return False
    return latest is None or not latest.to_team_suggestions


def _delivered(latest: Handover | None) -> bool:
    return (
        latest is not None
        and latest.from_team == "deploy_verify"
        and recipes_mod.blocking_question(latest) is None
    )


def _exempt_by_team(ws: Workspace, latest: Handover | None) -> str | None:
    team = ws.meta.team or (latest.from_team if latest is not None else None)
    if team in _INTERNAL_TEAMS:
        return "internal team"
    if _awaiting_harvest(ws, latest, team):
        return "design-family workspace awaiting harvest"
    if _delivered(latest):
        return "delivered"
    return None


def exempt_reason(ws: Workspace, latest: Handover | None, engine_cfg: EngineConfig) -> str | None:
    by_team = _exempt_by_team(ws, latest)
    if by_team is not None:
        return by_team
    if lifecycle.held_by_unarchive(ws.meta.status, ws.meta.park_reason):
        return "restored from archive"
    if live_hidden(ws.meta.engine_key):
        return "engine hidden"
    if not engine_cfg.unblock_enabled:
        return "unblock disabled for this engine"
    return None


def _park_class_for(
    question: OpenQuestion | None, latest: Handover | None, blocker: Blocker | None
) -> str:
    return ledger.park_class(
        question.id if question is not None else None,
        latest.from_invocation_id if latest is not None else "",
        blocker.name if blocker is not None else None,
    )


def candidate_for(row: WorkspaceRow, *, workspaces_root: Path) -> Candidate | None:
    try:
        ws = workspace_mod.read(row.task_id, root=workspaces_root)
    except workspace_mod.WorkspaceError:
        return None
    if ws.meta.status not in STUCK_WS_STATUSES:
        return None
    handovers = handover_mod.list_handovers(ws)
    latest = handovers[-1] if handovers else None
    engine_cfg = pr_forge_mod.governing_config_for_ws(ws)
    if exempt_reason(ws, latest, engine_cfg) is not None:
        return None
    question = recipes_mod.blocking_question(latest)
    blocker = recipes_mod.single_blocker(ws)
    return Candidate(
        row=row, ws=ws, latest=latest,
        blocking=latest if question is not None else None,
        question=question, blocker=blocker,
        pr_number=latest.pr_number if latest is not None else None,
        forge_target=pr_forge_mod.forge_target_for_ws(ws), engine_cfg=engine_cfg,
        identity=_identity(ws, latest), park_class=_park_class_for(question, latest, blocker),
    )


def _next_interval(attempts: int) -> float:
    return min(BASE_INTERVAL_S * 2.0**attempts, MAX_INTERVAL_S)


def _has_running_job(queue: Queue, task_id: str) -> bool:
    return bool(queue.list_jobs_for_workspace(task_id, status=JobStatus.RUNNING))


def _fingerprint_key(cand: Candidate) -> str:
    if cand.question is not None:
        return cand.question.id
    if cand.blocker is not None:
        return cand.blocker.name
    return ""


def _fingerprint(recipe: recipes_mod.Recipe, cand: Candidate) -> str:
    return incident_fingerprint(
        f"unblock:{recipe.name}", cand.row.resolved_scope_ref(), _fingerprint_key(cand)
    )


def _record(
    sc: SweepContext, cand: Candidate, *, shape: str, fingerprint: str, kind: str,
    action: str | None = None, outcome: str | None = None, detail: dict[str, Any] | None = None,
    bar_lowering: bool = False,
) -> int:
    return ledger.record(
        sc.queue,
        ledger.Entry(
            workspace_key=cand.ws.task_id, scope_ref=cand.row.resolved_scope_ref(), shape=shape,
            fingerprint=fingerprint, kind=kind, engine_key=cand.ws.meta.engine_key, action=action,
            outcome=outcome, head_sha=cand.head, handover_id=cand.identity,
            bar_lowering=bar_lowering, detail=detail, ts=sc.now,
        ),
    )


def _case_body(cand: Candidate, *, shape: str, lines: list[str]) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
    pr = f"PR #{cand.pr_number}" if cand.pr_number is not None else "no PR"
    park = f"{cand.ws.meta.status.value}/{cand.ws.meta.park_reason or '-'}"
    blocker = (
        f"`{cand.blocker.name}`: {cand.blocker.detail[:300]}" if cand.blocker is not None else "-"
    )
    head = [
        f"### `{cand.ws.task_id}` · {pr} · {cand.forge_target.repo.slug} · {stamp}",
        f"- shape: `{shape}`",
        f"- park: {park} · blocker {blocker}",
    ]
    return "\n".join(head + [f"- {line}" for line in lines])


def _shape_issue_body(title: str, shape: str) -> str:
    return (
        f"alan's recipe unblocker recognised the park shape `{shape}` ({title}) and acted on it"
        " autonomously. Every occurrence is appended below as a comment with the workspace, the"
        " pull request, what was probed, what was done and the outcome. The shape itself is a"
        " fleet defect until the host stops producing it; close this issue when that fix lands."
    )


def _file_intent(
    sc: SweepContext, cand: Candidate, *, shape: str, title: str, fingerprint: str,
    lines: list[str],
) -> None:
    slug = cand.forge_target.repo.slug
    _record(
        sc, cand, shape=shape, fingerprint=fingerprint, kind=ledger.KIND_ISSUE_INTENT,
        detail={
            "engine_key": cand.ws.meta.engine_key, "slug": slug,
            "owner": cand.forge_target.repo.owner, "repo": cand.forge_target.repo.repo,
            "credential_env_name": cand.forge_target.credential_env_name,
            "title": issue_mod.shape_issue_title(title, slug),
            "issue_body": _shape_issue_body(title, shape),
            "comment": _case_body(cand, shape=shape, lines=lines),
        },
    )


def _park_head(cand: Candidate, entry: WatchEntry | None) -> str | None:
    return (entry.park_head if entry is not None else None) or cand.head


def _next_entry(
    cand: Candidate, entry: WatchEntry | None, *, next_at: float, attempts: int,
    acted: bool = False, retried: bool = False, terminal: bool = False,
) -> WatchEntry:
    return WatchEntry(
        next_at=next_at, attempts=attempts, identity=cand.identity, acted=acted, retried=retried,
        terminal=terminal, park_head=_park_head(cand, entry),
    )


def _extend_terminal(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry
) -> None:
    watch[cand.ws.task_id] = _next_entry(
        cand, entry, next_at=sc.now + REPROBE_INTERVAL_S, attempts=entry.attempts + 1,
        terminal=True,
    )


def _park_fingerprint(cand: Candidate) -> str:
    return incident_fingerprint(
        f"unblock:{NEEDS_HUMAN_SHAPE}", cand.ws.task_id,
        ledger.park_key(cand.park_class, cand.head),
    )


def _report(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry | None,
    *, reason: str, authority_holder: str,
) -> None:
    if entry is not None and entry.terminal:
        _extend_terminal(sc, cand, watch, entry)
        return
    fingerprint = _park_fingerprint(cand)
    reason = ledger.repeat_reason(
        reason, ledger.report_history(sc.queue, cand.ws.task_id, fingerprint), cand.head
    )
    _record(
        sc, cand, shape=NEEDS_HUMAN_SHAPE, fingerprint=fingerprint, kind=ledger.KIND_REPORT,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        detail={"reason": reason, "authority_holder": authority_holder},
    )
    logger.info(
        "unblock sweep: %s reported needs_human (%s) authority=%s",
        cand.ws.task_id, reason, authority_holder,
    )
    _file_intent(
        sc, cand, shape=NEEDS_HUMAN_SHAPE, title=NEEDS_HUMAN_TITLE, fingerprint=fingerprint,
        lines=[
            f"probed: {reason}", f"authority holder: {authority_holder}",
            "outcome: needs_human",
        ],
    )
    attempts = entry.attempts if entry is not None else 0
    watch[cand.ws.task_id] = _next_entry(
        cand, entry, next_at=sc.now + REPROBE_INTERVAL_S, attempts=attempts, terminal=True
    )


def _wait(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry | None,
    *, shape: str, reasons: list[str],
) -> None:
    if entry is not None and entry.terminal:
        _extend_terminal(sc, cand, watch, entry)
        return
    attempts = entry.attempts if entry is not None else 0
    if entry is None:
        _record(
            sc, cand, shape=shape, fingerprint="", kind=ledger.KIND_WAIT,
            outcome=ledger.OUTCOME_NOT_YET, detail={"reason": "; ".join(reasons)},
        )
        logger.info(
            "unblock sweep: %s waiting on %s (%s)", cand.ws.task_id, shape, "; ".join(reasons)
        )
    watch[cand.ws.task_id] = _next_entry(
        cand, entry, next_at=sc.now + _next_interval(attempts), attempts=attempts + 1,
        retried=entry.retried if entry is not None else False,
    )


def _dispatch_task_id(cand: Candidate) -> str | None:
    try:
        return dispatch_mod.owning_task_id(cand.ws, cand.ws.task_id, blocking=cand.blocking)
    except AppError:
        return None


def _restore_park(fresh: Workspace, cand: Candidate) -> None:
    workspace_mod.transition(
        fresh, cand.ws.meta.status, park_reason=cand.ws.meta.park_reason,
        blockers=list(cand.ws.meta.blockers),
    )


def _still_parked(sc: SweepContext, cand: Candidate, fresh: Workspace) -> str | None:
    latest = handover_mod.list_handovers(fresh)
    if _identity(fresh, latest[-1] if latest else None) != cand.identity:
        return "skipped_moved"
    if _has_running_job(sc.queue, cand.ws.task_id):
        return "skipped_running"
    return None


def _record_plan_answer(fresh: Workspace, cand: Candidate, plan: recipes_mod.Plan) -> None:
    if plan.kind != "answer" or cand.blocking is None or cand.question is None:
        return
    option = plan.option or ""
    handover_mod.record_answer(
        fresh, cand.blocking, cand.question.id, option, from_team=cand.blocking.from_team,
        effect_override=cand.question.option_effects.get(option),
    )


def _dispatch_locked(
    sc: SweepContext, cand: Candidate, plan: recipes_mod.Plan, task_id: str
) -> str:
    fresh = workspace_mod.read(cand.ws.task_id, root=sc.workspaces_root)
    skipped = _still_parked(sc, cand, fresh)
    if skipped is not None:
        return skipped
    _record_plan_answer(fresh, cand, plan)
    try:
        dispatch_mod.dispatch_team_core(
            workspace=fresh, task_id=task_id, team=TEAMS[plan.team], queue=sc.queue,
            pubsub=sc.pubsub, enqueue_workflow=sc.enqueue_workflow,
            merge_gate=merge_gate_mod.MergeGateDecision.skipped(UNBLOCK_SKIP_REASON),
            instructions=plan.instructions, instructions_origin="generated",
        )
    except Exception:
        _restore_park(fresh, cand)
        raise
    return ledger.OUTCOME_ACTED


def _resolver_rejection(
    question: OpenQuestion, blocking: Handover, option: str, team: str
) -> str | None:
    if question.option_effects.get(option) == "pending_external":
        return "the recipe's answer waits on an out-of-fleet step"
    try:
        resolver = dispatch_mod.resolver_team_for_answer(question, blocking.from_team, option)
    except AppError:
        return "the parked question has no resolver for the recipe's answer"
    if resolver != team:
        return f"the answer resolves to {resolver}, not {team}"
    return None


def _answer_rejection(cand: Candidate, plan: recipes_mod.Plan) -> str | None:
    if plan.kind != "answer":
        return None
    question, blocking, option = cand.question, cand.blocking, plan.option or ""
    if question is None or blocking is None or option not in question.proposed_answer_space:
        return "the recipe's answer is not one of the parked question's options"
    return _resolver_rejection(question, blocking, option, plan.team)


def _plan_rejection(cand: Candidate, plan: recipes_mod.Plan) -> tuple[str, str] | None:
    reason = _answer_rejection(cand, plan)
    if reason is not None:
        return reason, HOLDER_HUMAN
    if not dispatch_mod.team_is_dispatchable(cand.ws, plan.team):
        return (
            f"{recipes_mod.FLEET_GRANT_MISSING}: {plan.team} is not admitted at this posture",
            recipes_mod.FLEET_GRANT_MISSING,
        )
    return None


def _perform(sc: SweepContext, cand: Candidate, plan: recipes_mod.Plan, task_id: str) -> str:
    if plan.forge_write is not None and not plan.forge_write():
        return "forge_write_failed"
    # The forge write and the probe ran outside the workspace lock; only the re-read,
    # the answer record and the dispatch run inside it.
    with sc.lock_for(cand.ws.task_id):
        return _dispatch_locked(sc, cand, plan, task_id)


def _ledger_attempt(
    sc: SweepContext, cand: Candidate, recipe: recipes_mod.Recipe, plan: recipes_mod.Plan,
    fingerprint: str, probe_reason: str, *, outcome: str,
) -> None:
    action = f"{plan.kind}:{plan.team}" + (f" · {plan.option[:60]}" if plan.option else "")
    _record(
        sc, cand, shape=recipe.name, fingerprint=fingerprint, kind=ledger.KIND_ATTEMPT,
        action=action, outcome=outcome, bar_lowering=plan.bar_lowering,
        detail={"reason": probe_reason},
    )
    logger.info(
        "unblock sweep: acted on %s (%s) action=%s outcome=%s",
        cand.ws.task_id, recipe.name, action, outcome,
    )
    _file_intent(
        sc, cand, shape=recipe.name, title=recipe.title, fingerprint=fingerprint,
        lines=[f"probed: {probe_reason}", f"action: {action}", f"outcome: {outcome}"],
    )


def _act(
    sc: SweepContext, cand: Candidate, recipe: recipes_mod.Recipe, plan: recipes_mod.Plan,
    fingerprint: str, watch: dict[str, WatchEntry], entry: WatchEntry | None, probe_reason: str,
) -> bool:
    task_id = _dispatch_task_id(cand)
    if task_id is None:
        _report(
            sc, cand, watch, entry, reason="decomposed workspace without a unique owning subtask",
            authority_holder=HOLDER_HUMAN,
        )
        return False
    rejection = _plan_rejection(cand, plan)
    if rejection is not None:
        _report(sc, cand, watch, entry, reason=rejection[0], authority_holder=rejection[1])
        return False
    attempts = entry.attempts if entry is not None else 0
    watch[cand.ws.task_id] = _next_entry(
        cand, entry, next_at=sc.now + OBSERVE_DELAY_S, attempts=attempts + 1, acted=True,
        retried=entry.retried if entry is not None else False,
    )
    _save_watch(sc.queue, watch)
    try:
        outcome = _perform(sc, cand, plan, task_id)
    except Exception as exc:
        _ledger_attempt(
            sc, cand, recipe, plan, fingerprint, probe_reason,
            outcome=f"failed: {type(exc).__name__}",
        )
        raise
    _ledger_attempt(sc, cand, recipe, plan, fingerprint, probe_reason, outcome=outcome)
    return outcome == ledger.OUTCOME_ACTED


def _probe_recipe(
    sc: SweepContext, cand: Candidate, recipe: recipes_mod.Recipe, ctx: recipes_mod.RecipeContext,
) -> tuple[recipes_mod.Probe, str, int]:
    fingerprint = _fingerprint(recipe, cand)
    attempts_fp = ledger.attempts_for_fingerprint(sc.queue, cand.ws.task_id, fingerprint)
    if attempts_fp >= ledger.ATTEMPT_CEILING_PER_FINGERPRINT:
        probe = recipes_mod.declined(f"{recipe.name}: per-fingerprint ceiling reached")
        return probe, fingerprint, attempts_fp
    try:
        return recipe.probe(replace(ctx, attempts_fp=attempts_fp)), fingerprint, attempts_fp
    except _FORGE_UNAVAILABLE as exc:
        probe = recipes_mod.not_yet(f"{recipe.name}: forge unavailable ({exc})")
        return probe, fingerprint, attempts_fp


def _bucket(
    probe: recipes_mod.Probe, recipe: recipes_mod.Recipe, waiting: list[str], declined: list[str]
) -> None:
    bucket = waiting if probe.state == "not_yet" else declined
    bucket.append(f"{recipe.name}: {probe.reason}")


def _try_recipes(
    sc: SweepContext, cand: Candidate, ctx: recipes_mod.RecipeContext,
    watch: dict[str, WatchEntry], entry: WatchEntry | None,
) -> bool:
    waiting: list[str] = []
    declined: list[str] = []
    shape = "pending"
    for recipe in recipes_mod.match_recipes(ctx):
        shape = recipe.name if shape == "pending" else shape
        probe, fingerprint, attempts_fp = _probe_recipe(sc, cand, recipe, ctx)
        if probe.state == "ready":
            plan = recipe.plan(replace(ctx, attempts_fp=attempts_fp))
            return _act(sc, cand, recipe, plan, fingerprint, watch, entry, probe.reason)
        _bucket(probe, recipe, waiting, declined)
    if waiting:
        _wait(sc, cand, watch, entry, shape=shape, reasons=waiting)
    else:
        _report_declined(sc, cand, watch, entry, declined)
    return False


def _report_declined(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry | None,
    declined: list[str],
) -> None:
    reason = "; ".join(declined) if declined else "no recipe recognises this park"
    holder = (
        recipes_mod.FLEET_GRANT_MISSING if recipes_mod.FLEET_GRANT_MISSING in reason
        else HOLDER_HUMAN
    )
    _report(sc, cand, watch, entry, reason=reason, authority_holder=holder)


def _live_entry(
    watch: dict[str, WatchEntry], cand: Candidate, now: float
) -> tuple[WatchEntry | None, bool]:
    entry = watch.get(cand.ws.task_id)
    if entry is not None and entry.identity != cand.identity:
        entry = None
    if entry is None:
        return None, True
    return entry, now >= entry.next_at


def _observed_noop_twice(entry: WatchEntry | None) -> bool:
    return entry is not None and entry.acted and entry.retried


def _retry_after_observe(entry: WatchEntry | None) -> WatchEntry | None:
    if entry is None or not entry.acted:
        return entry
    return WatchEntry(
        next_at=entry.next_at, attempts=entry.attempts, identity=entry.identity,
        acted=False, retried=True, terminal=entry.terminal, park_head=entry.park_head,
    )


def _ceiling_hit(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry | None
) -> bool:
    attempts_ws = ledger.attempts_for_workspace(sc.queue, cand.ws.task_id)
    if attempts_ws < ledger.ATTEMPT_CEILING_PER_WORKSPACE:
        return False
    _report(
        sc, cand, watch, entry, reason="per-workspace attempt ceiling reached",
        authority_holder=HOLDER_HUMAN,
    )
    return True


def _build_context(
    sc: SweepContext, cand: Candidate, readiness: pr_ready_mod.ReadinessResult | None,
    park_head: str | None,
) -> recipes_mod.RecipeContext:
    return recipes_mod.RecipeContext(
        ws=cand.ws, latest=cand.latest, blocking=cand.blocking, question=cand.question,
        blocker=cand.blocker, pr_number=cand.pr_number, forge_target=cand.forge_target,
        engine_cfg=cand.engine_cfg, readiness=readiness, now=sc.now, deps=sc.deps,
        park_head=park_head,
    )


def _read_readiness(sc: SweepContext, cand: Candidate) -> pr_ready_mod.ReadinessResult | None:
    if cand.pr_number is None:
        return None
    return sc.read_readiness(cand.pr_number, cand.ws.task_id, forge_target=cand.forge_target)


def _live_head(readiness: pr_ready_mod.ReadinessResult | None) -> str | None:
    if not isinstance(readiness, pr_ready_mod.PrReadiness) or not readiness.head_sha:
        return None
    return readiness.head_sha


def _head_moved(entry: WatchEntry, head: str | None) -> bool:
    return entry.park_head is not None and head is not None and head != entry.park_head


_Settled = tuple[Candidate, WatchEntry | None, pr_ready_mod.ReadinessResult | None]


def _read_and_settle(
    sc: SweepContext, cand: Candidate, watch: dict[str, WatchEntry], entry: WatchEntry | None
) -> _Settled | None:
    readiness = _read_readiness(sc, cand)
    cand = replace(cand, head=_live_head(readiness))
    if entry is None or not entry.exhausted:
        return cand, entry, readiness
    if _head_moved(entry, cand.head):
        return cand, _next_entry(cand, None, next_at=sc.now, attempts=0), readiness
    watch[cand.ws.task_id] = replace(
        entry, next_at=sc.now + REPROBE_INTERVAL_S, park_head=_park_head(cand, entry)
    )
    return None


def _read_spent(cand: Candidate) -> bool | None:
    return False if cand.pr_number is not None else None


def _process_settled(
    sc: SweepContext, watch: dict[str, WatchEntry], settled: _Settled
) -> bool | None:
    cand, entry, readiness = settled
    if _observed_noop_twice(entry):
        _report(
            sc, cand, watch, entry, reason="the action left the workspace parked twice",
            authority_holder=HOLDER_NOBODY,
        )
        return _read_spent(cand)
    entry = _retry_after_observe(entry)
    if _ceiling_hit(sc, cand, watch, entry):
        return _read_spent(cand)
    context = _build_context(sc, cand, readiness, _park_head(cand, entry))
    return _try_recipes(sc, cand, context, watch, entry)


def _process_row(
    sc: SweepContext, row: WorkspaceRow, watch: dict[str, WatchEntry]
) -> bool | None:
    cand = candidate_for(row, workspaces_root=sc.workspaces_root)
    if cand is None:
        watch.pop(row.task_id, None)
        return None
    entry, due = _live_entry(watch, cand, sc.now)
    if not due or _has_running_job(sc.queue, row.task_id):
        return None
    settled = _read_and_settle(sc, cand, watch, entry)
    return _read_spent(cand) if settled is None else _process_settled(sc, watch, settled)


def _intent_forge_target(detail: dict[str, Any]) -> ForgeTarget:
    owner, repo = detail.get("owner"), detail.get("repo")
    credential = detail.get("credential_env_name")
    if isinstance(owner, str) and isinstance(repo, str) and isinstance(credential, str):
        return ForgeTarget(
            repo=RepoIdentity(owner=owner, repo=repo), credential_env_name=credential
        )
    return forge_target_for_workspace(detail.get("engine_key"), None)


def _deliver_intent(queue: Queue, client: IssueClient, intent: ledger.LedgerRow) -> None:
    detail = intent.detail
    slug, title = str(detail.get("slug", "")), str(detail.get("title", ""))
    forge_target = _intent_forge_target(detail)
    number = client.ensure_shape_issue(
        title, str(detail.get("issue_body", "")), forge_target=forge_target
    )
    delivered = number is not None and client.comment(
        number, str(detail.get("comment", "")), forge_target=forge_target
    )
    kind = ledger.KIND_ISSUE_DELIVERED if delivered else ledger.KIND_ISSUE_ATTEMPT
    url = issue_mod.issue_url(slug, number) if number is not None else None
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key=intent.workspace_key, scope_ref=intent.scope_ref, shape=intent.shape,
            fingerprint=intent.fingerprint, kind=kind, engine_key=intent.engine_key,
            outcome="delivered" if delivered else "failed", detail={"issue_url": url},
            ref_id=intent.id,
        ),
    )


def drain_issue_intents(
    queue: Queue, client: IssueClient, *, limit: int = ISSUE_DRAIN_PER_TICK
) -> int:
    delivered = 0
    for intent in ledger.pending_issue_intents(queue, limit):
        try:
            _deliver_intent(queue, client, intent)
            delivered += 1
        except Exception:  # noqa: BLE001
            logger.exception("unblock sweep: issue intent %s failed", intent.id)
    return delivered


def _run_rows(sc: SweepContext, rows: Iterable[WorkspaceRow], watch: dict[str, WatchEntry]) -> int:
    acted = 0
    probed = 0
    for row in stuck_rows(rows):
        if probed >= MAX_FORGE_CANDIDATES_PER_TICK:
            break
        outcome = _process_row(sc, row, watch)
        if outcome is None:
            continue
        probed += 1
        acted += int(outcome)
    return acted


def _pruned_watch(queue: Queue, rows_by_task: dict[str, WorkspaceRow]) -> dict[str, WatchEntry]:
    return {
        tid: entry for tid, entry in load_watch(queue).items()
        if tid in rows_by_task and rows_by_task[tid].status in STUCK_WS_STATUSES
    }


def _resolve_reader(read_readiness: ReadReadiness | None) -> ReadReadiness:
    return read_readiness if read_readiness is not None else _default_read_readiness


def _resolve_deps(
    deps: recipes_mod.Deps | None, queue: Queue, workspace_index: WorkspaceIndex, pubsub: PubSub
) -> recipes_mod.Deps:
    return deps if deps is not None else default_deps(queue, workspace_index, pubsub)


def sweep(
    *,
    queue: Queue,
    pubsub: PubSub,
    workspace_index: WorkspaceIndex,
    workspaces_root: Path,
    now: float,
    lock_for: LockFor,
    enqueue_workflow: EnqueueWorkflowFn,
    read_readiness: ReadReadiness | None = None,
    deps: recipes_mod.Deps | None = None,
    issue_client: IssueClient | None = None,
) -> int:
    if disabled() or queue.paused_until() is not None:
        return 0
    ledger.ensure_schema(queue)
    sc = SweepContext(
        queue=queue, pubsub=pubsub, workspaces_root=workspaces_root, now=now, lock_for=lock_for,
        enqueue_workflow=enqueue_workflow,
        read_readiness=_resolve_reader(read_readiness),
        deps=_resolve_deps(deps, queue, workspace_index, pubsub),
    )
    rows_by_task = {row.task_id: row for row in workspace_index.rows()}
    watch = _pruned_watch(queue, rows_by_task)
    acted = _run_rows(sc, rows_by_task.values(), watch)
    _save_watch(queue, watch)
    drain_issue_intents(queue, issue_client if issue_client is not None else issue_mod)
    return acted


__all__ = [
    "BASE_INTERVAL_S",
    "DISABLE_ENV",
    "HOLDER_HUMAN",
    "HOLDER_NOBODY",
    "ISSUE_DRAIN_PER_TICK",
    "MAX_FORGE_CANDIDATES_PER_TICK",
    "MAX_INTERVAL_S",
    "NEEDS_HUMAN_SHAPE",
    "OBSERVE_DELAY_S",
    "REPROBE_CAP",
    "REPROBE_INTERVAL_S",
    "SWEEP_INTERVAL_S",
    "UNBLOCK_SKIP_REASON",
    "UNBLOCK_WATCH_META_KEY",
    "Candidate",
    "IssueClient",
    "LockFor",
    "ReadReadiness",
    "SweepContext",
    "WatchEntry",
    "candidate_for",
    "default_deps",
    "disabled",
    "drain_issue_intents",
    "exempt_reason",
    "load_watch",
    "stuck_rows",
    "sweep",
]

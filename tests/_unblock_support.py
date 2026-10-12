from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from alan import handover as handover_mod
from alan import unblock_recipes as recipes_mod
from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, ForgeTarget, RepoIdentity
from alan.events import PubSub
from alan.handover import ArtifactRef, Handover, OpenQuestion
from alan.park import Blocker, ParkReason
from alan.queue import Job, Queue
from alan.repo_status import MainCiField, Provenance
from alan.teams import _pr_feedback as pr_feedback_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.workspace import Status, Workspace
from tests._pr_branch_support import _WIDGET_FORGE

ENGINE = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
VERIFIER = "alan-verifier"


def readiness(
    *,
    head_sha: str = "abc",
    check_count: int = 1,
    merge_state_status: str = "CLEAN",
    review_decision: str = "",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    passing_checks: tuple[str, ...] = ("some-check",),
) -> pr_ready_mod.PrReadiness:
    return pr_ready_mod.PrReadiness(
        ok=not failing_checks and not pending_checks,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        head_sha=head_sha,
        check_count=check_count,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        passing_checks=passing_checks,
    )


def review_line(
    login: str | None = VERIFIER,
    state: str = "CHANGES_REQUESTED",
    commit_id: str = "old",
    review_id: int | str = 1,
) -> str:
    user: object = {"login": login} if login is not None else "nobody"
    return json.dumps({"id": review_id, "state": state, "user": user, "commit_id": commit_id})


def snapshot(
    *,
    ok: bool = True,
    human_unresolved: int = 0,
    human_items: int = 0,
    failure_reason: str = "",
) -> pr_feedback_mod.FeedbackSnapshot:
    threads = pr_feedback_mod.ThreadInfo(
        copilot_threads=(),
        human_unresolved=human_unresolved,
        other_bot_unresolved=0,
        total=human_unresolved,
    )
    comments = pr_feedback_mod.CommentInfo(
        copilot_items=(), human_items=human_items, other_bot_items=0, total=human_items
    )
    return pr_feedback_mod.FeedbackSnapshot(
        ok=ok,
        thread_info=threads,
        shadow_comment_info=comments,
        blocking_comment_info=comments,
        failure_reason=failure_reason,
    )


def main_ci(*, status: str = "fresh", red_count: int | None = 0) -> MainCiField:
    return MainCiField(
        provenance=Provenance(status=status, detail="d"),  # type: ignore[arg-type]
        green_count=1,
        red_count=red_count,
        counted_over="main_ci_workflow",
    )


def deps(
    *,
    review_stream: str | None = "",
    feedback: pr_feedback_mod.FeedbackSnapshot | None = None,
    ci: MainCiField | None = None,
    dismiss: bool = True,
    dismiss_calls: list[tuple[int, str, ForgeTarget]] | None = None,
    feedback_raises: BaseException | None = None,
) -> recipes_mod.Deps:
    calls = dismiss_calls if dismiss_calls is not None else []
    snap = feedback if feedback is not None else snapshot()

    def _feedback(_pr: int, _forge: ForgeTarget) -> pr_feedback_mod.FeedbackSnapshot:
        if feedback_raises is not None:
            raise feedback_raises
        return snap

    def _dismiss(pr: int, task_id: str, forge: ForgeTarget) -> bool:
        calls.append((pr, task_id, forge))
        return dismiss

    return recipes_mod.Deps(
        review_stream=lambda _pr, _forge: review_stream,
        feedback=_feedback,
        main_ci=lambda _cfg: ci,
        dismiss_stale_reviews=_dismiss,
    )


def question(
    *,
    question_id: str = "pr-merge-timeout",
    question_class: str | None = "merge_block_escalation",
    text: str = "PR #123 not mergeable after 630s.",
    options: tuple[str, str] = ("option one", "option two"),
    resolvers: dict[str, str] | None = None,
    effects: dict[str, str] | None = None,
    blocking: bool = True,
    resolver_role: str = "human",
    answer: str | None = None,
) -> OpenQuestion:
    return OpenQuestion(
        id=question_id,
        question=text,
        proposed_answer_space=list(options),
        recommended=options[0],
        question_class=question_class,  # type: ignore[arg-type]
        resolver_role=resolver_role,
        blocking=blocking,
        answer=answer,
        option_resolvers=resolvers or {},
        option_effects=effects or {},  # type: ignore[arg-type]
    )


def handover(
    ws: Workspace,
    *,
    from_team: str = "pr_merge",
    pr_number: int | None = 123,
    questions: list[OpenQuestion] | None = None,
    suggestions: list[str] | None = None,
    next_hop: str | None = None,
    handover_type: str = "forward",
    timestamp: datetime | None = None,
    handover_id: str | None = None,
    artifacts: list[ArtifactRef] | None = None,
) -> Handover:
    return Handover(
        handover_id=handover_id or f"handover_{ws.task_id}_seed",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id=ws.task_id,
        from_team=from_team,
        from_invocation_id="inv-seed",
        to_team_suggestions=suggestions or [],
        next_hop=next_hop,
        iteration=1,
        timestamp_utc=timestamp if timestamp is not None else datetime.now(UTC),
        full_synthesis="synthesis",
        pr_number=pr_number,
        open_questions=questions or [],
        artifacts_produced=artifacts or [],
    )


def parked_ws(
    root: Path,
    *,
    title: str = "parked",
    status: Status = Status.AWAITING_INPUT,
    park_reason: ParkReason | None = ParkReason.QUESTION,
    blockers: list[Blocker] | None = None,
    team: str | None = None,
    merge_block_class: str | None = None,
    pending_first_hop: str | None = None,
) -> Workspace:
    ws = workspace_mod.create(title, "d", root=root, team=team, pending_first_hop=pending_first_hop)
    resolved = (
        blockers
        if blockers is not None
        else [Blocker(kind="reason", name="pr-merge-timeout", detail="timeout")]
    )
    return workspace_mod.transition(
        ws,
        status,
        park_reason=park_reason,
        blockers=resolved,
        merge_block_class=merge_block_class,
    )


def save(ws: Workspace, ho: Handover) -> Handover:
    handover_mod.save_handover(ws, ho)
    return ho


def context(
    ws: Workspace,
    *,
    latest: Handover | None = None,
    blocker: Blocker | None = None,
    pr_number: int | None = 123,
    ready: pr_ready_mod.ReadinessResult | None = None,
    recipe_deps: recipes_mod.Deps | None = None,
    attempts_fp: int = 0,
    now: float = 0.0,
) -> recipes_mod.RecipeContext:
    q = recipes_mod.blocking_question(latest)
    return recipes_mod.RecipeContext(
        ws=ws,
        latest=latest,
        blocking=latest if q is not None else None,
        question=q,
        blocker=blocker if blocker is not None else recipes_mod.single_blocker(ws),
        pr_number=pr_number,
        forge_target=_WIDGET_FORGE,
        engine_cfg=ENGINE,
        readiness=ready,
        now=now,
        deps=recipe_deps if recipe_deps is not None else deps(),
        attempts_fp=attempts_fp,
    )


def fake_enqueue(
    *,
    queue: Queue,
    pubsub: PubSub,
    workspace: Workspace,
    team: object,
    prior: Handover | None,
    parent_handover_id: str | None = None,
    instructions: str | None = None,
    instructions_origin: str | None = None,
    task_id: str | None = None,
    eligible_at: float | None = None,
) -> tuple[Job, str]:
    del pubsub, prior
    job = queue.enqueue(
        task_id or workspace.task_id,
        team.name,  # type: ignore[attr-defined]
        workspace_id=workspace.workspace_id,
        parent_handover_id=parent_handover_id,
        instructions=instructions,
        instructions_origin=instructions_origin,
        eligible_at=eligible_at,
    )
    return job, "inv-fake"


def tracking_enqueue(
    dispatched: list[tuple[str, str | None, str | None]],
) -> Callable[..., tuple[Job, str]]:
    def _enqueue(
        *,
        queue: Queue,
        pubsub: PubSub,
        workspace: Workspace,
        team: object,
        prior: Handover | None,
        parent_handover_id: str | None = None,
        instructions: str | None = None,
        instructions_origin: str | None = None,
        task_id: str | None = None,
        eligible_at: float | None = None,
    ) -> tuple[Job, str]:
        job, inv = fake_enqueue(
            queue=queue,
            pubsub=pubsub,
            workspace=workspace,
            team=team,
            prior=prior,
            parent_handover_id=parent_handover_id,
            instructions=instructions,
            instructions_origin=instructions_origin,
            task_id=task_id,
            eligible_at=eligible_at,
        )
        dispatched.append((team.name, instructions_origin, instructions))  # type: ignore[attr-defined]
        return job, inv

    return _enqueue


def never_enqueue(**_kwargs: object) -> tuple[Job, str]:
    raise AssertionError("enqueue_workflow should not be called")


class NullLock:
    def __enter__(self) -> "NullLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def lock_for(workspace_id: str) -> NullLock:
    del workspace_id
    return NullLock()


class FakeIssueClient:
    def __init__(self, *, number: int | None = 7, comment_ok: bool = True) -> None:
        self.number = number
        self.comment_ok = comment_ok
        self.ensured: list[tuple[str, str, str]] = []
        self.comments: list[tuple[int, str, str]] = []

    def ensure_shape_issue(self, title: str, body: str, *, forge_target: ForgeTarget) -> int | None:
        self.ensured.append((title, body, forge_target.repo.slug))
        return self.number

    def comment(self, number: int, body: str, *, forge_target: ForgeTarget) -> bool:
        self.comments.append((number, body, forge_target.repo.slug))
        return self.comment_ok

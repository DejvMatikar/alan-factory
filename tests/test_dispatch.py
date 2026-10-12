from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import merge_gate as merge_gate_mod
from alan import workspace as workspace_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.claude_cli import OnProcStarted
from alan.dispatch import (
    assert_team_dispatchable,
    dispatch_team_core,
    prior_handover_for_task,
    resolver_team_for_answer,
    resolver_team_for_question,
    team_is_dispatchable,
)
from alan.engine_config import DEFAULT_FORGE_TARGET, EngineConfig, Posture, RepoIdentity, register
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion, save_handover
from alan.queue import Queue
from alan.teams import _pr_state_paths
from alan.teams._base import EmitFn
from alan.teams._pr_ready import PrReadiness
from alan.workflow import PendingTaskSpec
from alan.workspace import (
    EngineSnapshot,
    Status,
    Task,
    TaskStatus,
    Workspace,
    stamp_engine_snapshot,
)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture(autouse=True)
def _fix_cycle_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "dispatch-fix-cycle-state"))


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


class _StubFixTeam:
    name = "implementation_fix"
    to_team_suggestions: tuple[str, ...] = ()
    supports_resume = False

    def plan_workflow(
        self, workspace: Workspace, prior: Handover | None
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [PendingTaskSpec(id="worker", role="worker", model="sonnet")]

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        raise NotImplementedError


class _StubMergeTeam(_StubFixTeam):
    name = "pr_merge"


_SKIPPED_MERGE_GATE = merge_gate_mod.MergeGateDecision.skipped("no pull request in this test")


def test_dispatch_team_core_persists_sticky_instructions_for_consuming_team(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-persist", "d", root=ws_root)
    outcome = dispatch_team_core(
        workspace=ws,
        task_id=ws.task_id,
        team=_StubFixTeam(),
        queue=queue,
        pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow,
        merge_gate=_SKIPPED_MERGE_GATE,
        instructions="focus on this",
    )
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions[ws.task_id].text == "focus on this"
    assert reloaded.meta.dispatch_instructions[ws.task_id].origin == "dispatch"
    assert reloaded.meta.engine_snapshot is not None
    job = queue.get_job(outcome.job.id)
    assert job is not None
    assert job.instructions_origin == "dispatch"
    assert outcome.merge_gate is _SKIPPED_MERGE_GATE


def test_dispatch_team_core_skips_persistence_for_non_consuming_team(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-non-consuming", "d", root=ws_root)
    dispatch_team_core(
        workspace=ws,
        task_id=ws.task_id,
        team=_StubMergeTeam(),
        queue=queue,
        pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow,
        merge_gate=_SKIPPED_MERGE_GATE,
        instructions="gate json",
    )
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions == {}


def test_dispatch_team_core_leaves_store_untouched_for_empty_instructions(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-empty-instructions", "d", root=ws_root)
    dispatch_team_core(
        workspace=ws,
        task_id=ws.task_id,
        team=_StubFixTeam(),
        queue=queue,
        pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow,
        merge_gate=_SKIPPED_MERGE_GATE,
        instructions=None,
    )
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions == {}


def test_dispatch_team_core_records_an_attempted_merge_gate_decision(
    queue: Queue, ws_root: Path,
) -> None:
    ws = workspace_mod.create("dispatch-merge-gate-attempted", "d", root=ws_root)
    attempt = merge_gate_mod.MergeGateNotAttempted(
        PrReadiness(ok=True, merge_state_status="CLEAN", review_decision="APPROVED"),
    )
    decision = merge_gate_mod.MergeGateDecision.attempted(attempt)
    outcome = dispatch_team_core(
        workspace=ws,
        task_id=ws.task_id,
        team=_StubFixTeam(),
        queue=queue,
        pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow,
        merge_gate=decision,
        instructions=None,
    )
    assert outcome.merge_gate is decision
    assert outcome.merge_gate.policy == "attempted"
    assert outcome.merge_gate.attempt is attempt


@pytest.mark.parametrize(
    ("question_class", "from_team", "expected"),
    [
        ("merge_block_escalation", "pr_merge", "pr_merge"),
        ("verification_escalation", "verification", "verification"),
        ("fix_escalation", "implementation_fix", "implementation_fix"),
        ("orchestrated_completeness", "ui_review", "ui_review"),
        ("orchestrated_completeness", "brainstorming", "brainstorming"),
    ],
)
def test_resolver_team_for_question_pinned_map(
    question_class: str, from_team: str, expected: str
) -> None:
    assert resolver_team_for_question(question_class, from_team) == expected


def test_resolver_team_for_question_orchestrated_completeness_not_class_keyed() -> None:
    ui = resolver_team_for_question("orchestrated_completeness", "ui_review")
    brainstorm = resolver_team_for_question("orchestrated_completeness", "brainstorming")
    assert ui == "ui_review"
    assert brainstorm == "brainstorming"
    assert ui != brainstorm


def test_resolver_team_for_question_unknown_class_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        resolver_team_for_question("not_a_real_class", "implementation")
    assert exc_info.value.status_code == 400


def test_resolver_team_for_question_none_class_raises_400() -> None:
    with pytest.raises(AppError) as exc_info:
        resolver_team_for_question(None, "implementation")
    assert exc_info.value.status_code == 400


def test_resolver_team_for_answer_uses_mapped_option_when_present() -> None:
    question = OpenQuestion(
        id="non_actionable_feedback_x",
        question="not actionable",
        proposed_answer_space=["retry", "fix by hand"],
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={"fix by hand": "verification"},
    )
    assert resolver_team_for_answer(question, "implementation_fix", "fix by hand") == "verification"


def test_resolver_team_for_answer_falls_back_to_class_when_answer_unmapped() -> None:
    question = OpenQuestion(
        id="non_actionable_feedback_x",
        question="not actionable",
        proposed_answer_space=["retry", "fix by hand"],
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={"fix by hand": "verification"},
    )
    assert resolver_team_for_answer(question, "implementation_fix", "retry") == "implementation_fix"


def test_resolver_team_for_answer_falls_back_when_no_option_resolvers_at_all() -> None:
    question = OpenQuestion(
        id="q1",
        question="which?",
        proposed_answer_space=["accept", "expand"],
        question_class="orchestrated_completeness",
        resolver_role="human",
        blocking=True,
    )
    assert resolver_team_for_answer(question, "ui_review", "accept") == "ui_review"


def test_resolver_team_for_answer_unmapped_answer_still_raises_400_for_unknown_class() -> None:
    question = OpenQuestion(
        id="q1",
        question="which?",
        proposed_answer_space=["a", "b"],
        question_class=None,
        resolver_role="human",
        blocking=True,
    )
    with pytest.raises(AppError) as exc_info:
        resolver_team_for_answer(question, "implementation", "a")
    assert exc_info.value.status_code == 400


def _ws(root: Path, *, tasks: list[Task] | None = None) -> Workspace:
    return workspace_mod.create("Dispatch Guard", "d", root=root, tasks=tasks or [])


def test_assert_team_dispatchable_passes_for_root_task(ws_root: Path) -> None:
    ws = _ws(ws_root)
    assert_team_dispatchable(ws, ws.task_id, "implementation")


def test_assert_team_dispatchable_unknown_root_task_id_404s(ws_root: Path) -> None:
    ws = _ws(ws_root)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, "not-the-task", "implementation")
    assert exc_info.value.status_code == 404


def test_assert_team_dispatchable_multi_task_unknown_id_404s(ws_root: Path) -> None:
    tasks = [Task(id="plan", status=TaskStatus.PENDING)]
    ws = workspace_mod.create("Multi Guard", "d", root=ws_root, tasks=tasks)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, "unknown", "implementation")
    assert exc_info.value.status_code == 404


def test_assert_team_dispatchable_multi_task_known_id_passes(ws_root: Path) -> None:
    tasks = [Task(id="plan", status=TaskStatus.PENDING)]
    ws = workspace_mod.create("Multi Guard OK", "d", root=ws_root, tasks=tasks)
    assert_team_dispatchable(ws, "plan", "implementation")


def test_assert_team_dispatchable_archived_workspace_409s(ws_root: Path) -> None:
    ws = _ws(ws_root)
    ws = workspace_mod.transition(ws, Status.ARCHIVED)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert exc_info.value.status_code == 409


def test_assert_team_dispatchable_posture_withheld_422s(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert exc_info.value.status_code == 422
    assert "implementation" in exc_info.value.detail


def test_assert_team_dispatchable_posture_admitted_passes(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(posture=Posture.propose, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    assert_team_dispatchable(ws, ws.task_id, "implementation")


def test_assert_team_dispatchable_downgrade_floors_manual_dispatch(ws_root: Path) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.observe)
    )
    ws = workspace_mod.create("floor test", "d", root=ws_root, engine_key="acme/widget")
    snapshot = EngineSnapshot(posture=Posture.self_merge, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "pr_merge")
    assert exc_info.value.status_code == 422


def test_assert_team_dispatchable_upgrade_holds_at_pinned_posture(ws_root: Path) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.autonomous)
    )
    ws = workspace_mod.create("hold test", "d", root=ws_root, engine_key="acme/widget")
    snapshot = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert exc_info.value.status_code == 422


def test_assert_team_dispatchable_falls_back_to_registered_engine_key_posture(
    ws_root: Path,
) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.propose)
    )
    ws = workspace_mod.create(
        "Engine Key Guard", "d", root=ws_root, engine_key="acme/widget"
    )
    assert ws.meta.engine_snapshot is None
    assert_team_dispatchable(ws, ws.task_id, "implementation")


def test_assert_team_dispatchable_unregistered_engine_key_falls_back_to_default(
    ws_root: Path,
) -> None:
    ws = workspace_mod.create(
        "Unregistered Engine Key Guard", "d", root=ws_root, engine_key="no/such-engine"
    )
    assert ws.meta.engine_snapshot is None
    assert_team_dispatchable(ws, ws.task_id, "implementation")


def test_assert_team_dispatchable_empty_pinned_graph_is_unrestricted(ws_root: Path) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="unconfigured"), posture=Posture.autonomous
        )
    )
    ws = workspace_mod.create(
        "Unconfigured Roster", "d", root=ws_root, engine_key="acme/unconfigured"
    )
    assert_team_dispatchable(ws, ws.task_id, "opportunity_scout")


def test_assert_team_dispatchable_off_roster_team_refused_naming_team_and_engine(
    ws_root: Path,
) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"implementation": ["verification"], "verification": []},
        )
    )
    ws = workspace_mod.create("Roster Guard", "d", root=ws_root, engine_key="acme/sprocket")
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "opportunity_scout")
    assert exc_info.value.status_code == 422
    assert "opportunity_scout" in exc_info.value.detail
    assert "acme/sprocket" in exc_info.value.detail


def test_assert_team_dispatchable_destination_only_sink_is_admitted(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(
        posture=Posture.autonomous,
        transition_graph={"implementation": ["verification"]},
    )
    ws = stamp_engine_snapshot(ws, snapshot)
    assert_team_dispatchable(ws, ws.task_id, "verification")


def test_assert_team_dispatchable_pinned_snapshot_admits_team_excluded_from_live_graph(
    ws_root: Path,
) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"implementation": ["verification"], "verification": []},
        )
    )
    ws = workspace_mod.create("Pinned Roster", "d", root=ws_root, engine_key="acme/sprocket")
    snapshot = EngineSnapshot(
        posture=Posture.autonomous,
        transition_graph={
            "implementation": ["verification"],
            "verification": [],
            "opportunity_scout": ["research"],
            "research": [],
        },
    )
    ws = stamp_engine_snapshot(ws, snapshot)
    assert_team_dispatchable(ws, ws.task_id, "opportunity_scout")


def test_assert_team_dispatchable_off_roster_names_engine_from_pinned_snapshot_config(
    ws_root: Path,
) -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="sprocket"),
        posture=Posture.autonomous,
        transition_graph={"implementation": ["verification"], "verification": []},
    )
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(
        posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
    )
    ws = stamp_engine_snapshot(ws, snapshot)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "opportunity_scout")
    assert exc_info.value.status_code == 422
    assert "acme/sprocket" in exc_info.value.detail


def test_assert_team_dispatchable_off_roster_names_default_engine_with_no_pin(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "dependency_sweep")
    assert exc_info.value.status_code == 422
    assert "dependency_sweep" in exc_info.value.detail
    assert "DejvMatikar/alan" in exc_info.value.detail


def test_assert_team_dispatchable_off_roster_names_default_engine_for_unregistered_engine_key(
    ws_root: Path,
) -> None:
    ws = workspace_mod.create(
        "Unregistered Engine Key Roster", "d", root=ws_root, engine_key="no/such-engine"
    )
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "dependency_sweep")
    assert exc_info.value.status_code == 422
    assert "dependency_sweep" in exc_info.value.detail
    assert "DejvMatikar/alan" in exc_info.value.detail


def test_assert_team_dispatchable_default_engine_admits_every_team(ws_root: Path) -> None:
    ws = _ws(ws_root)
    for team in (
        "research",
        "brainstorming",
        "architecture",
        "implementation_plan",
        "decomposer",
        "implementation",
        "implementation_fix",
        "verification",
        "pr_merge",
        "deploy_verify",
        "maintenance",
        "triage",
        "opportunity_scout",
        "ui_review",
    ):
        assert_team_dispatchable(ws, ws.task_id, team)


def test_team_is_dispatchable_true_for_admitted_on_roster_team(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph={"pr_merge": []})
    ws = stamp_engine_snapshot(ws, snapshot)
    assert team_is_dispatchable(ws, "pr_merge") is True


def test_team_is_dispatchable_false_when_posture_denies(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    assert team_is_dispatchable(ws, "implementation") is False


def test_team_is_dispatchable_false_when_off_roster(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(
        posture=Posture.autonomous, transition_graph={"pr_merge": []},
    )
    ws = stamp_engine_snapshot(ws, snapshot)
    assert team_is_dispatchable(ws, "implementation_fix") is False


def test_team_is_dispatchable_true_when_roster_empty(ws_root: Path) -> None:
    ws = _ws(ws_root)
    snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph={})
    ws = stamp_engine_snapshot(ws, snapshot)
    assert team_is_dispatchable(ws, "opportunity_scout") is True


def test_auto_continue_teams_pre_change_membership_unwidened() -> None:
    from alan.teams import AUTO_CONTINUE_TEAMS

    assert AUTO_CONTINUE_TEAMS == frozenset({
        "deploy_verify",
        "implementation",
        "implementation_fix",
        "pr_merge",
        "verification",
    })


def _prior_escalation(ws: Workspace, pr_number: int | None) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verifier requested changes",
        pr_number=pr_number,
    )


def test_dispatch_team_core_bumps_fix_cycle_count_for_implementation_fix(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-bump-fix-cycle", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 900))
    dispatch_team_core(
        workspace=ws, task_id=ws.task_id, team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert _pr_state_paths.load_fix_cycle_state(900, DEFAULT_FORGE_TARGET).count == 1


def test_dispatch_team_core_does_not_bump_for_non_fix_team(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-no-bump-other-team", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 901))
    dispatch_team_core(
        workspace=ws, task_id=ws.task_id, team=_StubMergeTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert _pr_state_paths.load_fix_cycle_state(901, DEFAULT_FORGE_TARGET).count == 0


def test_dispatch_team_core_no_bump_without_prior_handover(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-no-bump-no-prior", "d", root=ws_root)
    outcome = dispatch_team_core(
        workspace=ws, task_id=ws.task_id, team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert outcome.job is not None


def test_dispatch_team_core_no_bump_without_pr_number(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("dispatch-no-bump-no-pr", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, None))
    outcome = dispatch_team_core(
        workspace=ws, task_id=ws.task_id, team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert outcome.job is not None


def test_assert_team_dispatchable_fix_ceiling_reached_409s(ws_root: Path) -> None:
    ws = workspace_mod.create("dispatch-ceiling-reached", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 902))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(902, DEFAULT_FORGE_TARGET)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation_fix")
    assert exc_info.value.status_code == 409
    assert "override" in exc_info.value.detail


def test_assert_team_dispatchable_fix_ceiling_below_budget_passes(ws_root: Path) -> None:
    ws = workspace_mod.create("dispatch-ceiling-not-reached", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 903))
    for _ in range(5):
        _pr_state_paths.bump_fix_cycle_count(903, DEFAULT_FORGE_TARGET)
    assert_team_dispatchable(ws, ws.task_id, "implementation_fix")


def test_assert_team_dispatchable_archived_wins_over_fix_ceiling(ws_root: Path) -> None:
    ws = workspace_mod.create("dispatch-ceiling-archived", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 904))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(904, DEFAULT_FORGE_TARGET)
    ws = workspace_mod.transition(ws, Status.ARCHIVED)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, ws.task_id, "implementation_fix")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "workspace is archived"


def test_assert_team_dispatchable_fix_ceiling_not_checked_for_other_teams(
    ws_root: Path,
) -> None:
    ws = workspace_mod.create("dispatch-ceiling-other-team", "d", root=ws_root)
    save_handover(ws, _prior_escalation(ws, 905))
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(905, DEFAULT_FORGE_TARGET)
    assert_team_dispatchable(ws, ws.task_id, "verification")


def test_prior_handover_for_task_resolves_parked_blocked_handover_id(
    ws_root: Path,
) -> None:
    task = Task(id="t1", status=TaskStatus.AWAITING_INPUT, depends_on=[])
    ws = workspace_mod.create("dispatch-blocked-handover-id", "d", root=ws_root, tasks=[task])
    park_handover = _prior_escalation(ws, 906).model_copy(
        update={"handover_id": "h-fix-ceiling-park"}
    )
    save_handover(ws, park_handover)
    ws = workspace_mod.update_task_status(
        ws, "t1", TaskStatus.AWAITING_INPUT, blocked_handover_id="h-fix-ceiling-park"
    )
    resolved = prior_handover_for_task(ws, "t1")
    assert resolved is not None
    assert resolved.handover_id == "h-fix-ceiling-park"

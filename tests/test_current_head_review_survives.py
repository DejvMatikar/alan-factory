from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server, unblock_recipes, unblock_sweep
from alan import workspace as workspace_mod
from alan.dispatch import EnqueueWorkflowFn
from alan.dispatcher import Dispatcher
from alan.events import PubSub
from alan.handover import Decision, Handover, OpenQuestion, save_handover
from alan.merge_gate import MergeGateBlocked
from alan.merge_recovery import MergeBlockClass
from alan.park import Blocker, ParkReason
from alan.queue import Job, Queue
from alan.routes._workspaces_merge_gate import apply_merge_gate_to_manual_dispatch
from alan.teams import TEAMS, _pr_ready, _pr_state_paths
from alan.workspace import (
    Status,
    Workspace,
    create,
    read,
)
from alan.workspace_index import WorkspaceIndex
from tests import review_forge_fake as forge_fake

_ALAN_SRC = Path(__file__).resolve().parent.parent / "alan"


def test_pr_waiver_dismissal_imported_only_from_answer_waiver() -> None:
    importers: list[Path] = []
    for path in _ALAN_SRC.rglob("*.py"):
        if path.name in {"_pr_waiver_dismissal.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "_pr_waiver_dismissal" in text:
            importers.append(path)
    relative = sorted(str(p.relative_to(_ALAN_SRC.parent)) for p in importers)
    assert relative == ["alan/routes/_answer_waiver.py"]


def _stale_review_readiness(*, head_sha: str = "head-sha") -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1, head_sha=head_sha,
    )


def _current_head_fake(pr_review_id: int = 5001) -> forge_fake.ReviewForgeFake:
    return forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=pr_review_id, login="alan-verifier", state="CHANGES_REQUESTED",
                commit_id="head-sha",
            ),
        ],
        head_sha="head-sha",
        readiness=_stale_review_readiness(),
    )


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
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


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


_NON_ACTIONABLE_PR_MERGE_OPTION = (
    "Re-dispatch pr_merge — the block is external gate state, not code."
)
_NON_ACTIONABLE_RESOLVE_OPTION = "Resolve by hand."


def _non_actionable_question() -> OpenQuestion:
    return OpenQuestion(
        id="fix-escalation-1",
        question="verification could not make progress",
        proposed_answer_space=[_NON_ACTIONABLE_PR_MERGE_OPTION, _NON_ACTIONABLE_RESOLVE_OPTION],
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        recommended=_NON_ACTIONABLE_PR_MERGE_OPTION,
        option_resolvers={
            _NON_ACTIONABLE_PR_MERGE_OPTION: "pr_merge",
            _NON_ACTIONABLE_RESOLVE_OPTION: "verification",
        },
    )


def _non_actionable_escalation(ws: Workspace, *, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_fix",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="non-actionable feedback",
        open_questions=[_non_actionable_question()],
        pr_number=pr_number,
    )


def test_non_actionable_park_re_dispatch_merge_option_leaves_current_head_review(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Non Actionable Park", "d", root=ws_root)
    save_handover(ws, _non_actionable_escalation(ws, pr_number=701))
    fake = _current_head_fake()
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws,
        {"question_id": "fix-escalation-1", "chosen_option": _NON_ACTIONABLE_PR_MERGE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert queue.list_jobs() == []
    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.STALE_REVIEW.value


async def test_manual_redispatch_merge_gate_leaves_current_head_review(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("Manual Redispatch", "d", root=ws_root)
    prior = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_verify",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="approved",
        pr_number=702,
    )
    fake = _current_head_fake(pr_review_id=6001)
    forge_fake.install(fake, monkeypatch)

    decision, instructions = await apply_merge_gate_to_manual_dispatch(ws, prior, None)

    assert decision.policy == "attempted"
    assert decision.attempt is not None
    assert isinstance(decision.attempt, MergeGateBlocked)
    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"
    assert instructions is not None
    assert "stale_review" in instructions


def _sweep_readiness(*, head_sha: str = "head-sha") -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
        head_sha=head_sha, check_count=1, failing_checks=(), pending_checks=(),
        passing_checks=("some-check",),
    )


class _NullLock:
    def __enter__(self) -> "_NullLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _lock_for(workspace_id: str) -> _NullLock:
    del workspace_id
    return _NullLock()


def _dispatched_tracker() -> tuple[list[tuple[str, str | None]], EnqueueWorkflowFn]:
    dispatched: list[tuple[str, str | None]] = []

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
        del pubsub, prior, parent_handover_id, instructions, eligible_at
        job = queue.enqueue(
            task_id or workspace.task_id,
            team.name,  # type: ignore[attr-defined]
            workspace_id=workspace.workspace_id,
            instructions_origin=instructions_origin,
        )
        dispatched.append((team.name, instructions_origin))  # type: ignore[attr-defined]
        return job, "inv-fake"

    return dispatched, _enqueue


class _NullIssueClient:
    def ensure_shape_issue(self, title: str, body: str, *, forge_target: object) -> int | None:
        del title, body, forge_target
        return None

    def comment(self, number: int, body: str, *, forge_target: object) -> bool:
        del number, body, forge_target
        return False


def test_unblock_sweep_leaves_current_head_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(unblock_sweep.DISABLE_ENV, raising=False)
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    index = WorkspaceIndex(root=ws_root, queue=queue)
    ws = workspace_mod.create("Sweep Candidate", "d", root=ws_root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.AWAITING_INPUT,
        park_reason=ParkReason.QUESTION,
        blockers=[Blocker(kind="reason", name="pr-merge-failed", detail="d")],
    )
    question = OpenQuestion(
        id="pr-merge-failed",
        question="PR #703 failed checks: some-check.",
        proposed_answer_space=[
            "Inspect the failed check's logs, fix the underlying failure, and push a new "
            "commit to re-trigger checks.",
            unblock_recipes.TRANSIENT_RETRY_OPTION,
        ],
        recommended=(
            "Inspect the failed check's logs, fix the underlying failure, and push a new "
            "commit to re-trigger checks."
        ),
        question_class="merge_block_escalation",
        resolver_role="human",
        blocking=True,
    )
    handover = Handover(
        handover_id=f"handover_{ws.task_id}_seed",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv-seed",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="failed: some-check",
        pr_number=703,
        open_questions=[question],
    )
    save_handover(ws, handover)

    fake = _current_head_fake(pr_review_id=7001)
    forge_fake.install(fake, monkeypatch)
    monkeypatch.setattr(
        _pr_state_paths, "load_gate_rerun_state",
        lambda *_a, **_kw: _pr_state_paths.GateRerunState(
            head_sha="head-sha", count=1, lifetime=1, checks=("some-check",)
        ),
    )
    dispatched, tracking_enqueue = _dispatched_tracker()

    cleared = unblock_sweep.sweep(
        queue=queue, pubsub=PubSub(), workspace_index=index, workspaces_root=ws_root,
        now=time.time(), lock_for=_lock_for, enqueue_workflow=tracking_enqueue,
        read_readiness=lambda *_a, **_kw: _sweep_readiness(),
        issue_client=_NullIssueClient(),
    )

    assert cleared == 1
    assert dispatched == [("pr_merge", "generated")]
    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"


def _pr_merge_forward_handover(task_id: str, *, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_pr",
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["pr_merge"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="verif-approve", statement=f"judge approved PR #{pr_number}",
                rationale="ok", confidence=1.0, reversibility="low",
            )
        ],
        full_synthesis="approved",
        pr_number=pr_number,
    )


def test_dispatcher_pre_merge_gate_leaves_current_head_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase", lambda **_kw: None
    )
    root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = workspace_mod.create("Dispatcher Gate Candidate", "d", root=root)
    fake = _current_head_fake(pr_review_id=8001)
    forge_fake.install(fake, monkeypatch)

    d._apply_router_decision(  # noqa: SLF001
        ws, _pr_merge_forward_handover(ws.task_id, pr_number=704)
    )

    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"

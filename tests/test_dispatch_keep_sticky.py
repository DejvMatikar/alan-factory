from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import merge_gate as merge_gate_mod
from alan import workspace as workspace_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.claude_cli import OnProcStarted
from alan.dispatch import dispatch_team_core
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion, save_handover
from alan.queue import Queue
from alan.teams._base import EmitFn
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace

RULING = "Edit .github/workflows/ci.yml line 41: copy the `permissions` entry from line 12."
DECISION = "Human answer to question [q1]: A: sqlite"
SKIPPED_MERGE_GATE = merge_gate_mod.MergeGateDecision.skipped("no pull request in this test")


class _StubTeam:
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


class _StubNonConsumingTeam(_StubTeam):
    name = "pr_merge"


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "keep-sticky-state"))


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _dispatch(
    ws: Workspace,
    queue: Queue,
    *,
    team: _StubTeam,
    instructions: str | None,
    keep_sticky: bool,
) -> int:
    outcome = dispatch_team_core(
        workspace=ws,
        task_id=ws.task_id,
        team=team,
        queue=queue,
        pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow,
        merge_gate=SKIPPED_MERGE_GATE,
        instructions=instructions,
        keep_sticky=keep_sticky,
    )
    return outcome.job.id


def _with_sticky(ws_root: Path, name: str) -> Workspace:
    ws = workspace_mod.create(name, "d", root=ws_root)
    return workspace_mod.set_sticky_instructions(ws, ws.task_id, RULING, origin="dispatch")


def _stored_sticky(ws_root: Path, ws: Workspace) -> workspace_mod.StickyInstruction | None:
    return workspace_mod.read(ws.task_id, root=ws_root).meta.dispatch_instructions.get(ws.task_id)


def _decision_escalation(task_id: str) -> Handover:
    question = OpenQuestion(
        id="q1",
        question="which db?",
        proposed_answer_space=["sqlite", "duckdb"],
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
    )
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=[question],
    )


def test_a_kept_sticky_is_composed_ahead_of_the_supplied_instructions(
    queue: Queue, ws_root: Path
) -> None:
    ws = _with_sticky(ws_root, "keep-composed")
    job_id = _dispatch(ws, queue, team=_StubTeam(), instructions=DECISION, keep_sticky=True)
    job = queue.get_job(job_id)
    assert job is not None
    assert job.instructions == f"{RULING}\n\n{DECISION}"
    assert job.instructions_origin == "inherited"


def test_a_kept_sticky_is_left_as_the_human_wrote_it(queue: Queue, ws_root: Path) -> None:
    ws = _with_sticky(ws_root, "keep-untouched")
    _dispatch(ws, queue, team=_StubTeam(), instructions=DECISION, keep_sticky=True)
    assert _stored_sticky(ws_root, ws) == workspace_mod.StickyInstruction(
        text=RULING, origin="dispatch"
    )


def test_a_kept_sticky_alone_is_enqueued_when_no_instructions_are_supplied(
    queue: Queue, ws_root: Path
) -> None:
    ws = _with_sticky(ws_root, "keep-alone")
    job_id = _dispatch(ws, queue, team=_StubTeam(), instructions="   ", keep_sticky=True)
    job = queue.get_job(job_id)
    assert job is not None
    assert job.instructions == RULING
    assert job.instructions_origin == "inherited"


def test_supplied_instructions_replace_the_sticky_when_it_is_not_kept(
    queue: Queue, ws_root: Path
) -> None:
    ws = _with_sticky(ws_root, "keep-off")
    job_id = _dispatch(ws, queue, team=_StubTeam(), instructions=DECISION, keep_sticky=False)
    job = queue.get_job(job_id)
    assert job is not None
    assert job.instructions == DECISION
    assert job.instructions_origin == "dispatch"
    assert _stored_sticky(ws_root, ws) == workspace_mod.StickyInstruction(
        text=DECISION, origin="dispatch"
    )


def test_supplied_instructions_are_persisted_when_keeping_but_no_sticky_exists(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("keep-none-stored", "d", root=ws_root)
    job_id = _dispatch(ws, queue, team=_StubTeam(), instructions=DECISION, keep_sticky=True)
    job = queue.get_job(job_id)
    assert job is not None
    assert job.instructions == DECISION
    assert job.instructions_origin == "dispatch"
    assert _stored_sticky(ws_root, ws) == workspace_mod.StickyInstruction(
        text=DECISION, origin="dispatch"
    )


def test_a_non_consuming_team_receives_only_the_supplied_instructions_and_no_sticky(
    queue: Queue, ws_root: Path
) -> None:
    ws = _with_sticky(ws_root, "keep-non-consuming")
    job_id = _dispatch(
        ws, queue, team=_StubNonConsumingTeam(), instructions=DECISION, keep_sticky=True
    )
    job = queue.get_job(job_id)
    assert job is not None
    assert job.instructions == DECISION
    assert job.instructions_origin == "dispatch"
    assert _stored_sticky(ws_root, ws) == workspace_mod.StickyInstruction(
        text=RULING, origin="dispatch"
    )


def test_answering_after_a_human_write_dispatches_the_ruling_with_the_decision(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("put-then-answer", "d", root=ws_root)
    save_handover(ws, _decision_escalation(ws.task_id))
    put = client.put(
        f"/api/workspaces/{ws.task_id}/sticky-instructions",
        json={"text": RULING},
        headers=_headers(),
    )
    assert put.status_code == 200
    answer = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer",
        json={"question_id": "q1", "chosen_option": "sqlite"},
        headers=_headers(),
    )
    assert answer.status_code == 200
    [job] = queue.list_jobs()
    assert job.instructions is not None
    assert job.instructions.startswith(RULING)
    assert "which db?" in job.instructions
    assert "sqlite" in job.instructions
    assert _stored_sticky(ws_root, ws) == workspace_mod.StickyInstruction(
        text=RULING, origin="dispatch"
    )

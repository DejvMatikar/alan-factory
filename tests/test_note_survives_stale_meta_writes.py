from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import _dispatcher_reconcile
from alan import _dispatcher_usage_limit as _usage_limit
from alan import park as park_mod
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover, HandoverError
from alan.queue import Queue
from alan.teams import brief_refine
from alan.teams._base import TeamEvent
from alan.teams._brief_refine_block import BriefRefinePayload, RefinedRequirementItem
from alan.teams._scenario_runner import ScenarioResult
from alan.workflow import PendingTaskSpec
from alan.workspace import Requirement, Task, TaskStatus, TestScenario, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _crash(_workspace: Workspace) -> Handover:
    raise RuntimeError("kaboom")


def _forward(workspace: Workspace) -> Handover:
    return Handover(
        handover_id=f"handover_{workspace.task_id}_test",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


class _NoteThenTeam:
    name = "research"
    supports_resume = False

    def __init__(self, root: Path, note: str, act: Callable[[Workspace], Handover]) -> None:
        self._root = root
        self._note = note
        self._act = act

    def plan_workflow(
        self, _workspace: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        del inv_id
        current = workspace_mod.read(workspace.task_id, root=self._root)
        workspace_mod.update_note(current, self._note)
        return self._act(workspace)


def test_note_survives_brief_refine_accepted_requirements(root: Path) -> None:
    ws = workspace_mod.create(
        "brief-note", "d", root=root, requirements=[Requirement(text="must ship")]
    )
    stale = workspace_mod.read(ws.task_id, root=root)
    workspace_mod.update_note(workspace_mod.read(ws.task_id, root=root), "handoff note")
    req_id = stale.meta.requirements[0].id  # type: ignore[index]
    payload = BriefRefinePayload(
        requirements=(RefinedRequirementItem(id=req_id, text="must ship"),),
        assumptions=(),
    )
    outcome = brief_refine._apply_payload(stale, payload)  # noqa: SLF001
    assert outcome.requirements is not None
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "handoff note"
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].text == "must ship"


def test_note_survives_brief_refine_no_gate_assumptions_only(root: Path) -> None:
    ws = workspace_mod.create("brief-note-nogate", "d", root=root)
    stale = workspace_mod.read(ws.task_id, root=root)
    workspace_mod.update_note(
        workspace_mod.read(ws.task_id, root=root), "handoff note nogate"
    )
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(
                id=None, text="new req", origin="user_text", locator="new req"
            ),
        ),
        assumptions=("inferred one",),
    )
    outcome = brief_refine._apply_payload(stale, payload)  # noqa: SLF001
    assert outcome.requirements is None
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "handoff note nogate"
    assert "new req" in reloaded.meta.assumptions
    assert "inferred one" in reloaded.meta.assumptions


def test_rejected_brief_refine_writes_nothing_after_note(root: Path, tmp_path: Path) -> None:
    ws = workspace_mod.create(
        "brief-note-rejected", "d", root=root, requirements=[Requirement(text="must ship")]
    )
    stale = workspace_mod.read(ws.task_id, root=root)
    workspace_mod.update_note(
        workspace_mod.read(ws.task_id, root=root), "handoff note rejected"
    )
    outcome = brief_refine._apply_payload(stale, None)  # noqa: SLF001
    assert outcome.requirements == stale.meta.requirements
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "handoff note rejected"
    assert reloaded.meta.requirements is not None
    assert [r.text for r in reloaded.meta.requirements] == ["must ship"]
    audit_path = tmp_path / "logs" / brief_refine.AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(row["action"] == "brief_refine_rejected" for row in rows)


def test_note_survives_final_requirement_check(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create(
        "final-check", "d", root=root, requirements=[Requirement(text="must ship")]
    )
    task = Task(
        id="only",
        status=TaskStatus.COMPLETED,
        scenarios=[TestScenario(id="sc-1", description="d", given="g", when="w", then="t")],
    )
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    workspace_mod._write_meta(ws.path, new_meta)  # noqa: SLF001
    stale = workspace_mod.read(ws.task_id, root=root)

    def fake_run_scenarios(
        scenarios: list[TestScenario], **_kw: object
    ) -> list[ScenarioResult]:
        current = workspace_mod.read(ws.task_id, root=root)
        workspace_mod.update_note(current, "handoff note")
        return [
            ScenarioResult(
                scenario_id=scenarios[0].id,
                status="PASS",
                diagnostic="",
                stdout_tail="",
                stderr_tail="",
                returncode=0,
                elapsed_s=0.0,
            )
        ]

    monkeypatch.setattr(_dispatcher_reconcile, "run_scenarios", fake_run_scenarios)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._maybe_evaluate_requirements(stale, None)  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "handoff note"
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].text == "must ship"


def test_note_survives_crash_park(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("crash-note", "d", root=root)
    team = _NoteThenTeam(root, "note-during-crash", _crash)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "note-during-crash"
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is park_mod.ParkReason.CRASH


def test_note_survives_handover_save_failure_park(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("handover-fail-note", "d", root=root)
    team = _NoteThenTeam(root, "note-during-handover-fail", _forward)

    def raising_save_handover(*_a: object, **_kw: object) -> Path:
        raise HandoverError("disk full")

    monkeypatch.setattr("alan.dispatcher.save_handover", raising_save_handover)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "note-during-handover-fail"
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is park_mod.ParkReason.CRASH


def test_note_survives_usage_limit_stranded_park(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("usage-limit-note", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    stale = workspace_mod.read(ws.task_id, root=root)
    workspace_mod.update_note(
        workspace_mod.read(ws.task_id, root=root), "note-during-usage-limit"
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    recovered = d._recover_stranded(  # noqa: SLF001
        claimed, stale, _usage_limit._Recovery(), label="usage_limit"  # noqa: SLF001
    )
    assert recovered is False
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.note == "note-during-usage-limit"
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is park_mod.ParkReason.CRASH


def test_fail_workspace_handles_vanished_workspace_like_write_error(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("vanish-me", "d", root=root)
    workspace_mod.delete(ws.task_id, root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._fail_workspace(ws, "boom")  # noqa: SLF001
    assert any("failed to flip workspace" in r.message for r in caplog.records)

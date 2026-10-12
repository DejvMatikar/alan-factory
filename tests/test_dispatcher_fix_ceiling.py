from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams import _pr_state_paths
from alan.workspace import Workspace

_FIX_DECISION = RouterDecision(
    kind="enqueue", team="implementation_fix", reason="request changes"
)


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
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _ws(root: Path) -> Workspace:
    return workspace_mod.create("fix ceiling ws", "d", root=root)


def _handover(ws: Workspace, pr_number: int | None) -> Handover:
    return Handover(
        handover_id="h-verif",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verifier found a real defect",
        pr_number=pr_number,
    )


def test_enforce_fix_ceiling_ignores_non_fix_enqueue(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    decision = RouterDecision(kind="enqueue", team="verification")
    out, out_handover = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 100), decision, ws.task_id
    )
    assert out is decision
    assert out_handover.pr_number == 100


def test_enforce_fix_ceiling_ignores_missing_pr_number(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    out, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, None), _FIX_DECISION, ws.task_id
    )
    assert out is _FIX_DECISION


def test_enforce_fix_ceiling_bumps_count_and_passes_through_below_ceiling(
    root: Path, queue: Queue
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    out, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 100), _FIX_DECISION, ws.task_id
    )
    assert out is _FIX_DECISION
    assert _pr_state_paths.load_fix_cycle_state(100, DEFAULT_FORGE_TARGET).count == 1


def test_seven_rounds_parks_on_the_seventh(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    decisions = [
        d._enforce_fix_ceiling(ws, _handover(ws, 200), _FIX_DECISION, ws.task_id)  # noqa: SLF001
        for _ in range(7)
    ]
    for decision, _ in decisions[:6]:
        assert decision.kind == "enqueue"
    parked, parked_handover = decisions[6]
    assert parked.kind == "await_human"
    assert parked.team is None
    assert parked.block_class is None
    assert "6" in (parked.reason or "")
    assert parked_handover.open_questions
    assert parked_handover.open_questions[0].blocking is True
    assert parked_handover.open_questions[0].resolver_role == "human"
    assert parked_handover.pr_number == 200


def test_park_reason_names_last_verifier_finding(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    for _ in range(6):
        d._enforce_fix_ceiling(ws, _handover(ws, 300), _FIX_DECISION, ws.task_id)  # noqa: SLF001
    parked, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 300), _FIX_DECISION, ws.task_id
    )
    assert "request changes" in (parked.reason or "")


def test_park_reason_falls_back_to_handover_synthesis_without_decision_reason(
    root: Path, queue: Queue
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    reasonless = RouterDecision(kind="enqueue", team="implementation_fix", reason=None)
    for _ in range(6):
        d._enforce_fix_ceiling(ws, _handover(ws, 310), reasonless, ws.task_id)  # noqa: SLF001
    parked, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 310), reasonless, ws.task_id
    )
    assert "verifier found a real defect" in (parked.reason or "")


def test_operator_redispatch_does_not_reset_the_count(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    for _ in range(3):
        d._enforce_fix_ceiling(ws, _handover(ws, 350), _FIX_DECISION, ws.task_id)  # noqa: SLF001
    _pr_state_paths.bump_fix_cycle_count(350, DEFAULT_FORGE_TARGET)
    assert _pr_state_paths.load_fix_cycle_state(350, DEFAULT_FORGE_TARGET).count == 4


def test_override_grant_lets_the_next_dispatch_succeed(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    for _ in range(6):
        d._enforce_fix_ceiling(ws, _handover(ws, 400), _FIX_DECISION, ws.task_id)  # noqa: SLF001
    parked, parked_handover = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 400), _FIX_DECISION, ws.task_id
    )
    assert parked.kind == "await_human"
    question_id = parked_handover.open_questions[0].id
    _pr_state_paths.grant_fix_cycle_override(400, question_id, DEFAULT_FORGE_TARGET)
    resumed, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 400), _FIX_DECISION, ws.task_id
    )
    assert resumed.kind == "enqueue"
    assert resumed.team == "implementation_fix"


def test_tenth_cycle_parks_again_after_override(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    for _ in range(6):
        d._enforce_fix_ceiling(ws, _handover(ws, 500), _FIX_DECISION, ws.task_id)  # noqa: SLF001
    parked, parked_handover = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 500), _FIX_DECISION, ws.task_id
    )
    question_id = parked_handover.open_questions[0].id
    _pr_state_paths.grant_fix_cycle_override(500, question_id, DEFAULT_FORGE_TARGET)
    for _ in range(3):
        decision, _ = d._enforce_fix_ceiling(  # noqa: SLF001
            ws, _handover(ws, 500), _FIX_DECISION, ws.task_id
        )
        assert decision.kind == "enqueue"
    parked_again, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 500), _FIX_DECISION, ws.task_id
    )
    assert parked_again.kind == "await_human"


def test_a_different_pr_number_starts_at_zero(root: Path, queue: Queue) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = _ws(root)
    for _ in range(6):
        d._enforce_fix_ceiling(ws, _handover(ws, 600), _FIX_DECISION, ws.task_id)  # noqa: SLF001
    decision, _ = d._enforce_fix_ceiling(  # noqa: SLF001
        ws, _handover(ws, 601), _FIX_DECISION, ws.task_id
    )
    assert decision.kind == "enqueue"

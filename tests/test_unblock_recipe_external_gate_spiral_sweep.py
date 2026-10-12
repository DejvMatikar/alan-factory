from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.events import PubSub
from alan.loop_guard import SpiralKind
from alan.park import ParkReason, synthetic_blocker
from alan.queue import Job, Queue
from alan.teams import _pr_ready as pr_ready_mod
from alan.teams._spiral_handover import spiral_escalation_handover
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support

Dispatched = list[tuple[str, str | None, str | None]]
Env = tuple[Queue, WorkspaceIndex, Path]
LedgerRow = tuple[str, str, str | None, dict[str, object]]

_KIND = recipes_mod.EXTERNAL_GATE_SPIRAL_KIND
_PREFIX = recipes_mod.EXTERNAL_GATE_SPIRAL_PREFIX
_DETAIL = _PREFIX + "unchanged actionable gate checks across 3 verification passes: e2e"
_OTHER_KINDS = sorted(set(get_args(SpiralKind)) - {_KIND})
_SHAPE = "external_gate_spiral"


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return queue, WorkspaceIndex(root=ws_root, queue=queue), ws_root


def _spiral_park(root: Path, detail: str = _DETAIL, *, pr_number: int = 123) -> Workspace:
    ws = support.parked_ws(
        root,
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=synthetic_blocker(detail),
    )
    prior = support.handover(ws, from_team="verification", pr_number=pr_number)
    support.save(ws, spiral_escalation_handover(ws, prior, detail))
    return ws


def _run(
    env: Env,
    *,
    now: float,
    ready: pr_ready_mod.ReadinessResult | None = None,
    dispatched: Dispatched | None = None,
    deps: recipes_mod.Deps | None = None,
    enqueue: Callable[..., tuple[Job, str]] | None = None,
) -> int:
    queue, index, ws_root = env
    return sweep_mod.sweep(
        queue=queue,
        pubsub=PubSub(),
        workspace_index=index,
        workspaces_root=ws_root,
        now=now,
        lock_for=support.lock_for,
        enqueue_workflow=enqueue
        or support.tracking_enqueue(dispatched if dispatched is not None else []),
        read_readiness=lambda *_a, **_k: ready if ready is not None else support.readiness(),
        deps=deps if deps is not None else support.deps(),
        issue_client=support.FakeIssueClient(),
    )


def _rows(queue: Queue) -> list[LedgerRow]:
    return [
        (row["kind"], row["shape"], row["outcome"], json.loads(row["detail"] or "{}"))
        for row in queue.run(
            "SELECT kind, shape, outcome, detail FROM unblock_ledger"
            " WHERE kind IN (?, ?, ?) ORDER BY id",
            (ledger.KIND_ATTEMPT, ledger.KIND_WAIT, ledger.KIND_REPORT),
        )
    ]


def _of_kind(queue: Queue, kind: str) -> list[LedgerRow]:
    return [row for row in _rows(queue) if row[0] == kind]


def _job_count(queue: Queue) -> int:
    return int(queue.run("SELECT COUNT(*) AS n FROM jobs")[0]["n"])


def _status(ws: Workspace, root: Path) -> Status:
    return workspace_mod.read(ws.task_id, root=root).meta.status


def test_a_green_mergeable_spiral_park_enqueues_exactly_one_merge_step(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    dispatched: Dispatched = []
    assert _run(env, now=time.time(), dispatched=dispatched) == 1
    assert [(team, origin) for team, origin, _text in dispatched] == [("pr_merge", "generated")]
    assert _job_count(queue) == 1


def test_the_merge_step_carries_this_recipes_instructions(env: Env) -> None:
    _queue, _index, ws_root = env
    _spiral_park(ws_root)
    dispatched: Dispatched = []
    _run(env, now=time.time(), dispatched=dispatched)
    assert "external_gate_spiral" in str(dispatched[0][2])
    assert "green_not_merging" not in str(dispatched[0][2])


def test_the_action_is_ledgered_under_its_own_shape(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    _run(env, now=time.time())
    assert _rows(queue) == [
        (
            ledger.KIND_ATTEMPT,
            _SHAPE,
            ledger.OUTCOME_ACTED,
            {"reason": "the pull request is mergeable"},
        )
    ]


def test_the_action_replaces_the_no_recipe_recognises_report(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    _run(env, now=time.time())
    assert _of_kind(queue, ledger.KIND_REPORT) == []


def test_the_dispatch_action_names_the_merge_team_and_never_an_answer(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    _run(env, now=time.time())
    actions = queue.run("SELECT action FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ATTEMPT,))
    assert [row["action"] for row in actions] == ["dispatch:pr_merge"]


def test_the_issue_intent_carries_this_recipes_title(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    _run(env, now=time.time())
    titles = [
        json.loads(row["detail"])["title"]
        for row in queue.run(
            "SELECT detail FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ISSUE_INTENT,)
        )
    ]
    slug = DEFAULT_FORGE_TARGET.repo.slug
    assert titles == [f"Unblocker: an external-gate spiral park's gate cleared ({slug})"]


def test_a_red_gate_enqueues_nothing_and_leaves_the_row_parked(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _spiral_park(ws_root)
    ready = support.readiness(failing_checks=("e2e",))
    assert _run(env, now=time.time(), ready=ready, enqueue=support.never_enqueue) == 0
    assert _job_count(queue) == 0
    assert _status(ws, ws_root) is Status.ESCALATED
    assert _of_kind(queue, ledger.KIND_ATTEMPT) == []


def test_a_red_gate_is_reported_with_the_probes_reason_not_as_unrecognised(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    ready = support.readiness(failing_checks=("e2e",))
    _run(env, now=time.time(), ready=ready, enqueue=support.never_enqueue)
    reports = _of_kind(queue, ledger.KIND_REPORT)
    assert [row[3]["reason"] for row in reports] == ["external_gate_spiral: checks failing: e2e"]


@pytest.mark.parametrize(
    ("ready", "deps", "reason"),
    [
        pytest.param(
            support.readiness(failing_checks=("e2e",)),
            support.deps(),
            "checks failing: e2e",
            id="failing-checks",
        ),
        pytest.param(
            support.readiness(merge_state_status="DIRTY"),
            support.deps(),
            "merge conflict; the reconcile actor owns DIRTY branches",
            id="conflicted-with-base",
        ),
        pytest.param(
            support.readiness(merge_state_status="BLOCKED"),
            support.deps(),
            "merge state is BLOCKED",
            id="non-green-merge-state",
        ),
        pytest.param(
            support.readiness(merge_state_status=""),
            support.deps(),
            "merge state is unknown",
            id="unknown-merge-state",
        ),
        pytest.param(
            support.readiness(review_decision="CHANGES_REQUESTED"),
            support.deps(),
            "a review requests changes",
            id="changes-requested",
        ),
        pytest.param(
            support.readiness(),
            support.deps(feedback=support.snapshot(human_unresolved=1)),
            "an unresolved human review thread or comment blocks the merge",
            id="unresolved-human-thread",
        ),
        pytest.param(
            support.readiness(),
            support.deps(feedback=support.snapshot(human_items=1)),
            "an unresolved human review thread or comment blocks the merge",
            id="blocking-human-comment",
        ),
    ],
)
def test_each_declining_condition_reports_without_recording_an_attempt(
    env: Env, ready: pr_ready_mod.ReadinessResult, deps: recipes_mod.Deps, reason: str
) -> None:
    queue, _index, ws_root = env
    ws = _spiral_park(ws_root)
    assert _run(env, now=time.time(), ready=ready, deps=deps, enqueue=support.never_enqueue) == 0
    assert [row[3]["reason"] for row in _of_kind(queue, ledger.KIND_REPORT)] == [
        f"external_gate_spiral: {reason}"
    ]
    assert _of_kind(queue, ledger.KIND_ATTEMPT) == []
    assert _job_count(queue) == 0
    assert _status(ws, ws_root) is Status.ESCALATED


@pytest.mark.parametrize(
    ("ready", "deps", "reason"),
    [
        pytest.param(
            support.readiness(pending_checks=("e2e",)),
            support.deps(),
            "checks pending: e2e",
            id="pending-checks",
        ),
        pytest.param(
            pr_ready_mod.ReadinessUnavailable("forge unavailable"),
            support.deps(),
            "forge unavailable",
            id="forge-unavailable",
        ),
        pytest.param(
            support.readiness(check_count=0, passing_checks=()),
            support.deps(),
            "no check results at the head yet",
            id="result-less-forge",
        ),
        pytest.param(
            support.readiness(),
            support.deps(feedback=support.snapshot(ok=False, failure_reason="rate limited")),
            "feedback unavailable: rate limited",
            id="feedback-unavailable",
        ),
    ],
)
def test_each_waiting_condition_records_a_wait_without_an_attempt(
    env: Env, ready: pr_ready_mod.ReadinessResult, deps: recipes_mod.Deps, reason: str
) -> None:
    queue, _index, ws_root = env
    ws = _spiral_park(ws_root)
    now = time.time()
    assert _run(env, now=now, ready=ready, deps=deps, enqueue=support.never_enqueue) == 0
    assert _rows(queue) == [
        (ledger.KIND_WAIT, _SHAPE, ledger.OUTCOME_NOT_YET, {"reason": f"{_SHAPE}: {reason}"})
    ]
    assert _job_count(queue) == 0
    assert _status(ws, ws_root) is Status.ESCALATED


def test_a_gate_that_stays_red_never_records_an_attempt(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    ready = support.readiness(failing_checks=("e2e",))
    now = time.time()
    for tick in range(4):
        elapsed = tick * (sweep_mod.REPROBE_INTERVAL_S + 1)
        _run(env, now=now + elapsed, ready=ready, enqueue=support.never_enqueue)
    assert _of_kind(queue, ledger.KIND_ATTEMPT) == []
    assert len(_of_kind(queue, ledger.KIND_REPORT)) == 1


def test_a_head_that_moves_while_checks_are_pending_acts_once_the_new_head_is_green(
    env: Env,
) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    dispatched: Dispatched = []
    now = time.time()
    pending = support.readiness(head_sha="one", pending_checks=("e2e",))
    assert _run(env, now=now, ready=pending, enqueue=support.never_enqueue) == 0
    green = support.readiness(head_sha="two")
    later = now + sweep_mod.BASE_INTERVAL_S + 1
    assert _run(env, now=later, ready=green, dispatched=dispatched) == 1
    assert [team for team, _origin, _text in dispatched] == ["pr_merge"]
    assert [row[:3] for row in _of_kind(queue, ledger.KIND_ATTEMPT)] == [
        (ledger.KIND_ATTEMPT, _SHAPE, ledger.OUTCOME_ACTED)
    ]


def test_a_head_that_moves_after_a_report_acts_on_the_next_reprobe(env: Env) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root)
    dispatched: Dispatched = []
    now = time.time()
    red = support.readiness(head_sha="one", failing_checks=("e2e",))
    assert _run(env, now=now, ready=red, enqueue=support.never_enqueue) == 0
    green = support.readiness(head_sha="two")
    later = now + sweep_mod.REPROBE_INTERVAL_S + 1
    assert _run(env, now=later, ready=green, dispatched=dispatched) == 1
    assert [team for team, _origin, _text in dispatched] == ["pr_merge"]
    assert len(_of_kind(queue, ledger.KIND_ATTEMPT)) == 1


@pytest.mark.parametrize("kind", _OTHER_KINDS)
def test_a_park_of_another_spiral_kind_is_not_acted_on_by_this_recipe(
    env: Env, kind: str
) -> None:
    queue, _index, ws_root = env
    _spiral_park(ws_root, f"spiral:{kind}: {_DETAIL}")
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    assert _of_kind(queue, ledger.KIND_ATTEMPT) == []
    assert [row[3]["reason"] for row in _of_kind(queue, ledger.KIND_REPORT)] == [
        "no recipe recognises this park"
    ]


def test_a_spiral_park_without_a_pull_request_is_left_alone(env: Env) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(
        ws_root,
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=synthetic_blocker(_DETAIL),
    )
    prior = support.handover(ws, from_team="verification", pr_number=None)
    support.save(ws, spiral_escalation_handover(ws, prior, _DETAIL))
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    assert _of_kind(queue, ledger.KIND_ATTEMPT) == []
    assert _job_count(queue) == 0

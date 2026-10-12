from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import handover as handover_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.events import PubSub
from alan.park import Blocker
from alan.queue import Job, Queue
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.workspace import Workspace
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support

Dispatched = list[tuple[str, str | None, str | None]]
Env = tuple[Queue, WorkspaceIndex, Path]
LedgerRow = tuple[str, str, str | None, dict[str, object]]

_QUESTION_ID = "declared_blocker_abcdef12"
_TEXT = "backend must be green before this can merge"
_GREEN = support.readiness(head_sha="one", passing_checks=("backend", "frontend"))
_NEW_SHAPE = "green_named_check_blocker"
_NEW_TITLE = "a declared blocker's named checks are green"


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture
def env(tmp_path: Path) -> Env:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return queue, WorkspaceIndex(root=ws_root, queue=queue), ws_root


def _declared_park(root: Path, text: str = _TEXT) -> Workspace:
    ws = support.parked_ws(
        root, blockers=[Blocker(kind="reason", name=_QUESTION_ID, detail=text)]
    )
    question = support.question(
        question_id=_QUESTION_ID,
        question_class="fix_escalation",
        text=text,
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        resolvers={
            declared_blocker_mod.HAND_RESOLVE_OPTION: "implementation_fix",
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "verification",
        },
    )
    support.save(ws, support.handover(ws, from_team="implementation_fix", questions=[question]))
    return ws


def _run(
    env: Env,
    *,
    now: float,
    ready: pr_ready_mod.PrReadiness = _GREEN,
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
        read_readiness=lambda *_a, **_k: ready,
        deps=deps if deps is not None else support.deps(),
        issue_client=support.FakeIssueClient(),
    )


def _rows(queue: Queue, *kinds: str) -> list[LedgerRow]:
    return [
        (row["kind"], row["shape"], row["outcome"], json.loads(row["detail"] or "{}"))
        for row in queue.run("SELECT kind, shape, outcome, detail FROM unblock_ledger ORDER BY id")
        if row["kind"] in kinds
    ]


def _titles(queue: Queue) -> list[str]:
    return [str(row[3]["title"]) for row in _rows(queue, ledger.KIND_ISSUE_INTENT)]


def _job_count(queue: Queue) -> int:
    return int(queue.run("SELECT COUNT(*) AS n FROM jobs")[0]["n"])


def _verifier_rejections(count: int, head: str) -> str:
    return "\n".join(support.review_line(commit_id=head, review_id=index) for index in range(count))


def _needs_human_title() -> str:
    slug = DEFAULT_FORGE_TARGET.repo.slug
    return f"Unblocker: {sweep_mod.NEEDS_HUMAN_TITLE} ({slug})"


def test_a_green_declared_blocker_park_dispatches_verification_once(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    dispatched: Dispatched = []
    assert _run(env, now=time.time(), dispatched=dispatched) == 1
    assert [(team, origin) for team, origin, _text in dispatched] == [
        ("verification", "generated")
    ]
    assert _job_count(queue) == 1
    assert "green_named_check_blocker" in str(dispatched[0][2])


def test_the_green_park_records_the_blocker_does_not_hold_answer(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _declared_park(ws_root)
    _run(env, now=time.time())
    latest = handover_mod.list_handovers(workspace_mod.read(ws.task_id, root=ws_root))[-1]
    assert latest.handover_type == "revisit_amend"
    assert latest.open_questions[0].answer == declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION
    assert [row[:3] for row in _rows(queue, ledger.KIND_ATTEMPT)] == [
        (ledger.KIND_ATTEMPT, _NEW_SHAPE, ledger.OUTCOME_ACTED)
    ]


def test_exactly_one_recipe_acts_in_a_pass(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    _run(env, now=time.time())
    assert len(_rows(queue, ledger.KIND_ATTEMPT)) == 1
    assert _job_count(queue) == 1


def test_a_head_that_moved_past_the_park_acts_on_the_hand_resolved_recipe(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    now = time.time()
    pending = support.readiness(
        head_sha="one", passing_checks=("frontend",), pending_checks=("backend",)
    )
    assert _run(env, now=now, ready=pending, enqueue=support.never_enqueue) == 0
    moved = support.readiness(head_sha="two", passing_checks=("backend", "frontend"))
    dispatched: Dispatched = []
    later = now + sweep_mod.BASE_INTERVAL_S + 1
    assert _run(env, now=later, ready=moved, dispatched=dispatched) == 1
    assert [team for team, _origin, _text in dispatched] == ["implementation_fix"]
    assert [row[1] for row in _rows(queue, ledger.KIND_ATTEMPT)] == [
        "hand_resolved_declared_blocker"
    ]


def test_a_named_check_still_pending_records_a_wait_and_suppresses_the_decline_report(
    env: Env,
) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    pending = support.readiness(
        head_sha="one", passing_checks=("frontend",), pending_checks=("backend",)
    )
    assert _run(env, now=time.time(), ready=pending, enqueue=support.never_enqueue) == 0
    assert _rows(queue, ledger.KIND_ATTEMPT, ledger.KIND_WAIT, ledger.KIND_REPORT) == [
        (
            ledger.KIND_WAIT,
            "declared_blocker",
            ledger.OUTCOME_NOT_YET,
            {"reason": f"{_NEW_SHAPE}: a check this blocker names is pending: backend"},
        )
    ]
    assert _job_count(queue) == 0


def test_a_named_red_check_is_reported_under_the_needs_human_shape_with_no_new_title(
    env: Env,
) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    red = support.readiness(
        head_sha="one", passing_checks=("frontend",), failing_checks=("backend",)
    )
    assert _run(env, now=time.time(), ready=red, enqueue=support.never_enqueue) == 0
    [report] = _rows(queue, ledger.KIND_REPORT)
    assert report[1] == sweep_mod.NEEDS_HUMAN_SHAPE
    assert f"{_NEW_SHAPE}: a check this blocker names is red: backend" in str(report[3]["reason"])
    assert _titles(queue) == [_needs_human_title()]
    assert _rows(queue, ledger.KIND_ATTEMPT) == []


def test_a_blocker_naming_no_check_is_reported_with_the_decline_reason(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root, "the docs check must be green")
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    [report] = _rows(queue, ledger.KIND_REPORT)
    assert (
        f"{_NEW_SHAPE}: the declared blocker names no check on this pull request"
        in str(report[3]["reason"])
    )
    assert _titles(queue) == [_needs_human_title()]


def test_a_head_the_verifier_rejected_twice_is_reported_and_never_answered(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    deps = support.deps(review_stream=_verifier_rejections(2, "one"))
    assert _run(env, now=time.time(), deps=deps, enqueue=support.never_enqueue) == 0
    [report] = _rows(queue, ledger.KIND_REPORT)
    assert (
        f"{_NEW_SHAPE}: the verifier already rejected this head twice; a third pass adds nothing"
        in str(report[3]["reason"])
    )
    assert _rows(queue, ledger.KIND_ATTEMPT) == []
    assert _job_count(queue) == 0


def test_a_head_the_verifier_rejected_once_is_still_answered(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    deps = support.deps(review_stream=_verifier_rejections(1, "one"))
    assert _run(env, now=time.time(), deps=deps) == 1
    assert [row[1] for row in _rows(queue, ledger.KIND_ATTEMPT)] == [_NEW_SHAPE]


def test_the_acting_recipe_files_its_own_title_like_every_other_recipe(env: Env) -> None:
    queue, _index, ws_root = env
    _declared_park(ws_root)
    _run(env, now=time.time())
    slug = DEFAULT_FORGE_TARGET.repo.slug
    assert _titles(queue) == [f"Unblocker: {_NEW_TITLE} ({slug})"]

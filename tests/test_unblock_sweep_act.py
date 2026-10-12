from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import handover as handover_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.events import PubSub
from alan.park import Blocker, ParkReason
from alan.queue import Job, Queue
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _fix_escalation as fix_escalation_mod
from alan.teams import _forge_errors as forge_errors_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.workspace import Status, Task, TaskStatus, Workspace
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support

Dispatched = list[tuple[str, str | None, str | None]]


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)


@pytest.fixture
def env(tmp_path: Path) -> tuple[Queue, WorkspaceIndex, Path]:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return queue, WorkspaceIndex(root=ws_root, queue=queue), ws_root


@pytest.fixture(autouse=True)
def _dispatchable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


def _run(
    env: tuple[Queue, WorkspaceIndex, Path],
    *,
    now: float,
    dispatched: Dispatched | None = None,
    ready: pr_ready_mod.PrReadiness | None = None,
    deps: recipes_mod.Deps | None = None,
    lock_for: Callable[[str], object] | None = None,
    enqueue: Callable[..., tuple[Job, str]] | None = None,
    client: support.FakeIssueClient | None = None,
) -> int:
    queue, index, ws_root = env
    return sweep_mod.sweep(
        queue=queue,
        pubsub=PubSub(),
        workspace_index=index,
        workspaces_root=ws_root,
        now=now,
        lock_for=lock_for or support.lock_for,  # type: ignore[arg-type]
        enqueue_workflow=enqueue
        or support.tracking_enqueue(dispatched if dispatched is not None else []),
        read_readiness=lambda *_a, **_k: ready if ready is not None else support.readiness(),
        deps=deps if deps is not None else support.deps(),
        issue_client=client if client is not None else support.FakeIssueClient(),
    )


def _rows(queue: Queue) -> list[tuple[str, str, str | None, dict[str, object]]]:
    return [
        (r.kind, r.shape, r.outcome, r.detail)
        for r in (
            ledger._row_from_sql(row)
            for row in queue.run(  # noqa: SLF001
                "SELECT * FROM unblock_ledger WHERE kind IN (?, ?, ?) ORDER BY id",
                (ledger.KIND_ATTEMPT, ledger.KIND_WAIT, ledger.KIND_REPORT),
            )
        )
    ]


def _intents(queue: Queue) -> list[str]:
    return [
        str(ledger._row_from_sql(row).detail["title"])  # noqa: SLF001
        for row in queue.run(
            "SELECT * FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ISSUE_INTENT,)
        )
    ]


def _watch(queue: Queue) -> dict[str, sweep_mod.WatchEntry]:
    return sweep_mod.load_watch(queue)


def _crash(root: Path, *, team: str = "verification", title: str = "crashed") -> Workspace:
    return support.parked_ws(
        root,
        title=title,
        park_reason=ParkReason.CRASH,
        team=team,
        blockers=[Blocker(kind="reason", name="reason", detail="restart interrupted")],
    )


def _stale(root: Path) -> Workspace:
    ws = support.parked_ws(root)
    support.save(ws, support.handover(ws, questions=[support.question()]))
    return ws


def _stale_deps(
    *, dismiss: bool = True, feedback_raises: BaseException | None = None
) -> recipes_mod.Deps:
    return support.deps(
        review_stream=support.review_line(commit_id="old"),
        dismiss=dismiss,
        feedback_raises=feedback_raises,
    )


def test_crash_park_is_dispatched_fresh_and_ledgered(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)
    dispatched: Dispatched = []
    now = time.time()
    assert _run(env, now=now, dispatched=dispatched) == 1
    assert dispatched[0][:2] == ("verification", "generated")
    assert "crash_park" in str(dispatched[0][2])
    assert _rows(queue) == [
        (
            ledger.KIND_ATTEMPT,
            "crash_park",
            ledger.OUTCOME_ACTED,
            {"reason": "fresh dispatch of verification"},
        )
    ]
    slug = DEFAULT_FORGE_TARGET.repo.slug
    assert _intents(queue) == [f"Unblocker: a restart-interrupted run was parked ({slug})"]
    entry = _watch(queue)[ws.task_id]
    assert (entry.acted, entry.attempts, entry.next_at) == (
        True,
        1,
        now + sweep_mod.OBSERVE_DELAY_S,
    )
    assert workspace_mod.read(ws.task_id, root=ws_root).meta.status is Status.ACTIVE


def test_a_workspace_that_moved_leaves_the_watch(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)
    now = time.time()
    _run(env, now=now)
    assert _run(env, now=now + sweep_mod.OBSERVE_DELAY_S + 1, enqueue=support.never_enqueue) == 0
    assert ws.task_id not in _watch(queue)


def test_stale_review_dismisses_then_dispatches_pr_merge(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _stale(ws_root)
    dispatched: Dispatched = []
    calls: list[tuple[int, str, ForgeTarget]] = []
    deps = support.deps(review_stream=support.review_line(commit_id="old"), dismiss_calls=calls)
    assert (
        _run(
            env,
            now=time.time(),
            dispatched=dispatched,
            ready=support.readiness(review_decision="CHANGES_REQUESTED"),
            deps=deps,
        )
        == 1
    )
    assert [d[0] for d in dispatched] == ["pr_merge"]
    assert calls[0][0] == 123
    kind, shape, outcome, detail = _rows(queue)[0]
    assert (kind, shape, outcome) == (ledger.KIND_ATTEMPT, "stale_review", ledger.OUTCOME_ACTED)
    assert (
        queue.run("SELECT action FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ATTEMPT,))[0][
            "action"
        ]
        == "dispatch:pr_merge"
    )


def test_a_failed_forge_write_is_retried_once_then_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _stale(ws_root)
    ready = support.readiness(review_decision="CHANGES_REQUESTED")
    now = time.time()
    assert (
        _run(
            env,
            now=now,
            ready=ready,
            deps=_stale_deps(dismiss=False),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert (
        _run(
            env,
            now=now + 1,
            ready=ready,
            deps=_stale_deps(dismiss=False),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert (
        _run(
            env,
            now=now + 121,
            ready=ready,
            deps=_stale_deps(dismiss=False),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert (
        _run(
            env,
            now=now + 242,
            ready=ready,
            deps=_stale_deps(dismiss=False),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert (
        _run(
            env,
            now=now + 363,
            ready=ready,
            deps=_stale_deps(dismiss=False),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    rows = _rows(queue)
    assert [(k, o) for k, _s, o, _d in rows] == [
        (ledger.KIND_ATTEMPT, "forge_write_failed"),
        (ledger.KIND_ATTEMPT, "forge_write_failed"),
        (ledger.KIND_REPORT, ledger.OUTCOME_NEEDS_HUMAN),
    ]
    assert rows[-1][3]["authority_holder"] == sweep_mod.HOLDER_NOBODY
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts) == (True, 2)
    assert workspace_mod.read(ws.task_id, root=ws_root).meta.status is Status.AWAITING_INPUT


def test_a_pending_probe_waits_with_a_doubling_backoff(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _stale(ws_root)
    ready = support.readiness(review_decision="CHANGES_REQUESTED", pending_checks=("e2e",))
    now = time.time()
    _run(env, now=now, ready=ready, enqueue=support.never_enqueue)
    first = _watch(queue)[ws.task_id]
    assert (first.attempts, first.next_at) == (1, now + 300.0)
    _run(env, now=now + 100, ready=ready, enqueue=support.never_enqueue)
    assert _watch(queue)[ws.task_id] == first
    _run(env, now=now + 301, ready=ready, enqueue=support.never_enqueue)
    second = _watch(queue)[ws.task_id]
    assert (second.attempts, second.next_at) == (2, now + 301 + 600.0)
    waits = [r for r in _rows(queue) if r[0] == ledger.KIND_WAIT]
    assert len(waits) == 1
    assert waits[0][1] == "stale_review"
    assert "checks pending: e2e" in str(waits[0][3]["reason"])


def test_an_unrecognised_park_is_reported_once_per_identity(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(ws_root, blockers=[Blocker(kind="reason", name="verif-x", detail="d")])
    support.save(
        ws,
        support.handover(
            ws,
            from_team="verification",
            questions=[
                support.question(question_id="verif-x", question_class="verification_escalation")
            ],
        ),
    )
    now = time.time()
    _run(env, now=now, enqueue=support.never_enqueue)
    _run(env, now=now + 1000, enqueue=support.never_enqueue)
    reports = [r for r in _rows(queue) if r[0] == ledger.KIND_REPORT]
    assert len(reports) == 1
    assert reports[0][3] == {
        "reason": "no recipe recognises this park",
        "authority_holder": sweep_mod.HOLDER_HUMAN,
    }
    slug = DEFAULT_FORGE_TARGET.repo.slug
    assert _intents(queue) == [f"Unblocker: the unblocker could not clear a stall ({slug})"]
    assert _watch(queue)[ws.task_id].terminal is True
    support.save(
        ws,
        support.handover(
            ws,
            from_team="verification",
            handover_id=f"handover_{ws.task_id}_two",
            questions=[
                support.question(question_id="verif-x", question_class="verification_escalation")
            ],
        ),
    )
    _run(env, now=now + 2000, enqueue=support.never_enqueue)
    assert len([r for r in _rows(queue) if r[0] == ledger.KIND_REPORT]) == 2


def test_a_running_job_is_never_touched(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)
    queue.enqueue(ws.task_id, "verification", workspace_id=ws.task_id)
    queue.claim()
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    assert _rows(queue) == []


def _seed_attempts(queue: Queue, ws: Workspace, count: int, *, fingerprint: str = "fp") -> None:
    ledger.ensure_schema(queue)
    for _ in range(count):
        ledger.record(
            queue,
            ledger.Entry(
                workspace_key=ws.task_id,
                scope_ref=f"ws:{ws.task_id}",
                shape="crash_park",
                fingerprint=fingerprint,
                kind=ledger.KIND_ATTEMPT,
            ),
        )


def test_the_workspace_ceiling_reports_instead_of_acting(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)
    _seed_attempts(queue, ws, ledger.ATTEMPT_CEILING_PER_WORKSPACE)
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    report = _rows(queue)[-1]
    assert report[0] == ledger.KIND_REPORT
    assert report[3]["reason"] == "per-workspace attempt ceiling reached"


def test_the_fingerprint_ceiling_declines_the_recipe(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)
    row = next(r for r in env[1].rows() if r.task_id == ws.task_id)
    cand = sweep_mod.candidate_for(row, workspaces_root=ws_root)
    assert cand is not None
    recipe = next(r for r in recipes_mod.RECIPES if r.name == "crash_park")
    _seed_attempts(
        queue,
        ws,
        ledger.ATTEMPT_CEILING_PER_FINGERPRINT,
        fingerprint=sweep_mod._fingerprint(recipe, cand),
    )  # noqa: SLF001
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    report = _rows(queue)[-1]
    assert "per-fingerprint ceiling" in str(report[3]["reason"])


def test_the_fingerprint_ceiling_declines_a_crash_before_the_first_hop(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(
        ws_root,
        park_reason=ParkReason.CRASH,
        team=None,
        pending_first_hop="implementation_plan",
        blockers=[Blocker(kind="reason", name="reason", detail="restart interrupted")],
    )
    support.save(ws, support.handover(ws, from_team="brief_refine", pr_number=None))
    row = next(r for r in env[1].rows() if r.task_id == ws.task_id)
    cand = sweep_mod.candidate_for(row, workspaces_root=ws_root)
    assert cand is not None
    recipe = next(r for r in recipes_mod.RECIPES if r.name == "crash_park")
    _seed_attempts(
        queue,
        ws,
        ledger.ATTEMPT_CEILING_PER_FINGERPRINT,
        fingerprint=sweep_mod._fingerprint(recipe, cand),
    )  # noqa: SLF001
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    report = _rows(queue)[-1]
    assert "per-fingerprint ceiling" in str(report[3]["reason"])


def test_a_forge_outage_inside_a_probe_waits(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    _stale(ws_root)
    deps = _stale_deps(feedback_raises=forge_errors_mod.ForgeTransientError("down"))
    _run(
        env,
        now=time.time(),
        ready=support.readiness(review_decision="CHANGES_REQUESTED"),
        deps=deps,
        enqueue=support.never_enqueue,
    )
    wait = _rows(queue)[0]
    assert wait[0] == ledger.KIND_WAIT
    assert "forge unavailable" in str(wait[3]["reason"])


def test_a_decomposed_workspace_without_an_owner_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = workspace_mod.create(
        "split",
        "d",
        root=ws_root,
        team="verification",
        tasks=[Task(id="a", status=TaskStatus.PENDING)],
    )
    workspace_mod.transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=ParkReason.CRASH,
        blockers=[Blocker(kind="reason", name="reason", detail="r")],
    )
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    report = _rows(queue)[-1]
    assert report[3] == {
        "reason": "decomposed workspace without a unique owning subtask",
        "authority_holder": sweep_mod.HOLDER_HUMAN,
    }


class _MutatingLock:
    def __init__(self, action: Callable[[], None]) -> None:
        self._action = action

    def __enter__(self) -> "_MutatingLock":
        self._action()
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_a_workspace_that_moved_under_the_lock_is_skipped(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)

    def _touch() -> None:
        support.save(ws, support.handover(ws, from_team="verification", pr_number=None))

    assert (
        _run(
            env,
            now=time.time(),
            lock_for=lambda _id: _MutatingLock(_touch),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert _rows(queue)[0][2] == "skipped_moved"


def test_a_job_claimed_under_the_lock_is_skipped(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)

    def _claim() -> None:
        queue.enqueue(ws.task_id, "verification", workspace_id=ws.task_id)
        queue.claim()

    assert (
        _run(
            env,
            now=time.time(),
            lock_for=lambda _id: _MutatingLock(_claim),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert _rows(queue)[0][2] == "skipped_running"


def test_a_failed_dispatch_restores_the_park_and_propagates(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _crash(ws_root)

    def _boom(**_kwargs: object) -> tuple[Job, str]:
        raise RuntimeError("enqueue failed")

    with pytest.raises(RuntimeError, match="enqueue failed"):
        _run(env, now=time.time(), enqueue=_boom)
    restored = workspace_mod.read(ws.task_id, root=ws_root)
    assert restored.meta.status is Status.AWAITING_INPUT
    assert restored.meta.park_reason is ParkReason.CRASH
    assert restored.meta.blockers[0].detail == "restart interrupted"
    assert _rows(queue)[0][:3] == (ledger.KIND_ATTEMPT, "crash_park", "failed: RuntimeError")
    assert len(_intents(queue)) == 1
    assert _watch(queue)[ws.task_id].acted is True


def test_an_answer_plan_records_the_answer_then_dispatches(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(
        ws_root, blockers=[Blocker(kind="reason", name="declared_blocker_inv_005", detail="d")]
    )
    q = support.question(
        question_id="declared_blocker_inv_005",
        question_class="fix_escalation",
        text="tests fail on base fed07dd5, independent of this PR",
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        resolvers={declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "verification"},
    )
    support.save(ws, support.handover(ws, from_team="implementation_fix", questions=[q]))
    dispatched: Dispatched = []
    assert (
        _run(env, now=time.time(), dispatched=dispatched, deps=support.deps(ci=support.main_ci()))
        == 1
    )
    assert dispatched[0][0] == "verification"
    latest = handover_mod.list_handovers(workspace_mod.read(ws.task_id, root=ws_root))[-1]
    assert latest.handover_type == "revisit_amend"
    assert latest.open_questions[0].answer == declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION
    action = queue.run("SELECT action FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ATTEMPT,))[
        0
    ]["action"]
    assert str(action).startswith("answer:verification · The blocker does not hold")


def test_the_per_tick_cap_bounds_forge_spend(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    for i in range(sweep_mod.MAX_FORGE_CANDIDATES_PER_TICK + 1):
        _crash(ws_root, title=f"crash-{i}")
    now = time.time()
    assert _run(env, now=now) == sweep_mod.MAX_FORGE_CANDIDATES_PER_TICK
    assert len(_watch(queue)) == sweep_mod.MAX_FORGE_CANDIDATES_PER_TICK
    assert _run(env, now=now + 1) == 1


def test_a_missing_fleet_grant_names_its_holder(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _index, ws_root = env
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: False)
    _crash(ws_root)
    _run(env, now=time.time(), enqueue=support.never_enqueue)
    assert _rows(queue)[-1][3]["authority_holder"] == recipes_mod.FLEET_GRANT_MISSING


def test_observe_helpers_classify_entries() -> None:
    idle = sweep_mod.WatchEntry(next_at=0.0, attempts=1, identity="i")
    acted = sweep_mod.WatchEntry(next_at=0.0, attempts=1, identity="i", acted=True)
    twice = sweep_mod.WatchEntry(next_at=0.0, attempts=2, identity="i", acted=True, retried=True)
    assert sweep_mod._observed_noop_twice(None) is False  # noqa: SLF001
    assert sweep_mod._observed_noop_twice(idle) is False  # noqa: SLF001
    assert sweep_mod._observed_noop_twice(twice) is True  # noqa: SLF001
    assert sweep_mod._retry_after_observe(None) is None  # noqa: SLF001
    assert sweep_mod._retry_after_observe(idle) is idle  # noqa: SLF001
    assert sweep_mod._retry_after_observe(acted) == sweep_mod.WatchEntry(  # noqa: SLF001
        next_at=0.0, attempts=1, identity="i", acted=False, retried=True
    )


def test_issue_intents_are_drained_in_the_same_tick(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, _root = env
    _crash(env[2])
    client = support.FakeIssueClient(number=11)
    _run(env, now=time.time(), client=client)
    assert client.ensured[0][0].startswith("Unblocker: a restart-interrupted run was parked")
    assert client.comments[0][0] == 11
    assert "shape: `crash_park`" in client.comments[0][1]
    assert ledger.pending_issue_intents(queue, 5) == []


def test_an_exempt_stuck_row_is_dropped_from_the_watch(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(ws_root, team="research")
    sweep_mod._save_watch(  # noqa: SLF001
        queue, {ws.task_id: sweep_mod.WatchEntry(next_at=0.0, attempts=1, identity="stale")}
    )
    assert _run(env, now=time.time(), enqueue=support.never_enqueue) == 0
    assert _watch(queue) == {}
    assert _rows(queue) == []


def _declared(ws_root: Path, **question_overrides: object) -> Workspace:
    ws = support.parked_ws(
        ws_root, blockers=[Blocker(kind="reason", name="declared_blocker_inv_005", detail="d")]
    )
    fields: dict[str, object] = {
        "question_id": "declared_blocker_inv_005",
        "question_class": "fix_escalation",
        "text": "tests fail on base fed07dd5, independent of this PR",
        "options": (
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
        "resolvers": {declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "verification"},
    }
    fields.update(question_overrides)
    q = support.question(**fields)  # type: ignore[arg-type]
    support.save(ws, support.handover(ws, from_team="implementation_fix", questions=[q]))
    return ws


def _last_report_reason(queue: Queue) -> str:
    report = [r for r in _rows(queue) if r[0] == ledger.KIND_REPORT][-1]
    return str(report[3]["reason"])


def test_an_answer_outside_the_offered_options_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _declared(ws_root, options=("option one", "option two"), resolvers={})
    assert (
        _run(
            env,
            now=time.time(),
            deps=support.deps(ci=support.main_ci()),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert "not one of the parked question's options" in _last_report_reason(queue)


def test_a_walled_answer_is_reported(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    _declared(ws_root, effects={declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "pending_external"})
    assert (
        _run(
            env,
            now=time.time(),
            deps=support.deps(ci=support.main_ci()),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert "waits on an out-of-fleet step" in _last_report_reason(queue)


def test_an_answer_resolving_elsewhere_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _declared(ws_root, resolvers={declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION: "pr_merge"})
    assert (
        _run(
            env,
            now=time.time(),
            deps=support.deps(ci=support.main_ci()),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert _last_report_reason(queue) == "the answer resolves to pr_merge, not verification"


def test_an_answer_without_a_resolver_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _declared(ws_root, question_class=None, resolvers={})
    assert (
        _run(
            env,
            now=time.time(),
            deps=support.deps(ci=support.main_ci()),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert (
        _last_report_reason(queue) == "the parked question has no resolver for the recipe's answer"
    )


def test_a_plan_team_the_posture_rejects_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _index, ws_root = env
    _stale(ws_root)
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, team: team != "pr_merge")
    assert (
        _run(
            env,
            now=time.time(),
            ready=support.readiness(review_decision="CHANGES_REQUESTED"),
            deps=_stale_deps(),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    report = [r for r in _rows(queue) if r[0] == ledger.KIND_REPORT][-1]
    assert report[3]["authority_holder"] == recipes_mod.FLEET_GRANT_MISSING
    assert "pr_merge is not admitted" in str(report[3]["reason"])


def test_a_raising_forge_write_is_ledgered_before_propagating(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _stale(ws_root)

    def _boom(_pr: int, _task_id: str, _forge: object) -> bool:
        raise RuntimeError("forge exploded")

    deps = recipes_mod.Deps(
        review_stream=lambda _pr, _forge: support.review_line(commit_id="old"),
        feedback=lambda _pr, _forge: support.snapshot(),
        main_ci=lambda _cfg: None,
        dismiss_stale_reviews=_boom,
    )
    with pytest.raises(RuntimeError, match="forge exploded"):
        _run(
            env,
            now=time.time(),
            ready=support.readiness(review_decision="CHANGES_REQUESTED"),
            deps=deps,
            enqueue=support.never_enqueue,
        )
    rows = _rows(queue)
    assert rows[-1][:3] == (ledger.KIND_ATTEMPT, "stale_review", "failed: RuntimeError")
    assert len(_intents(queue)) == 1


def test_an_intent_carries_and_restores_its_forge_target(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _crash(ws_root)
    client = support.FakeIssueClient(number=3)
    _run(env, now=time.time(), client=client)
    intent = ledger._row_from_sql(  # noqa: SLF001
        queue.run("SELECT * FROM unblock_ledger WHERE kind = ?", (ledger.KIND_ISSUE_INTENT,))[0]
    )
    default = DEFAULT_FORGE_TARGET
    assert (intent.detail["owner"], intent.detail["repo"]) == (
        default.repo.owner,
        default.repo.repo,
    )
    assert intent.detail["credential_env_name"] == default.credential_env_name
    assert client.ensured[0][2] == default.repo.slug


def test_a_stored_forge_target_wins_over_the_engine_registry(queue: Queue) -> None:
    ledger.ensure_schema(queue)
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key="ws-1",
            scope_ref="ws:ws-1",
            shape="crash_park",
            fingerprint="fp",
            kind=ledger.KIND_ISSUE_INTENT,
            detail={
                "slug": "acme-corp/widget-app",
                "title": "T",
                "issue_body": "B",
                "comment": "C",
                "owner": "acme-corp",
                "repo": "widget-app",
                "credential_env_name": "ACME_WIDGET_GH_TOKEN",
            },
        ),
    )
    client = support.FakeIssueClient(number=4)
    sweep_mod.drain_issue_intents(queue, client)
    assert client.ensured[0][2] == "acme-corp/widget-app"


def test_an_answered_question_whose_dispatch_never_ran_is_resumed(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _declared(ws_root)
    latest = handover_mod.list_handovers(ws)[-1]
    handover_mod.record_answer(
        ws,
        latest,
        "declared_blocker_inv_005",
        declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        from_team="implementation_fix",
    )
    dispatched: Dispatched = []
    assert _run(env, now=time.time(), dispatched=dispatched) == 1
    assert dispatched[0][0] == "verification"
    assert "answered_not_dispatched" in str(dispatched[0][2])
    assert _rows(queue)[0][:3] == (
        ledger.KIND_ATTEMPT,
        "answered_not_dispatched",
        ledger.OUTCOME_ACTED,
    )


def _failed_check_park(ws_root: Path) -> Workspace:
    ws = support.parked_ws(
        ws_root, blockers=[Blocker(kind="reason", name="pr-merge-failed", detail="d")]
    )
    q = support.question(
        question_id="pr-merge-failed",
        text="PR #123 failed checks: Frontend E2E (Playwright, blocking).",
        options=("fix it", recipes_mod.TRANSIENT_RETRY_OPTION),
    )
    support.save(ws, support.handover(ws, questions=[q]))
    return ws


def test_a_reported_park_is_reprobed_and_acted_on_when_the_forge_turns_green(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    red = support.readiness(failing_checks=("Frontend E2E (Playwright, blocking)",))
    now = time.time()
    _run(env, now=now, ready=red, enqueue=support.never_enqueue)
    reported = _watch(queue)[ws.task_id]
    assert (reported.terminal, reported.next_at) == (True, now + sweep_mod.REPROBE_INTERVAL_S)
    _run(
        env,
        now=now + sweep_mod.REPROBE_INTERVAL_S - 1,
        ready=support.readiness(),
        enqueue=support.never_enqueue,
    )
    dispatched: Dispatched = []
    assert (
        _run(
            env,
            now=now + sweep_mod.REPROBE_INTERVAL_S + 1,
            ready=support.readiness(),
            dispatched=dispatched,
        )
        == 1
    )
    assert dispatched[0][0] == "pr_merge"
    kinds = [r[0] for r in _rows(queue)]
    assert kinds == [ledger.KIND_REPORT, ledger.KIND_ATTEMPT]


def test_a_reprobe_that_still_declines_extends_without_a_second_report(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    red = support.readiness(failing_checks=("Frontend E2E (Playwright, blocking)",))
    now = time.time()
    _run(env, now=now, ready=red, enqueue=support.never_enqueue)
    _run(env, now=now + sweep_mod.REPROBE_INTERVAL_S + 1, ready=red, enqueue=support.never_enqueue)
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts) == (True, 1)
    assert entry.next_at == now + 2 * sweep_mod.REPROBE_INTERVAL_S + 1
    assert [r[0] for r in _rows(queue)] == [ledger.KIND_REPORT]
    assert len(_intents(queue)) == 1


def test_reprobing_stops_at_the_cap(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    red = support.readiness(failing_checks=("Frontend E2E (Playwright, blocking)",))
    now = time.time()
    _run(env, now=now, ready=red, enqueue=support.never_enqueue)
    capped = sweep_mod.WatchEntry(
        next_at=0.0,
        attempts=sweep_mod.REPROBE_CAP,
        identity=_watch(queue)[ws.task_id].identity,
        terminal=True,
    )
    sweep_mod._save_watch(queue, {ws.task_id: capped})  # noqa: SLF001
    assert (
        _run(
            env,
            now=now + 10 * sweep_mod.REPROBE_INTERVAL_S,
            ready=support.readiness(),
            enqueue=support.never_enqueue,
        )
        == 0
    )
    assert _watch(queue)[ws.task_id] == replace(
        capped, next_at=now + 11 * sweep_mod.REPROBE_INTERVAL_S, park_head="abc"
    )


def test_a_terminal_reprobe_that_waits_keeps_its_terminal_state(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    red = support.readiness(failing_checks=("Frontend E2E (Playwright, blocking)",))
    pending = support.readiness(pending_checks=("Frontend E2E (Playwright, blocking)",))
    now = time.time()
    _run(env, now=now, ready=red, enqueue=support.never_enqueue)
    _run(
        env,
        now=now + sweep_mod.REPROBE_INTERVAL_S + 1,
        ready=pending,
        enqueue=support.never_enqueue,
    )
    waited = _watch(queue)[ws.task_id]
    assert (waited.terminal, waited.attempts) == (True, 1)
    assert waited.next_at == now + 2 * sweep_mod.REPROBE_INTERVAL_S + 1
    _run(
        env,
        now=now + 2 * sweep_mod.REPROBE_INTERVAL_S + 2,
        ready=red,
        enqueue=support.never_enqueue,
    )
    assert [r[0] for r in _rows(queue)] == [ledger.KIND_REPORT]
    assert len(_intents(queue)) == 1


def _non_actionable_park(ws_root: Path) -> Workspace:
    question = fix_escalation_mod.non_actionable_question(
        None, "inv_008", "both failing clauses need pipeline evidence, not a code change"
    )
    ws = support.parked_ws(
        ws_root, blockers=[Blocker(kind="reason", name=question.id, detail=question.question)]
    )
    support.save(ws, support.handover(ws, from_team="implementation_fix", questions=[question]))
    return ws


def test_a_non_actionable_park_at_a_green_head_is_answered_towards_verification(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    ws = _non_actionable_park(ws_root)
    dispatched: Dispatched = []
    deps = support.deps(review_stream=support.review_line(commit_id="abc"))
    ready = support.readiness(head_sha="abc", review_decision="CHANGES_REQUESTED")
    assert _run(env, now=time.time(), dispatched=dispatched, ready=ready, deps=deps) == 1
    assert dispatched[0][0] == "verification"
    latest = handover_mod.list_handovers(workspace_mod.read(ws.task_id, root=ws_root))[-1]
    assert latest.handover_type == "revisit_amend"
    assert latest.open_questions[0].answer == fix_escalation_mod.RESOLVE_BY_HAND
    attempt = next(r for r in _rows(queue) if r[0] == ledger.KIND_ATTEMPT)
    assert attempt[1] == "non_actionable_green_head"


def test_a_non_actionable_park_the_verifier_rejected_twice_at_the_head_is_reported(
    env: tuple[Queue, WorkspaceIndex, Path],
) -> None:
    queue, _index, ws_root = env
    _non_actionable_park(ws_root)
    dispatched: Dispatched = []
    stream = "\n".join(
        support.review_line(commit_id="abc", review_id=review_id) for review_id in (1, 2)
    )
    ready = support.readiness(head_sha="abc", review_decision="CHANGES_REQUESTED")
    _run(
        env, now=time.time(), dispatched=dispatched, ready=ready,
        deps=support.deps(review_stream=stream),
    )
    assert dispatched == []
    assert _last_report_reason(queue) == (
        "non_actionable_green_head: the verifier already rejected this head twice;"
        " a third pass adds nothing"
    )

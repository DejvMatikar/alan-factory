from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan import workspace as workspace_mod
from alan.engine_config import ForgeTarget
from alan.events import PubSub
from alan.park import Blocker, ParkReason
from alan.queue import Job, Queue
from alan.teams import _pr_ready as pr_ready_mod
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex
from tests import _unblock_support as support

_CHECK = "Frontend E2E (Playwright, blocking)"
Env = tuple[Queue, WorkspaceIndex, Path]


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(dispatch_mod, "team_is_dispatchable", lambda _ws, _team: True)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return queue, WorkspaceIndex(root=ws_root, queue=queue), ws_root


def _red(head: str = "abc") -> pr_ready_mod.PrReadiness:
    return support.readiness(head_sha=head, failing_checks=(_CHECK,))


def _run(
    env: Env,
    *,
    now: float,
    ready: pr_ready_mod.ReadinessResult,
    reads: list[str] | None = None,
    dispatched: list[tuple[str, str | None, str | None]] | None = None,
) -> int:
    queue, index, ws_root = env
    seen = reads if reads is not None else []

    def _read(
        pr_number: int, task_id: str, *, forge_target: ForgeTarget
    ) -> pr_ready_mod.ReadinessResult:
        del pr_number, forge_target
        seen.append(task_id)
        return ready

    return sweep_mod.sweep(
        queue=queue, pubsub=PubSub(), workspace_index=index, workspaces_root=ws_root, now=now,
        lock_for=support.lock_for,
        enqueue_workflow=support.tracking_enqueue(dispatched if dispatched is not None else []),
        read_readiness=_read, deps=support.deps(), issue_client=support.FakeIssueClient(),
    )


def _failed_check_park(ws_root: Path, title: str = "parked") -> Workspace:
    ws = support.parked_ws(
        ws_root, title=title, blockers=[Blocker(kind="reason", name="pr-merge-failed", detail="d")]
    )
    q = support.question(
        question_id="pr-merge-failed",
        text=f"PR #123 failed checks: {_CHECK}.",
        options=("fix it", recipes_mod.TRANSIENT_RETRY_OPTION),
    )
    support.save(ws, support.handover(ws, questions=[q]))
    return ws


def _watch(queue: Queue) -> dict[str, sweep_mod.WatchEntry]:
    return sweep_mod.load_watch(queue)


def _exhaust(queue: Queue, task_id: str, **changes: object) -> sweep_mod.WatchEntry:
    entry = replace(
        _watch(queue)[task_id], attempts=sweep_mod.REPROBE_CAP, next_at=0.0, terminal=True,
        **changes,  # type: ignore[arg-type]
    )
    sweep_mod._save_watch(queue, {**_watch(queue), task_id: entry})  # noqa: SLF001
    return entry


def _report_rows(queue: Queue) -> list[ledger.LedgerRow]:
    return [
        ledger._row_from_sql(row)  # noqa: SLF001
        for row in queue.run(
            "SELECT * FROM unblock_ledger WHERE kind = ? ORDER BY id", (ledger.KIND_REPORT,)
        )
    ]


def _reported_and_exhausted(env: Env, **changes: object) -> tuple[Workspace, float]:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    now = time.time()
    _run(env, now=now, ready=_red())
    _exhaust(queue, ws.task_id, **changes)
    return ws, now


def test_an_exhausted_park_at_an_unmoved_head_extends_without_rearming(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    reads: list[str] = []
    later = now + 10 * sweep_mod.REPROBE_INTERVAL_S
    assert _run(env, now=later, ready=support.readiness(head_sha="abc"), reads=reads) == 0
    entry = _watch(queue)[ws.task_id]
    assert reads == [ws.task_id]
    assert (entry.terminal, entry.attempts) == (True, sweep_mod.REPROBE_CAP)
    assert entry.next_at == later + sweep_mod.REPROBE_INTERVAL_S
    assert entry.park_head == "abc"
    assert len(_report_rows(queue)) == 1


def test_an_exhausted_park_cadence_advances_on_every_due_read(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    step = sweep_mod.REPROBE_INTERVAL_S
    _run(env, now=now + 10 * step, ready=_red())
    _run(env, now=now + 10 * step + 1, ready=_red())
    assert _watch(queue)[ws.task_id].next_at == now + 11 * step
    _run(env, now=now + 11 * step, ready=_red())
    assert _watch(queue)[ws.task_id].next_at == now + 12 * step


def test_an_exhausted_park_that_is_not_yet_due_performs_no_read(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    due_at = now + 5 * sweep_mod.REPROBE_INTERVAL_S
    sweep_mod._save_watch(  # noqa: SLF001
        queue, {ws.task_id: replace(_watch(queue)[ws.task_id], next_at=due_at)}
    )
    reads: list[str] = []
    _run(env, now=due_at - 1, ready=support.readiness(head_sha="moved"), reads=reads)
    assert reads == []
    assert _watch(queue)[ws.task_id].next_at == due_at


def test_a_moved_head_rearms_the_reprobe_budget_and_lets_a_recipe_act(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    dispatched: list[tuple[str, str | None, str | None]] = []
    later = now + 10 * sweep_mod.REPROBE_INTERVAL_S
    green = support.readiness(head_sha="moved")
    assert _run(env, now=later, ready=green, dispatched=dispatched) == 1
    assert [team for team, _origin, _text in dispatched] == ["pr_merge"]
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts, entry.acted) == (False, 1, True)
    assert entry.park_head == "moved"


def test_a_moved_head_that_is_still_red_reports_at_the_new_head_with_a_fresh_budget(
    env: Env,
) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    _run(env, now=now + 10 * sweep_mod.REPROBE_INTERVAL_S, ready=_red("moved"))
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts, entry.park_head) == (True, 0, "moved")
    rows = _report_rows(queue)
    assert [row.head_sha for row in rows] == ["abc", "moved"]
    assert "report #" not in str(rows[1].detail["reason"])


def test_a_park_with_no_recorded_head_never_rearms_and_records_the_live_head(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env, park_head=None)
    step = sweep_mod.REPROBE_INTERVAL_S
    assert _run(env, now=now + 10 * step, ready=support.readiness(head_sha="first")) == 0
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts) == (True, sweep_mod.REPROBE_CAP)
    assert entry.park_head == "first"
    assert len(_report_rows(queue)) == 1


def test_a_park_recorded_by_its_first_read_rearms_only_on_a_later_difference(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env, park_head=None)
    step = sweep_mod.REPROBE_INTERVAL_S
    _run(env, now=now + 10 * step, ready=support.readiness(head_sha="first"))
    dispatched: list[tuple[str, str | None, str | None]] = []
    assert (
        _run(
            env,
            now=now + 12 * step,
            ready=support.readiness(head_sha="second"),
            dispatched=dispatched,
        )
        == 1
    )
    assert [team for team, _origin, _text in dispatched] == ["pr_merge"]
    assert _watch(queue)[ws.task_id].park_head == "second"


def test_a_failed_forge_read_never_rearms_and_keeps_the_recorded_head(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    later = now + 10 * sweep_mod.REPROBE_INTERVAL_S
    assert _run(env, now=later, ready=pr_ready_mod.ReadinessUnavailable("forge unavailable")) == 0
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts, entry.park_head) == (True, sweep_mod.REPROBE_CAP, "abc")
    assert entry.next_at == later + sweep_mod.REPROBE_INTERVAL_S


def test_the_head_read_budget_counts_reads_that_led_to_no_action(env: Env) -> None:
    queue, _index, ws_root = env
    tasks = [_failed_check_park(ws_root, title=f"parked {n}").task_id for n in range(4)]
    now = time.time()
    _run(env, now=now, ready=_red())
    _run(env, now=now, ready=_red())
    assert set(_watch(queue)) == set(tasks)
    for task_id in tasks:
        _exhaust(queue, task_id)
    reads: list[str] = []
    later = now + 10 * sweep_mod.REPROBE_INTERVAL_S
    _run(env, now=later, ready=support.readiness(head_sha="abc"), reads=reads)
    assert len(reads) == sweep_mod.MAX_FORGE_CANDIDATES_PER_TICK
    untouched = [t for t in tasks if _watch(queue)[t].next_at == 0.0]
    assert len(untouched) == len(tasks) - sweep_mod.MAX_FORGE_CANDIDATES_PER_TICK


def test_parks_that_are_not_due_spend_none_of_the_head_read_budget(env: Env) -> None:
    queue, _index, ws_root = env
    tasks = [_failed_check_park(ws_root, title=f"quiet {n}").task_id for n in range(4)]
    now = time.time()
    _run(env, now=now, ready=_red())
    _run(env, now=now, ready=_red())
    step = sweep_mod.REPROBE_INTERVAL_S
    watch = _watch(queue)
    for task_id in tasks[:3]:
        watch[task_id] = replace(watch[task_id], next_at=now + 100 * step)
    sweep_mod._save_watch(queue, watch)  # noqa: SLF001
    reads: list[str] = []
    _run(env, now=now + 2 * step, ready=_red(), reads=reads)
    assert reads == [tasks[3]]


def test_an_unexhausted_terminal_park_keeps_its_first_park_head_when_the_head_moves(
    env: Env,
) -> None:
    queue, _index, _root = env
    ws = _failed_check_park(env[2])
    now = time.time()
    _run(env, now=now, ready=_red("abc"))
    _run(env, now=now + sweep_mod.REPROBE_INTERVAL_S + 1, ready=_red("moved"))
    entry = _watch(queue)[ws.task_id]
    assert (entry.terminal, entry.attempts, entry.park_head) == (True, 1, "abc")


def test_a_job_that_is_running_blocks_the_head_read(env: Env) -> None:
    queue, _index, _root = env
    ws, now = _reported_and_exhausted(env)
    queue.enqueue(ws.task_id, "verification", workspace_id=ws.workspace_id)
    job: Job | None = queue.claim()
    assert job is not None
    reads: list[str] = []
    _run(env, now=now + 10 * sweep_mod.REPROBE_INTERVAL_S, ready=_red("moved"), reads=reads)
    assert reads == []


def _heads(queue: Queue) -> list[str | None]:
    return [
        ledger._row_from_sql(row).head_sha  # noqa: SLF001
        for row in queue.run(
            "SELECT * FROM unblock_ledger WHERE kind != ? ORDER BY id",
            (ledger.KIND_ISSUE_DELIVERED,),
        )
    ]


def test_a_reported_park_records_its_head_on_the_watch_record_and_every_row(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    _run(env, now=time.time(), ready=_red("abc"))
    assert _watch(queue)[ws.task_id].park_head == "abc"
    assert _heads(queue) == ["abc", "abc"]


def test_a_waiting_park_records_its_head_and_keeps_it_when_the_head_moves(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    now = time.time()
    pending = {"pending_checks": (_CHECK,)}
    _run(env, now=now, ready=support.readiness(head_sha="abc", **pending))  # type: ignore[arg-type]
    first = _watch(queue)[ws.task_id]
    assert (first.terminal, first.attempts, first.park_head) == (False, 1, "abc")
    assert _heads(queue) == ["abc"]
    _run(env, now=now + 1000, ready=support.readiness(head_sha="moved", **pending))  # type: ignore[arg-type]
    assert _watch(queue)[ws.task_id].park_head == "abc"


def test_an_acted_park_records_its_head_on_the_attempt_and_the_observing_entry(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    assert _run(env, now=time.time(), ready=support.readiness(head_sha="abc")) == 1
    entry = _watch(queue)[ws.task_id]
    assert (entry.acted, entry.park_head) == (True, "abc")
    assert set(_heads(queue)) == {"abc"}


def test_a_park_with_no_pull_request_records_no_head_and_reads_nothing(env: Env) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(
        ws_root, park_reason=ParkReason.CRASH, team="verification",
        blockers=[Blocker(kind="reason", name="reason", detail="restart interrupted")],
    )
    reads: list[str] = []
    assert _run(env, now=time.time(), ready=_red(), reads=reads) == 1
    assert reads == []
    assert _watch(queue)[ws.task_id].park_head is None
    assert set(_heads(queue)) == {None}


def test_a_park_whose_forge_read_failed_records_no_head(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    _run(env, now=time.time(), ready=pr_ready_mod.ReadinessUnavailable("forge unavailable"))
    assert _watch(queue)[ws.task_id].park_head is None
    assert set(_heads(queue)) == {None}


def _repark(ws_root: Path, ws: Workspace) -> None:
    fresh = workspace_mod.read(ws.task_id, root=ws_root)
    workspace_mod.transition(
        fresh, Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION,
        blockers=list(fresh.meta.blockers),
    )


def _intent_comments(queue: Queue) -> list[str]:
    return [
        str(ledger._row_from_sql(row).detail["comment"])  # noqa: SLF001
        for row in queue.run(
            "SELECT * FROM unblock_ledger WHERE kind = ? ORDER BY id",
            (ledger.KIND_ISSUE_INTENT,),
        )
    ]


def _reasons(queue: Queue) -> list[str]:
    return [str(row.detail["reason"]) for row in _report_rows(queue)]


def test_a_repeat_park_of_one_class_at_an_unmoved_head_reads_as_a_repeat(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    first_at = time.time()
    _run(env, now=first_at, ready=_red("abcdef1234"))
    _repark(ws_root, ws)
    _run(env, now=first_at + 100, ready=_red("abcdef1234"))
    _repark(ws_root, ws)
    _run(env, now=first_at + 200, ready=_red("abcdef1234"))
    first, second, third = _reasons(queue)
    assert "report #" not in first
    suffix = f" at head abcdef12, first reported {ledger.stamp(first_at)}"
    assert second.endswith(f"report #2 of this park class{suffix}")
    assert third.endswith(f"report #3 of this park class{suffix}")
    rows = _report_rows(queue)
    assert len({row.fingerprint for row in rows}) == 1
    assert {row.outcome for row in rows} == {ledger.OUTCOME_NEEDS_HUMAN}
    assert ledger.report_history(queue, ws.task_id, rows[0].fingerprint).count == 3


def test_the_repeat_reason_reaches_the_issue_comment_through_the_same_path(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    now = time.time()
    _run(env, now=now, ready=_red("abcdef1234"))
    _repark(ws_root, ws)
    _run(env, now=now + 100, ready=_red("abcdef1234"))
    second_reason = _reasons(queue)[1]
    assert f"- probed: {second_reason}" in _intent_comments(queue)[1]


def test_a_park_at_a_moved_head_reads_as_a_first_park(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    now = time.time()
    _run(env, now=now, ready=_red("abc"))
    _repark(ws_root, ws)
    _run(env, now=now + 100, ready=_red("def"))
    assert all("report #" not in reason for reason in _reasons(queue))
    first, second = _report_rows(queue)
    assert first.fingerprint != second.fingerprint


def test_a_park_of_another_class_reads_as_a_first_park(env: Env) -> None:
    queue, _index, ws_root = env
    ws = _failed_check_park(ws_root)
    now = time.time()
    _run(env, now=now, ready=_red())
    other = support.question(question_id="another-class", options=("a", "b"))
    support.save(ws, support.handover(ws, questions=[other], handover_id="h_other"))
    _repark(ws_root, ws)
    _run(env, now=now + 100, ready=_red())
    assert all("report #" not in reason for reason in _reasons(queue))
    first, second = _report_rows(queue)
    assert first.fingerprint != second.fingerprint


def test_a_park_class_survives_a_new_invocation_minting_a_new_question_id(env: Env) -> None:
    queue, _index, ws_root = env
    ws = support.parked_ws(ws_root, blockers=[])
    now = time.time()
    for n, invocation in enumerate(("aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3")):
        question = support.question(
            question_id=f"clause-stall-{invocation[:8]}", options=("a", "b")
        )
        parked = support.handover(ws, questions=[question], handover_id=f"h_{n}")
        support.save(ws, parked.model_copy(update={"from_invocation_id": invocation}))
        _repark(ws_root, ws)
        _run(env, now=now + 100 * n, ready=_red("abcdef1234"))
    first, second, third = _reasons(queue)
    assert "report #" not in first
    assert "report #2 of this park class at head abcdef12" in second
    assert "report #3 of this park class at head abcdef12" in third
    assert len({row.fingerprint for row in _report_rows(queue)}) == 1

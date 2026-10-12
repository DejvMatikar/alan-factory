from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover, InjectedTask
from alan.queue import (
    INTRA_WORKSPACE_CAP,
    INTRA_WORKSPACE_CAP_POOLED,
    Job,
    JobStatus,
    Queue,
    QueueError,
)
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Task, TaskStatus, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


def _ts() -> datetime:
    return datetime.now(UTC)


def _terminal_handover(task_id: str, from_team: str = "retrospective") -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_{from_team}",
        handover_type="terminal",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=_ts(),
        full_synthesis="done",
    )


def _handover_with_injection(
    task_id: str, from_team: str, injected: list[InjectedTask]
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_{from_team}_inj",
        handover_type="terminal",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=_ts(),
        full_synthesis="done",
        injected_tasks=injected,
    )


def test_get_workspace_lock_creates_new_lock_for_unknown_workspace(
    root: Path, db_path: Path
) -> None:
    q = Queue(db_path=db_path)
    d = Dispatcher(q, {}, workspaces_root=root)
    lock = d._get_workspace_lock("ws-alpha")  # noqa: SLF001
    assert type(lock).__name__ == "_NonReentrantLock"
    q.close()


def test_workspace_lock_raises_on_same_thread_reentry(
    root: Path, db_path: Path
) -> None:
    q = Queue(db_path=db_path)
    d = Dispatcher(q, {}, workspaces_root=root)
    lock = d._get_workspace_lock("ws-reentry")  # noqa: SLF001
    with lock:
        with pytest.raises(RuntimeError, match="re-entry"):
            with lock:
                pass  # pragma: no cover
    q.close()


def test_get_workspace_lock_returns_same_lock_on_second_call(
    root: Path, db_path: Path
) -> None:
    q = Queue(db_path=db_path)
    d = Dispatcher(q, {}, workspaces_root=root)
    lock1 = d._get_workspace_lock("ws-beta")  # noqa: SLF001
    lock2 = d._get_workspace_lock("ws-beta")  # noqa: SLF001
    assert lock1 is lock2
    q.close()


def test_get_workspace_lock_distinct_for_different_workspaces(
    root: Path, db_path: Path
) -> None:
    q = Queue(db_path=db_path)
    d = Dispatcher(q, {}, workspaces_root=root)
    lock_a = d._get_workspace_lock("ws-a")  # noqa: SLF001
    lock_b = d._get_workspace_lock("ws-b")  # noqa: SLF001
    assert lock_a is not lock_b
    q.close()


def test_concurrent_revisit_increments_not_lost(root: Path, db_path: Path) -> None:
    ws = workspace_mod.create("rev-conc", "d", root=root)
    q = Queue(db_path=db_path, intra_workspace_cap=2)
    d = Dispatcher(q, {}, workspaces_root=root)

    entry_barrier = threading.Barrier(2, timeout=5.0)
    errors: list[Exception] = []

    def do_increment(team: str) -> None:
        try:
            entry_barrier.wait()
            lock = d._get_workspace_lock(ws.workspace_id)  # noqa: SLF001
            with lock:
                fresh = workspace_mod.read(ws.task_id, root=root)
                workspace_mod.increment_revisit_count(fresh, team, "chain-001")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=lambda: do_increment("research"))
    t2 = threading.Thread(target=lambda: do_increment("brainstorming"))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "thread t1 did not finish within timeout"
    assert not t2.is_alive(), "thread t2 did not finish within timeout"
    assert not errors
    final = workspace_mod.read(ws.task_id, root=root)
    k_research = f"{ws.workspace_id}|{ws.task_id}|research|chain-001"
    k_brainstorm = f"{ws.workspace_id}|{ws.task_id}|brainstorming|chain-001"
    assert final.meta.revisit_counts.get(k_research) == 1, "research count lost"
    assert final.meta.revisit_counts.get(k_brainstorm) == 1, "brainstorming count lost"
    q.close()


def test_concurrent_same_key_revisit_increments_both_applied(
    root: Path, db_path: Path
) -> None:
    ws = workspace_mod.create("rev-same-key", "d", root=root)
    q = Queue(db_path=db_path, intra_workspace_cap=2)
    d = Dispatcher(q, {}, workspaces_root=root)
    chain = "chain-xyz"
    key = f"{ws.workspace_id}|{ws.task_id}|research|{chain}"

    entry_barrier = threading.Barrier(2, timeout=5.0)
    errors: list[Exception] = []

    def do_increment() -> None:
        try:
            entry_barrier.wait()
            lock = d._get_workspace_lock(ws.workspace_id)  # noqa: SLF001
            with lock:
                fresh = workspace_mod.read(ws.task_id, root=root)
                workspace_mod.increment_revisit_count(fresh, "research", chain)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=do_increment)
    t2 = threading.Thread(target=do_increment)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "thread t1 did not finish within timeout"
    assert not t2.is_alive(), "thread t2 did not finish within timeout"
    assert not errors
    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.revisit_counts.get(key) == 2, (
        f"Expected count=2 but got {final.meta.revisit_counts.get(key)} (lost-update detected)"
    )
    q.close()


def test_concurrent_injection_not_duplicated(root: Path, db_path: Path) -> None:
    task_t1 = Task(id="t1", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("inj-conc", "d", root=root, tasks=[task_t1])
    q = Queue(db_path=db_path, intra_workspace_cap=2)
    d = Dispatcher(q, {}, workspaces_root=root)

    inj = InjectedTask(id="sub-shared", team="research", instructions="shared work")
    handover = _handover_with_injection("t1", "research", [inj])

    entry_barrier = threading.Barrier(2, timeout=5.0)
    errors: list[Exception] = []

    def do_advance() -> None:
        try:
            entry_barrier.wait()
            lock = d._get_workspace_lock(ws.workspace_id)  # noqa: SLF001
            with lock:
                fresh = workspace_mod.read(ws.task_id, root=root)
                d._advance_workspace_dag(fresh, "t1", handover=handover)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=do_advance)
    t2 = threading.Thread(target=do_advance)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "thread t1 did not finish within timeout"
    assert not t2.is_alive(), "thread t2 did not finish within timeout"
    assert not errors
    final = workspace_mod.read(ws.task_id, root=root)
    injected = [t for t in final.meta.tasks if t.id.startswith("inj-")]
    assert len(injected) == 1, (
        f"Expected 1 injected task, got {len(injected)}: {[t.id for t in injected]}"
    )
    q.close()


def test_observe_task_failure_serialized_by_workspace_lock(
    root: Path, db_path: Path
) -> None:
    task_t1 = Task(id="t1", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_t2 = Task(id="t2", team="research", status=TaskStatus.PENDING, depends_on=["t1"])
    ws = workspace_mod.create("fail-conc", "d", root=root, tasks=[task_t1, task_t2])

    q = Queue(db_path=db_path, intra_workspace_cap=2)
    job1 = q.enqueue("t1", "research", workspace_id=ws.workspace_id)
    job2 = q.enqueue("t2", "research", workspace_id=ws.workspace_id)
    d = Dispatcher(q, {}, workspaces_root=root)

    entry_barrier = threading.Barrier(2, timeout=5.0)
    errors: list[Exception] = []

    def observe(job_id: int, task_id: str) -> None:
        try:
            fake_job = Job(
                id=job_id,
                task_id=task_id,
                workspace_id=ws.workspace_id,
                team="research",
                status=JobStatus.RUNNING,
                enqueued_at=_ts(),
            )
            entry_barrier.wait()
            d._observe_task_failure(fake_job)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=lambda: observe(job1.id, "t1"))
    t2 = threading.Thread(target=lambda: observe(job2.id, "t2"))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "thread t1 did not finish within timeout"
    assert not t2.is_alive(), "thread t2 did not finish within timeout"
    assert not errors
    final = workspace_mod.read(ws.task_id, root=root)
    statuses = {t.id: t.status for t in final.meta.tasks}
    assert statuses["t1"] == TaskStatus.FAILED
    assert statuses["t2"] == TaskStatus.FAILED
    q.close()


def test_observe_task_failure_no_tasks_returns_early(
    root: Path, db_path: Path
) -> None:
    ws = workspace_mod.create("no-tasks-ws", "d", root=root)
    q = Queue(db_path=db_path)
    job = q.enqueue(ws.task_id, "research")
    d = Dispatcher(q, {}, workspaces_root=root)

    fake_job = Job(
        id=job.id,
        task_id=ws.task_id,
        workspace_id=ws.workspace_id,
        team="research",
        status=JobStatus.RUNNING,
        enqueued_at=_ts(),
    )
    d._observe_task_failure(fake_job)  # noqa: SLF001
    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.status == Status.ACTIVE
    q.close()


def test_observe_task_failure_unknown_task_id_logs_warning(
    root: Path, db_path: Path
) -> None:
    task_t1 = Task(id="t1", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("unknown-task-ws", "d", root=root, tasks=[task_t1])
    q = Queue(db_path=db_path)
    job = q.enqueue("ghost", "research", workspace_id=ws.workspace_id)
    d = Dispatcher(q, {}, workspaces_root=root)

    fake_job = Job(
        id=job.id,
        task_id="ghost",
        workspace_id=ws.workspace_id,
        team="research",
        status=JobStatus.RUNNING,
        enqueued_at=_ts(),
    )
    d._observe_task_failure(fake_job)  # noqa: SLF001
    final = workspace_mod.read(ws.task_id, root=root)
    assert {t.status for t in final.meta.tasks} == {TaskStatus.PENDING}
    q.close()


def test_intra_workspace_cap_value_is_one() -> None:
    assert INTRA_WORKSPACE_CAP == 1


def test_intra_workspace_cap_pooled_value_is_two() -> None:
    assert INTRA_WORKSPACE_CAP_POOLED == 2


def test_queue_intra_workspace_cap_stored(tmp_path: Path) -> None:
    q = Queue(db_path=tmp_path / "q.db", intra_workspace_cap=3)
    assert q._intra_workspace_cap == 3  # noqa: SLF001
    q.close()


def test_intra_workspace_cap_allows_two_distinct_tasks_in_parallel(
    root: Path, db_path: Path
) -> None:
    task_a = Task(id="ta", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(id="tb", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("par-ws", "d", root=root, tasks=[task_a, task_b])

    q = Queue(db_path=db_path, intra_workspace_cap=2)
    q.enqueue("ta", "research", workspace_id=ws.workspace_id)
    q.enqueue("tb", "research", workspace_id=ws.workspace_id)

    claimed_a = q.claim()
    claimed_b = q.claim()

    assert claimed_a is not None, "first job should be claimed"
    assert claimed_b is not None, "second job should be claimed with cap=2"
    assert {claimed_a.task_id, claimed_b.task_id} == {"ta", "tb"}
    q.close()


def test_intra_workspace_cap_blocks_third_job(root: Path, db_path: Path) -> None:
    task_a = Task(id="ta", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(id="tb", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_c = Task(id="tc", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("cap-block", "d", root=root, tasks=[task_a, task_b, task_c])

    q = Queue(db_path=db_path, intra_workspace_cap=2)
    q.enqueue("ta", "research", workspace_id=ws.workspace_id)
    q.enqueue("tb", "research", workspace_id=ws.workspace_id)
    q.enqueue("tc", "research", workspace_id=ws.workspace_id)

    c1 = q.claim()
    c2 = q.claim()
    c3 = q.claim()

    assert c1 is not None
    assert c2 is not None
    assert c3 is None, "third job should be blocked by cap=2"
    q.close()


def test_legacy_workspace_serial_with_cap_two(root: Path, db_path: Path) -> None:
    ws = workspace_mod.create("legacy-serial", "d", root=root)

    q = Queue(db_path=db_path, intra_workspace_cap=2)
    q.enqueue(ws.task_id, "research")
    q.enqueue(ws.task_id, "research")

    c1 = q.claim()
    c2 = q.claim()

    assert c1 is not None, "first job should be claimed"
    assert c2 is None, "second job blocked: same task_id already running"
    q.close()


def test_dispatcher_concurrent_dag_advance_with_cap_two(
    root: Path, db_path: Path
) -> None:
    rendezvous = threading.Barrier(2, timeout=5.0)
    both_ran = threading.Event()
    run_count = [0]
    run_count_lock = threading.Lock()

    task_a = Task(id="ta", team="retro", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(id="tb", team="retro", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("dag-par", "d", root=root, tasks=[task_a, task_b])

    class RetroTeam:
        name = "retro"
        supports_resume = False

        def plan_workflow(
            self, _w: Workspace, _p: Handover | None
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _p: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            rendezvous.wait()
            with run_count_lock:
                run_count[0] += 1
                if run_count[0] == 2:
                    both_ran.set()
            return _terminal_handover(workspace.task_id, from_team=self.name)

    q = Queue(db_path=db_path, intra_workspace_cap=2)
    d = Dispatcher(q, {"retro": RetroTeam()}, workspaces_root=root, concurrency=2)

    q.enqueue("ta", "retro", workspace_id=ws.workspace_id)
    q.enqueue("tb", "retro", workspace_id=ws.workspace_id)

    d.start()
    assert both_ran.wait(timeout=10.0), "both jobs did not reach rendezvous within 10s"
    d.stop(timeout=5.0)

    final_jobs = q.list_jobs()
    assert final_jobs
    assert all(j.status is JobStatus.COMPLETED for j in final_jobs)
    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.status is Status.COMPLETED
    q.close()


def test_queue_invalid_intra_workspace_cap_raises(tmp_path: Path) -> None:
    with pytest.raises(QueueError, match="intra_workspace_cap must be >= 1"):
        Queue(db_path=tmp_path / "q.db", intra_workspace_cap=0)


def test_intra_workspace_cap_does_not_serialize_same_task_id_across_workspaces(
    root: Path, db_path: Path
) -> None:
    shared_task_id = "backend"
    task_a = Task(id=shared_task_id, team="research", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(id=shared_task_id, team="research", status=TaskStatus.PENDING, depends_on=[])
    ws_a = workspace_mod.create("ws-cross-a", "d", root=root, tasks=[task_a])
    ws_b = workspace_mod.create("ws-cross-b", "d", root=root, tasks=[task_b])

    q = Queue(db_path=db_path, intra_workspace_cap=1)
    q.enqueue(shared_task_id, "research", workspace_id=ws_a.workspace_id)
    q.enqueue(shared_task_id, "research", workspace_id=ws_b.workspace_id)

    c1 = q.claim()
    c2 = q.claim()

    assert c1 is not None, "first workspace job should be claimed"
    assert c2 is not None, "second workspace job blocked by cross-workspace task_id exclusion"
    assert c1.workspace_id != c2.workspace_id
    q.close()


def test_queue_default_cap_is_one(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    assert q._intra_workspace_cap == 1  # noqa: SLF001
    q.close()


def test_queue_cap_one_blocks_second_workspace_job(
    root: Path, db_path: Path
) -> None:
    task_a = Task(id="ta", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(id="tb", team="research", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("cap-one-ws", "d", root=root, tasks=[task_a, task_b])

    q = Queue(db_path=db_path, intra_workspace_cap=1)
    q.enqueue("ta", "research", workspace_id=ws.workspace_id)
    q.enqueue("tb", "research", workspace_id=ws.workspace_id)

    c1 = q.claim()
    c2 = q.claim()

    assert c1 is not None
    assert c2 is None, "second job should be blocked by cap=1"
    q.close()

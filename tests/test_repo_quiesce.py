from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import build_chat as build_chat_mod
from alan import explore_chat as explore_chat_mod
from alan import worker_records
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, Posture, RepoIdentity, register_and_persist
from alan.queue import Queue
from alan.repo_quiesce import QuiesceReport, quiesce_repo
from alan.slot_pool import SlotPool
from tests.fake_clock import FakeClock


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def build_chat_root(tmp_path: Path) -> Path:
    return tmp_path / "build-chat"


@pytest.fixture
def explore_chat_root(tmp_path: Path) -> Path:
    return tmp_path / "explore-chat"


@pytest.fixture
def build_chat_locks() -> build_chat_mod.BuildChatSessionLocks:
    return build_chat_mod.BuildChatSessionLocks()


@pytest.fixture
def explore_chat_locks() -> explore_chat_mod.ExploreChatSessionLocks:
    return explore_chat_mod.ExploreChatSessionLocks()


def _engine(posture: Posture = Posture.self_merge) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="acme", repo="widgets"), posture=posture)


def _quiesce(
    repo: RepoIdentity,
    *,
    dispatcher: Dispatcher | None,
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> QuiesceReport:
    return quiesce_repo(
        repo,
        dispatcher=dispatcher,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )


def test_quiesce_repo_hides_and_floors_posture(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    from alan.engine_config import lookup_by_repo

    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)

    report = _quiesce(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    live = lookup_by_repo(cfg.repo)
    assert live is not None
    assert live.hidden is True
    assert live.posture is Posture.observe
    assert live.hidden_restore_posture is Posture.self_merge
    assert report.slug == cfg.repo.slug
    assert report.residue_workspace_ids == []
    assert report.workers_interrupted == 0
    assert report.chats_cancelled == 0
    assert report.pool_disposed is False
    assert report.pool_residue is None


def test_quiesce_repo_idle_workspace_is_not_residue(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create(
        "idle-ws", "d", root=workspaces_root, engine_key=cfg.repo.slug
    )
    workspace_mod.transition(ws, workspace_mod.Status.ACTIVE)

    report = _quiesce(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.residue_workspace_ids == []


def test_quiesce_repo_excludes_terminal_workspaces(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create(
        "done-ws", "d", root=workspaces_root, engine_key=cfg.repo.slug
    )
    workspace_mod.transition(ws, workspace_mod.Status.COMPLETED)
    queue.enqueue(ws.task_id, "research")
    queue.claim()

    report = _quiesce(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.residue_workspace_ids == []
    assert queue.is_task_paused(ws.task_id) is False


def test_quiesce_repo_matches_workspace_via_engine_snapshot(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create("snap-ws", "d", root=workspaces_root)
    snapshot = workspace_mod.EngineSnapshot(
        posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)
    workspace_mod.transition(ws, workspace_mod.Status.ACTIVE)
    queue.enqueue(ws.task_id, "research")
    queue.claim()

    report = _quiesce(
        cfg.repo,
        dispatcher=Dispatcher(queue, {}, workspaces_root=workspaces_root),
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.residue_workspace_ids == [ws.task_id]
    assert queue.is_task_paused(ws.task_id) is True


def test_quiesce_repo_reports_residue_when_worker_cannot_be_terminated(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create("t1", "d", root=workspaces_root, engine_key=cfg.repo.slug)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=workspaces_root)

    report = _quiesce(
        cfg.repo,
        dispatcher=d,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.residue_workspace_ids == [ws.task_id]
    assert report.workers_interrupted == 0
    assert queue.is_task_paused(ws.task_id) is True


def test_quiesce_repo_terminates_attached_worker_process(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create("t1", "d", root=workspaces_root, engine_key=cfg.repo.slug)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=workspaces_root)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        d._proc_registry.attach(job.id, proc)  # noqa: SLF001

        report = _quiesce(
            cfg.repo,
            dispatcher=d,
            queue=queue,
            workspaces_root=workspaces_root,
            build_chat_root=build_chat_root,
            build_chat_locks=build_chat_locks,
            explore_chat_root=explore_chat_root,
            explore_chat_locks=explore_chat_locks,
        )

        assert report.workers_interrupted == 1
        assert report.residue_workspace_ids == []
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _write_terminable_record(session_id: str, *, job_id: int, pid: int) -> None:
    identity = worker_records.ps_identity(pid)
    assert identity is not None
    lstart, cmdline = identity
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=job_id, task_id="t1", workspace_id="t1", team="research",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="acme/widgets", slot_path="/fake/slot_0", session_id=session_id,
            output_path="/fake/slot_0/stream.jsonl",
            pid=pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(),
        )
    )


def test_quiesce_repo_terminates_detached_journal_mode_worker_via_worker_records(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create("t1", "d", root=workspaces_root, engine_key=cfg.repo.slug)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        _write_terminable_record("sess-detached", job_id=job.id, pid=proc.pid)
        d = Dispatcher(queue, {}, workspaces_root=workspaces_root)

        report = _quiesce(
            cfg.repo,
            dispatcher=d,
            queue=queue,
            workspaces_root=workspaces_root,
            build_chat_root=build_chat_root,
            build_chat_locks=build_chat_locks,
            explore_chat_root=explore_chat_root,
            explore_chat_locks=explore_chat_locks,
        )

        assert report.workers_interrupted == 1
        assert report.residue_workspace_ids == []
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
        assert worker_records.load_all() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_quiesce_repo_cancels_bound_build_and_explore_chats(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)

    build_session = build_chat_mod.create_session(root=build_chat_root, engine_key=cfg.repo.slug)
    build_entry = build_chat_locks.acquire(build_session.session_id)
    idle_build_session = build_chat_mod.create_session(
        root=build_chat_root, engine_key=cfg.repo.slug
    )
    other_build_session = build_chat_mod.create_session(
        root=build_chat_root, engine_key="other/repo"
    )
    other_build_entry = build_chat_locks.acquire(other_build_session.session_id)

    explore_session = explore_chat_mod.create_session(
        "consultant", root=explore_chat_root, engine_key=cfg.repo.slug
    )
    explore_entry = explore_chat_locks.acquire(explore_session.session_id)

    report = _quiesce(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.chats_cancelled == 2
    assert build_entry.cancel_requested is True
    assert explore_entry.cancel_requested is True
    assert other_build_entry.cancel_requested is False
    del idle_build_session


def test_quiesce_repo_disposes_idle_pool(
    tmp_path: Path,
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=workspaces_root, slot_pools_by_engine={cfg.repo.slug: pool}
    )

    report = _quiesce(
        cfg.repo,
        dispatcher=d,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.pool_disposed is True
    assert report.pool_residue is None
    assert cfg.repo.slug not in d._slot_pools_by_engine  # noqa: SLF001


def test_quiesce_repo_reports_pool_residue_when_busy(
    tmp_path: Path,
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    idx, token = pool._claim()  # noqa: SLF001
    d = Dispatcher(
        queue, {}, workspaces_root=workspaces_root, slot_pools_by_engine={cfg.repo.slug: pool}
    )

    try:
        report = _quiesce(
            cfg.repo,
            dispatcher=d,
            queue=queue,
            workspaces_root=workspaces_root,
            build_chat_root=build_chat_root,
            build_chat_locks=build_chat_locks,
            explore_chat_root=explore_chat_root,
            explore_chat_locks=explore_chat_locks,
        )
    finally:
        pool._release(idx, token)  # noqa: SLF001

    assert report.pool_disposed is False
    assert report.pool_residue is not None
    assert cfg.repo.slug in d._slot_pools_by_engine  # noqa: SLF001


def test_quiesce_repo_skips_pool_dispose_when_no_dispatcher(
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)

    report = _quiesce(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )

    assert report.pool_disposed is False
    assert report.pool_residue is None


def test_quiesce_repo_reports_combined_residue_across_worker_chat_and_pool(
    tmp_path: Path,
    queue: Queue,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    cfg = _engine()
    register_and_persist(cfg, validate_env=False)
    ws = workspace_mod.create("t1", "d", root=workspaces_root, engine_key=cfg.repo.slug)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    idx, token = pool._claim()  # noqa: SLF001
    d = Dispatcher(
        queue, {}, workspaces_root=workspaces_root, slot_pools_by_engine={cfg.repo.slug: pool}
    )
    build_session = build_chat_mod.create_session(root=build_chat_root, engine_key=cfg.repo.slug)
    build_entry = build_chat_locks.acquire(build_session.session_id)

    try:
        report = _quiesce(
            cfg.repo,
            dispatcher=d,
            queue=queue,
            workspaces_root=workspaces_root,
            build_chat_root=build_chat_root,
            build_chat_locks=build_chat_locks,
            explore_chat_root=explore_chat_root,
            explore_chat_locks=explore_chat_locks,
        )
    finally:
        pool._release(idx, token)  # noqa: SLF001

    assert report.residue_workspace_ids == [ws.task_id]
    assert report.workers_interrupted == 0
    assert report.chats_cancelled == 1
    assert build_entry.cancel_requested is True
    assert report.pool_disposed is False
    assert report.pool_residue is not None


def test_quiesce_repo_paused_task_never_wakes_after_eligible_at_passes(
    tmp_path: Path,
    workspaces_root: Path,
    build_chat_root: Path,
    build_chat_locks: build_chat_mod.BuildChatSessionLocks,
    explore_chat_root: Path,
    explore_chat_locks: explore_chat_mod.ExploreChatSessionLocks,
) -> None:
    fake = FakeClock(epoch=1_000.0)
    future_queue = Queue(db_path=tmp_path / "future-queue.db", clock=fake)
    try:
        cfg = _engine()
        register_and_persist(cfg, validate_env=False)
        ws = workspace_mod.create(
            "future-ws", "d", root=workspaces_root, engine_key=cfg.repo.slug
        )
        future_queue.enqueue(ws.task_id, "research", eligible_at=1_500.0)
        assert future_queue.claim() is None

        _quiesce(
            cfg.repo,
            dispatcher=None,
            queue=future_queue,
            workspaces_root=workspaces_root,
            build_chat_root=build_chat_root,
            build_chat_locks=build_chat_locks,
            explore_chat_root=explore_chat_root,
            explore_chat_locks=explore_chat_locks,
        )

        fake.advance(600.0)
        assert future_queue.claim() is None
        assert future_queue.is_task_paused(ws.task_id) is True
    finally:
        future_queue.close()

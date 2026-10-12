from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alan import server, worker_records


def _boot_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    (tmp_path / ".git").mkdir(exist_ok=True)
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines_store"))
    monkeypatch.delenv("ALAN_ENGINES_DIR", raising=False)


def _write_unknown_record(tmp_path: Path, *, slot_path: Path, session_id: str) -> None:
    record = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION,
        job_id=None, task_id=None, workspace_id="ws-1", team="implementation",
        inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
        engine="default", slot_path=str(slot_path), session_id=session_id,
        output_path=str(tmp_path / "stream.jsonl"),
        pid=None, started_at=None, lstart=None, cmdline=None,
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
        host=worker_records.current_host(),
    )
    worker_records.write_initial(record)


def test_never_stamped_record_reservation_does_not_withhold_slot_after_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    slot_0 = tmp_path / "slots" / "slot_0"
    _write_unknown_record(tmp_path, slot_path=slot_0, session_id="sess-never-stamped")

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        pool = dispatcher._slot_pool  # noqa: SLF001
        assert pool is not None
        assert pool._reserved == {}  # noqa: SLF001
        assert 0 in pool._free  # noqa: SLF001

    assert worker_records.load_all()[0][1] is not None


def test_detached_survivor_reservation_still_withholds_slot_after_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    proc = subprocess.Popen(  # noqa: S603,S607
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        slot_0 = tmp_path / "slots" / "slot_0"
        record = worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id="t1", workspace_id="ws-1", team="implementation",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path=str(slot_0), session_id="sess-survivor",
            output_path=str(tmp_path / "stream.jsonl"),
            pid=proc.pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=424242,
        )
        worker_records.write_initial(record)

        test_app = FastAPI(lifespan=server.lifespan)
        with TestClient(test_app):
            dispatcher = test_app.state.dispatcher
            pool = dispatcher._slot_pool  # noqa: SLF001
            assert pool is not None
            assert pool._reserved == {0: "sess-survivor"}  # noqa: SLF001
            assert 0 not in pool._free  # noqa: SLF001
    finally:
        proc.kill()
        proc.wait()


def test_lifespan_removes_dead_record_and_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    dead = worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION,
        job_id=None, task_id=None, workspace_id="ws-1", team="implementation",
        inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
        engine="default", slot_path=str(tmp_path / "slots" / "slot_0"), session_id="sess-dead",
        output_path=str(tmp_path / "stream.jsonl"),
        pid=999999, started_at=1.0, lstart="stale", cmdline="stale-cmd",
        deadline_at=None, idle_timeout_s=None, max_turns=150,
        service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
        host=worker_records.current_host(),
    )
    worker_records.write_initial(dead)

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        pool = dispatcher._slot_pool  # noqa: SLF001
        assert pool is not None
        assert pool._reserved == {}  # noqa: SLF001
        assert pool._free == set(range(pool._size))  # noqa: SLF001

    assert worker_records.load_all() == []


def test_lifespan_boots_identically_with_no_records_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        pool = dispatcher._slot_pool  # noqa: SLF001
        assert pool is not None
        assert pool._reserved == {}  # noqa: SLF001
        assert pool._free == set(range(pool._size))  # noqa: SLF001


def test_reap_runs_before_job_reconciler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    order: list[str] = []

    from alan import reconciler as reconciler_mod
    from alan import worker_reaper as worker_reaper_mod
    from alan.queue import Queue

    real_reap = worker_reaper_mod.reap_and_reserve
    real_reconcile = reconciler_mod.reconcile_on_startup

    def spy_reap(queue: Queue) -> object:
        order.append("reap")
        return real_reap(queue)

    def spy_reconcile(**kwargs: object) -> object:
        order.append("reconcile")
        return real_reconcile(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_reaper_mod, "reap_and_reserve", spy_reap)
    monkeypatch.setattr(reconciler_mod, "reconcile_on_startup", spy_reconcile)

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert order == ["reap", "reconcile"]


def test_deploy_gate_sweep_runs_after_reap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    order: list[str] = []

    from alan import deploy_gate_sweep as deploy_gate_sweep_mod
    from alan import worker_reaper as worker_reaper_mod
    from alan.queue import Queue

    real_reap = worker_reaper_mod.reap_and_reserve
    real_sweep = deploy_gate_sweep_mod.promote_covered_jobs

    def spy_reap(queue: Queue) -> object:
        order.append("reap")
        return real_reap(queue)

    def spy_sweep(queue: Queue) -> int:
        order.append("sweep")
        return real_sweep(queue)

    monkeypatch.setattr(worker_reaper_mod, "reap_and_reserve", spy_reap)
    monkeypatch.setattr(deploy_gate_sweep_mod, "promote_covered_jobs", spy_sweep)

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert order == ["reap", "sweep"]


def test_boot_refuses_clean_when_job_running_and_nothing_reserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    from alan.queue import Queue

    q = Queue(db_path=tmp_path / "queue.db")
    q.enqueue("task-1", "implementation")
    job = q.claim()
    assert job is not None
    q.close()

    from alan import slot_pool as slot_pool_mod

    clean_calls: list[Path] = []
    monkeypatch.setattr(slot_pool_mod, "_clean_slot", lambda path: clean_calls.append(path))

    slot_0 = tmp_path / "slots" / "slot_0"
    slot_0.mkdir(parents=True)
    (slot_0 / ".git").mkdir()

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert clean_calls == []


def test_boot_protects_slot_held_by_a_fresh_affinity_hold_with_no_running_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    from alan import slot_pool as slot_pool_mod

    worker_records.write_slot_hold("ws-successor-pending", str(tmp_path / "slots" / "slot_0"))

    clean_calls: list[Path] = []
    monkeypatch.setattr(slot_pool_mod, "_clean_slot", lambda path: clean_calls.append(path))

    slot_0 = tmp_path / "slots" / "slot_0"
    slot_0.mkdir(parents=True)
    (slot_0 / ".git").mkdir()

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        pool = dispatcher._slot_pool  # noqa: SLF001
        assert pool is not None
        assert 0 in pool._free  # noqa: SLF001

    assert slot_0 not in clean_calls


def test_three_slot_busy_restart_withholds_exactly_one_and_cleans_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    from alan import slot_pool as slot_pool_mod

    proc = subprocess.Popen(  # noqa: S603,S607
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        slot_0 = tmp_path / "slots" / "slot_0"
        slot_1 = tmp_path / "slots" / "slot_1"
        slot_2 = tmp_path / "slots" / "slot_2"
        for slot in (slot_0, slot_1, slot_2):
            slot.mkdir(parents=True)
            (slot / ".git").mkdir()

        record = worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id="t1", workspace_id="ws-1", team="implementation",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path=str(slot_0), session_id="sess-survivor",
            output_path=str(tmp_path / "stream.jsonl"),
            pid=proc.pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=424242,
        )
        worker_records.write_initial(record)
        _write_unknown_record(tmp_path, slot_path=slot_1, session_id="sess-never-stamped")
        worker_records.write_slot_hold("ws-held", str(slot_2))

        clean_calls: list[Path] = []
        monkeypatch.setattr(slot_pool_mod, "_clean_slot", lambda path: clean_calls.append(path))

        test_app = FastAPI(lifespan=server.lifespan)
        with TestClient(test_app):
            dispatcher = test_app.state.dispatcher
            pool = dispatcher._slot_pool  # noqa: SLF001
            assert pool is not None
            assert 0 not in pool._free  # noqa: SLF001
            assert 1 in pool._free  # noqa: SLF001
            assert 2 in pool._free  # noqa: SLF001

        assert slot_0 not in clean_calls
        assert slot_1 not in clean_calls
        assert slot_2 not in clean_calls
    finally:
        proc.kill()
        proc.wait()


def test_boot_cleans_slots_when_no_jobs_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    from alan import slot_pool as slot_pool_mod

    clean_calls: list[Path] = []
    monkeypatch.setattr(slot_pool_mod, "_clean_slot", lambda path: clean_calls.append(path))

    slot_0 = tmp_path / "slots" / "slot_0"
    slot_0.mkdir(parents=True)
    (slot_0 / ".git").mkdir()

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert slot_0 in clean_calls


def test_dial_off_terminates_all_detached_before_reap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    order: list[str] = []

    from alan import worker_reaper as worker_reaper_mod

    real_terminate = worker_reaper_mod.terminate_all_detached
    real_reap = worker_reaper_mod.reap_and_reserve

    def spy_terminate() -> None:
        order.append("terminate_all_detached")
        real_terminate()

    def spy_reap(queue: object) -> object:
        order.append("reap")
        return real_reap(queue)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_reaper_mod, "terminate_all_detached", spy_terminate)
    monkeypatch.setattr(worker_reaper_mod, "reap_and_reserve", spy_reap)

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert order == ["terminate_all_detached", "reap"]

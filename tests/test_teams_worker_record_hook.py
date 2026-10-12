from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from alan import worker_records
from alan.teams._worker_record_hook import worker_record
from alan.workspace import Workspace, create


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("hook test", "d", root=tmp_path / "workspaces")


@dataclass
class _FakeProcStartedHook:
    job_id: int
    calls: list[object] = field(default_factory=list)

    def __call__(self, proc: object) -> None:
        self.calls.append(proc)


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def _fake_proc(pid: int) -> "subprocess.Popen[str]":
    return cast("subprocess.Popen[str]", _FakeProc(pid))


def _one_record() -> worker_records.WorkerRecord:
    [(_path, record)] = worker_records.load_all()
    assert record is not None
    return record


def test_worker_record_writes_before_yield(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-a",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        record = _one_record()
        assert record.session_id == "sess-a"
        assert record.pid is None


def test_worker_record_removes_on_normal_exit(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-b",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        pass
    assert worker_records.load_all() == []


def test_worker_record_swallows_oserror_from_remove_on_exit(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_session_id: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(worker_records, "remove_record", boom)
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-b2",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        pass


def test_worker_record_removes_on_exception_exit(workspace: Workspace, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-c",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        raise RuntimeError("boom")
    assert worker_records.load_all() == []


def test_worker_record_stamps_after_proc_started(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-d",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        hooks.on_proc_started(_fake_proc(4242))
        record = _one_record()
        assert record.pid == 4242
        assert record.started_at is not None


def test_worker_record_caller_callback_runs_before_stamp(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    original_stamp = worker_records.stamp_started

    def spy_stamp(*a: object, **kw: object) -> None:
        order.append("stamp")
        original_stamp(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_records, "stamp_started", spy_stamp)

    def caller_cb(_proc: object) -> None:
        order.append("caller")

    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-e",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150,
        on_proc_started=caller_cb,
    ) as hooks:
        hooks.on_proc_started(_fake_proc(1))
    assert order == ["caller", "stamp"]


def test_worker_record_swallows_oserror_from_stamp(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(worker_records, "stamp_started", boom)
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-f",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        hooks.on_proc_started(_fake_proc(1))


def test_worker_record_derives_job_id_from_callback(workspace: Workspace, tmp_path: Path) -> None:
    hook = _FakeProcStartedHook(job_id=42)
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-g",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=hook,
    ) as hooks:
        record = _one_record()
        assert record.job_id == 42
        hooks.on_proc_started(_fake_proc(1))
    assert hook.calls


def test_worker_record_job_id_none_for_plain_callback(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-h",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150,
        on_proc_started=lambda _p: None,
    ):
        assert _one_record().job_id is None


def test_worker_record_job_id_none_when_callback_job_id_is_bool(
    workspace: Workspace, tmp_path: Path
) -> None:
    @dataclass
    class _BoolHook:
        job_id: bool = True

        def __call__(self, proc: object) -> None:
            del proc

    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-i",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=_BoolHook(),
    ):
        assert _one_record().job_id is None


def test_worker_record_job_id_none_when_no_callback(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-j",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().job_id is None


def test_worker_record_slot_path_from_cwd(workspace: Workspace, tmp_path: Path) -> None:
    cwd = tmp_path / "slot_0"
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-k",
        stream_path=tmp_path / "stream.jsonl", cwd=cwd, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().slot_path == str(cwd)


def test_worker_record_slot_path_empty_when_cwd_none(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-l",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().slot_path == ""


def test_worker_record_engine_from_workspace_meta_engine_key(
    workspace: Workspace, tmp_path: Path
) -> None:
    stamped_meta = workspace.meta.model_copy(update={"engine_key": "sprocket-co/sprocket"})
    stamped = workspace.model_copy(update={"meta": stamped_meta})
    with worker_record(
        stamped, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-m",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().engine == "sprocket-co/sprocket"


def test_worker_record_engine_empty_when_engine_key_none(
    workspace: Workspace, tmp_path: Path
) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-n",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().engine == ""


def test_worker_record_output_path_from_stream_path(workspace: Workspace, tmp_path: Path) -> None:
    stream_path = tmp_path / "custom" / "stream.jsonl"
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-o",
        stream_path=stream_path, cwd=None, max_turns=150, on_proc_started=None,
    ):
        assert _one_record().output_path == str(stream_path)


def test_worker_record_deadline_at_uses_abs_timeout(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_ABS_TIMEOUT_SECONDS", "100")
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-p",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        record = _one_record()
        assert record.deadline_at is not None


def test_worker_record_task_id_none_still_writes_and_reserves(
    workspace: Workspace, tmp_path: Path
) -> None:
    with worker_record(
        workspace, task_id=None, team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-q",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ):
        record = _one_record()
        assert record.task_id is None
        assert record.session_id == "sess-q"


def test_worker_record_real_subprocess_end_to_end(workspace: Workspace, tmp_path: Path) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-r",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
        try:
            proc.wait(timeout=5.0)
            hooks.on_proc_started(proc)
            record = _one_record()
            assert record.pid == proc.pid
            assert record.started_at is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_worker_record_journal_stream_reflects_detached_mode_on(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "on")
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-s",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        assert hooks.journal_stream is True


def test_worker_record_journal_stream_reflects_detached_mode_off(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-t",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        assert hooks.journal_stream is False


def test_worker_record_stamps_pgid_and_offsets_when_journal_opened_before_spawn(
    workspace: Workspace, tmp_path: Path
) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-u",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        hooks.on_journal_opened(10, 20)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
        try:
            hooks.on_proc_started(proc)
            record = _one_record()
            assert record.pgid is not None
            assert record.journal_offset == 10
            assert record.err_offset == 20
        finally:
            proc.kill()
            proc.wait()


def test_worker_record_no_detach_stamp_when_journal_never_opened(
    workspace: Workspace, tmp_path: Path
) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-v",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        hooks.on_proc_started(_fake_proc(4243))
        record = _one_record()
        assert record.pgid is None
        assert record.journal_offset is None


def test_worker_record_no_detach_stamp_when_pgid_lookup_fails(
    workspace: Workspace, tmp_path: Path
) -> None:
    with worker_record(
        workspace, task_id="t1", team="research", inv_id="inv_001", node_id="research",
        role="research", model="sonnet", session_id="sess-w",
        stream_path=tmp_path / "stream.jsonl", cwd=None, max_turns=150, on_proc_started=None,
    ) as hooks:
        hooks.on_journal_opened(5, 6)
        hooks.on_proc_started(_fake_proc(999999))
        record = _one_record()
        assert record.pgid is None
        assert record.journal_offset is None

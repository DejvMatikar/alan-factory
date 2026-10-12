from __future__ import annotations

import contextlib
import gzip
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import retention
from alan import workspace as ws_mod
from alan.queue import Queue
from alan.retention import archive_stale_completed, is_archivable_status
from alan.workspace import Status, create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _retention_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ALAN_AUTO_ARCHIVE_DAYS", raising=False)
    monkeypatch.delenv("ALAN_AUTO_ARCHIVE_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _completed_ws(ws_root: Path, title: str, *, age_days: int) -> str:
    ws = create(title, "d", root=ws_root)
    ws = ws_mod.transition(ws, Status.COMPLETED)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    return ws.task_id


def _audit_rows(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "retention_audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("status", list(Status))
def test_is_archivable_status_only_completed(status: Status) -> None:
    assert is_archivable_status(status) == (status == Status.COMPLETED)


def test_disabled_when_env_unset(ws_root: Path, queue: Queue) -> None:
    _completed_ws(ws_root, "Old Done", age_days=30)
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == []


def test_disabled_on_invalid_or_nonpositive_env(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    _completed_ws(ws_root, "Old Done", age_days=30)
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "not-a-number")
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == []
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "0")
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == []


def test_archives_only_stale_completed(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    fresh_id = _completed_ws(ws_root, "Fresh Done", age_days=1)
    failed = create("Old Failed", "d", root=ws_root)
    ws_mod.transition(failed, Status.FAILED)
    active = create("Still Active", "d", root=ws_root)

    archived = archive_stale_completed(queue=queue, workspaces_root=ws_root)

    assert archived == [old_id]
    assert ws_mod.read(old_id, root=ws_root).meta.status == Status.ARCHIVED
    assert ws_mod.read(fresh_id, root=ws_root).meta.status == Status.COMPLETED
    assert ws_mod.read(failed.task_id, root=ws_root).meta.status == Status.FAILED
    assert ws_mod.read(active.task_id, root=ws_root).meta.status == Status.ACTIVE
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["task_id"] == old_id
    assert rows[0]["dry_run"] is False
    assert rows[0]["min_age_days"] == 14


def _completed_ws_naive_updated_at(ws_root: Path, title: str, *, age_days: int) -> str:
    ws = create(title, "d", root=ws_root)
    ws = ws_mod.transition(ws, Status.COMPLETED)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    naive = (datetime.now(UTC) - timedelta(days=age_days)).replace(tzinfo=None)
    raw["updated_at"] = naive.isoformat()
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    return ws.task_id


def test_stale_candidates_with_naive_updated_at_workspace_does_not_abort_sweep(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    naive_id = _completed_ws_naive_updated_at(ws_root, "Naive Done", age_days=30)
    aware_id = _completed_ws(ws_root, "Aware Done", age_days=30)
    fresh_id = _completed_ws(ws_root, "Fresh Done", age_days=1)

    archived = archive_stale_completed(queue=queue, workspaces_root=ws_root)

    assert set(archived) == {naive_id, aware_id}
    assert ws_mod.read(naive_id, root=ws_root).meta.status == Status.ARCHIVED
    assert ws_mod.read(aware_id, root=ws_root).meta.status == Status.ARCHIVED
    assert ws_mod.read(fresh_id, root=ws_root).meta.status == Status.COMPLETED


def test_skips_workspace_with_active_jobs(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    busy_id = _completed_ws(ws_root, "Busy Done", age_days=30)
    queue.enqueue(busy_id, "research")

    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == []
    assert ws_mod.read(busy_id, root=ws_root).meta.status == Status.COMPLETED


def test_dry_run_audits_without_archiving(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DRY_RUN", "1")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)

    archived = archive_stale_completed(queue=queue, workspaces_root=ws_root)

    assert archived == [old_id]
    assert ws_mod.read(old_id, root=ws_root).meta.status == Status.COMPLETED
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["dry_run"] is True


def test_dry_run_zero_means_disabled_dry_run(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DRY_RUN", "0")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == [old_id]
    assert ws_mod.read(old_id, root=ws_root).meta.status == Status.ARCHIVED


def test_explicit_now_controls_cutoff(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    past = datetime.now(UTC) - timedelta(days=25)
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root, now=past) == []
    assert (
        archive_stale_completed(queue=queue, workspaces_root=ws_root)
        == [old_id]
    )


def test_default_logs_dir_used_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert retention._logs_dir() == Path.home() / ".alan" / "logs"


def test_dry_run_falsy_words_disable_dry_run(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DRY_RUN", "False")
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == [old_id]
    assert ws_mod.read(old_id, root=ws_root).meta.status == Status.ARCHIVED


def test_dry_run_unrecognized_value_stays_conservative(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DRY_RUN", "banana")
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == [old_id]
    assert ws_mod.read(old_id, root=ws_root).meta.status == Status.COMPLETED


def test_one_failed_archive_does_not_abort_the_pass(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    bad_id = _completed_ws(ws_root, "A Bad One", age_days=30)
    good_id = _completed_ws(ws_root, "B Good One", age_days=30)
    original = ws_mod.transition

    def flaky(ws: ws_mod.Workspace, status: Status, **kwargs: object) -> ws_mod.Workspace:
        if ws.task_id == bad_id:
            raise RuntimeError("disk exploded")
        return original(ws, status)

    monkeypatch.setattr(ws_mod, "transition", flaky)
    archived = archive_stale_completed(queue=queue, workspaces_root=ws_root)
    assert archived == [good_id]
    assert ws_mod.read(good_id, root=ws_root).meta.status == Status.ARCHIVED
    rows = _audit_rows(tmp_path)
    assert [r["task_id"] for r in rows] == [good_id]


def _stream_file(ws: ws_mod.Workspace, name: str, body: str) -> Path:
    agents = ws.path / "invocations" / "inv_001" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    stream = agents / f"{name}.stream.jsonl"
    stream.write_text(body, encoding="utf-8")
    return stream


def _archived_ws_with_stream(ws_root: Path, title: str) -> tuple[str, Path]:
    ws = create(title, "d", root=ws_root)
    ws = ws_mod.transition(ws, Status.ARCHIVED)
    return ws.task_id, _stream_file(ws, "implementer", '{"n": 1}\n{"n": 2}\n')


def test_compress_disabled_when_env_unset(ws_root: Path) -> None:
    _, stream = _archived_ws_with_stream(ws_root, "Archived One")
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 0
    assert stream.is_file()


def test_compress_skipped_in_dry_run(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DRY_RUN", "1")
    _, stream = _archived_ws_with_stream(ws_root, "Archived One")
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 0
    assert stream.is_file()


def test_compresses_archived_streams_and_audits(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws_id, stream = _archived_ws_with_stream(ws_root, "Archived One")
    active = create("Still Active", "d", root=ws_root)
    active_stream = _stream_file(active, "implementer", '{"n": 9}\n')

    assert retention.compress_archived_streams(workspaces_root=ws_root) == 1

    assert not stream.exists()
    gz = stream.with_name(stream.name + ".gz")
    assert gzip.decompress(gz.read_bytes()) == b'{"n": 1}\n{"n": 2}\n'
    assert active_stream.is_file()
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["task_id"] == ws_id
    assert rows[0]["compressed_streams"] == 1


def test_compress_skips_workspace_without_invocations(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("Archived Bare", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 0


def test_compress_ignores_stream_named_directories(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("Archived Odd", "d", root=ws_root)
    ws = ws_mod.transition(ws, Status.ARCHIVED)
    (ws.path / "invocations" / "inv_001" / "agents" / "odd.stream.jsonl").mkdir(parents=True)
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 0


def test_compress_budget_caps_each_pass(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.setattr(retention, "_MAX_COMPRESSIONS_PER_PASS", 1)
    _, first = _archived_ws_with_stream(ws_root, "Archived A")
    _, second = _archived_ws_with_stream(ws_root, "Archived B")
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 1
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 1
    assert not first.exists()
    assert not second.exists()


def test_compress_budget_stops_mid_workspace(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.setattr(retention, "_MAX_COMPRESSIONS_PER_PASS", 1)
    ws_id, first = _archived_ws_with_stream(ws_root, "Archived Two")
    ws = ws_mod.read(ws_id, root=ws_root)
    second = _stream_file(ws, "verifier", '{"n": 3}\n')
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 1
    assert not first.exists()
    assert second.is_file()
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 1
    assert not second.exists()


def test_compress_failure_keeps_original(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    _, stream = _archived_ws_with_stream(ws_root, "Archived One")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", boom)
    assert retention.compress_archived_streams(workspaces_root=ws_root) == 0
    assert stream.is_file()
    assert not stream.with_name(stream.name + ".gz.tmp").exists()
    assert not stream.with_name(stream.name + ".gz").exists()


def test_archive_pass_compresses_newly_archived_streams(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    old_id = _completed_ws(ws_root, "Old Done", age_days=30)
    ws = ws_mod.read(old_id, root=ws_root)
    stream = _stream_file(ws, "implementer", '{"n": 1}\n')

    assert archive_stale_completed(queue=queue, workspaces_root=ws_root) == [old_id]

    assert not stream.exists()
    assert stream.with_name(stream.name + ".gz").is_file()

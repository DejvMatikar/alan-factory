from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import workspace_dag
from alan.archive_guard import (
    INCOMPLETE_SUBTASKS_PREFIX,
    ArchiveRequest,
    record_forced_archive,
    refusal_detail,
)
from alan.roots import logs_root
from alan.workspace import Task, TaskStatus, create
from alan.workspace_dag import all_leaves_done, incomplete_subtask_ids


def test_all_leaves_done_true_for_no_tasks(tmp_path: Path) -> None:
    ws = create("No Tasks", "d", root=tmp_path)
    assert all_leaves_done(ws) is True
    assert incomplete_subtask_ids(ws) == ()


def test_all_leaves_done_true_for_completed_and_failed_mix(tmp_path: Path) -> None:
    ws = create(
        "Terminal Mix",
        "d",
        root=tmp_path,
        tasks=[
            Task(id="a", status=TaskStatus.COMPLETED),
            Task(id="b", status=TaskStatus.FAILED),
        ],
    )
    assert all_leaves_done(ws) is True
    assert incomplete_subtask_ids(ws) == ()


def test_all_leaves_done_false_with_one_awaiting_input(tmp_path: Path) -> None:
    ws = create(
        "Awaiting",
        "d",
        root=tmp_path,
        tasks=[
            Task(id="a", status=TaskStatus.COMPLETED),
            Task(id="b", status=TaskStatus.AWAITING_INPUT),
        ],
    )
    assert all_leaves_done(ws) is False
    assert incomplete_subtask_ids(ws) == ("b",)


def test_incomplete_subtask_ids_preserves_task_order_for_all_pending(tmp_path: Path) -> None:
    ws = create(
        "All Pending",
        "d",
        root=tmp_path,
        tasks=[
            Task(id="c", status=TaskStatus.PENDING),
            Task(id="a", status=TaskStatus.PENDING),
            Task(id="b", status=TaskStatus.PENDING),
        ],
    )
    assert all_leaves_done(ws) is False
    assert incomplete_subtask_ids(ws) == ("c", "a", "b")


def test_archive_request_accepts_default() -> None:
    body = ArchiveRequest()
    assert body.force is False
    assert body.reason == ""


def test_archive_request_accepts_non_forced_reason() -> None:
    body = ArchiveRequest(force=False, reason="x")
    assert body.force is False


def test_archive_request_accepts_forced_with_reason() -> None:
    body = ArchiveRequest(force=True, reason="cleanup")
    assert body.force is True
    assert body.reason == "cleanup"


def test_archive_request_rejects_forced_with_empty_reason() -> None:
    with pytest.raises(ValidationError):
        ArchiveRequest(force=True, reason="")


def test_archive_request_rejects_forced_with_whitespace_reason() -> None:
    with pytest.raises(ValidationError):
        ArchiveRequest(force=True, reason="   ")


def test_refusal_detail_none_when_all_leaves_done(tmp_path: Path) -> None:
    ws = create("Done", "d", root=tmp_path, tasks=[Task(id="a", status=TaskStatus.COMPLETED)])
    assert refusal_detail(ws) is None


def test_refusal_detail_prefixed_string_when_incomplete(tmp_path: Path) -> None:
    ws = create(
        "Incomplete",
        "d",
        root=tmp_path,
        tasks=[Task(id="a", status=TaskStatus.AWAITING_INPUT)],
    )
    assert refusal_detail(ws) == f"{INCOMPLETE_SUBTASKS_PREFIX}: a"


def test_record_forced_archive_writes_audit_row(tmp_path: Path) -> None:
    ws = create("Forced", "d", root=tmp_path, tasks=[Task(id="a", status=TaskStatus.PENDING)])
    record_forced_archive(ws, "abandon this")
    lines = (logs_root() / "archive_audit.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["task_id"] == ws.task_id
    assert row["reason"] == "abandon this"
    assert "timestamp_utc" in row


def test_all_leaves_done_rename_left_no_underscore_shim() -> None:
    assert not hasattr(workspace_dag, "_all_leaves_done")


def test_incomplete_subtasks_prefix_literal_is_pinned() -> None:
    assert INCOMPLETE_SUBTASKS_PREFIX == "workspace has incomplete subtasks"

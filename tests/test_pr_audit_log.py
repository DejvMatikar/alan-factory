from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.teams import _pr_audit_log


def test_audit_dir_honours_alan_logs_root_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "custom-logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(target))
    assert _pr_audit_log.audit_dir() == target


def test_append_audit_writes_one_jsonl_line_per_call() -> None:
    _pr_audit_log.append_audit({"task_id": "t1", "action": "a"})
    _pr_audit_log.append_audit({"task_id": "t2", "action": "b"})
    log = _pr_audit_log.audit_dir() / _pr_audit_log.AUDIT_FILE
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "t1"
    assert json.loads(lines[1])["task_id"] == "t2"


def test_append_copilot_audit_writes_to_distinct_file() -> None:
    _pr_audit_log.append_audit({"action": "impl"})
    _pr_audit_log.append_copilot_audit({"action": "copilot"})
    impl_log = _pr_audit_log.audit_dir() / _pr_audit_log.AUDIT_FILE
    copilot_log = _pr_audit_log.audit_dir() / _pr_audit_log.COPILOT_AUDIT_FILE
    assert impl_log != copilot_log
    assert json.loads(impl_log.read_text(encoding="utf-8").splitlines()[0])["action"] == "impl"
    assert (
        json.loads(copilot_log.read_text(encoding="utf-8").splitlines()[0])["action"]
        == "copilot"
    )


def test_append_audit_uses_impl_log_label(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_append(path: Path, entry: dict[str, object], *, log_label: str) -> None:
        del path, entry
        calls.append(log_label)

    monkeypatch.setattr(_pr_audit_log, "append_jsonl_audit", fake_append)
    _pr_audit_log.append_audit({"action": "x"})
    assert calls == ["impl"]


def test_append_copilot_audit_uses_copilot_log_label(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_append(path: Path, entry: dict[str, object], *, log_label: str) -> None:
        del path, entry
        calls.append(log_label)

    monkeypatch.setattr(_pr_audit_log, "append_jsonl_audit", fake_append)
    _pr_audit_log.append_copilot_audit({"action": "x"})
    assert calls == ["copilot"]

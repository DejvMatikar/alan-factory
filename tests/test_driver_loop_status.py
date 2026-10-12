from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import driver_loop_pause as pause_mod
from alan import driver_loop_runs as runs_mod
from alan import driver_loop_status as status_mod
from alan.detached_mode import DIAL_ENV
from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome, RunRecord
from alan.driver_loop_schedule import HoldReason
from alan.queue import Queue


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "babysit",
        "command": "/babysit",
        "cwd": "/opt/checkouts/alan-checkout",
        "self_paced": True,
        "timeout_seconds": 3600,
        "enabled": True,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


@pytest.fixture(autouse=True)
def _supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")


@pytest.fixture(autouse=True)
def _redirect_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _reset_run_now_state() -> Iterator[None]:
    status_mod._run_now_requested.clear()
    yield
    status_mod._run_now_requested.clear()


def test_interactive_lock_present_true_when_file_exists(tmp_path: Path) -> None:
    entry = _entry(cwd=str(tmp_path))
    lock = status_mod.interactive_lock_path(entry)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{}", encoding="utf-8")
    assert status_mod.interactive_lock_present(entry) is True


def test_interactive_lock_present_false_when_absent(tmp_path: Path) -> None:
    entry = _entry(cwd=str(tmp_path))
    assert status_mod.interactive_lock_present(entry) is False


def test_build_context_reflects_not_supervised(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    ctx = status_mod.build_context(_entry(), queue=queue)
    assert ctx.supervised is False


def test_build_context_reads_usage_pause_from_queue(queue: Queue) -> None:
    queue.pause_until(9_999_999_999.0)
    ctx = status_mod.build_context(_entry(), queue=queue)
    assert ctx.usage_paused_until == 9_999_999_999.0


def test_build_context_running_true_when_unfinished_run_exists(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=99999.0,
                stdout_log="x", stderr_log="y",
            )
        ],
    )
    ctx = status_mod.build_context(_entry(), queue=queue)
    assert ctx.running is True


def test_build_context_self_paced_next_wake_derived_from_last_run(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=99999.0,
                stdout_log="x", stderr_log="y", ended_at=500.0, next_wake_hint_s=300.0,
                outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    ctx = status_mod.build_context(_entry(self_paced=True), queue=queue)
    assert ctx.self_paced_next_wake_at == 800.0


def test_snapshot_returns_one_view_per_loop(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(
        status_mod, "load_registry", lambda: ([_entry(name="a"), _entry(name="b")], [])
    )
    views = status_mod.snapshot(queue=queue)
    assert [v.name for v in views] == ["a", "b"]


def test_snapshot_view_reports_last_outcome_and_denials(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=2.0,
                stdout_log="x", stderr_log="y", ended_at=2.0,
                outcome=RunOutcome.RAN_WITH_DENIALS, denial_count=3, final_message="done",
            )
        ],
    )
    views = status_mod.snapshot(queue=queue)
    assert views[0].last_outcome == RunOutcome.RAN_WITH_DENIALS
    assert views[0].denial_count == 3
    assert views[0].final_message == "done"


def test_snapshot_view_skips_unfinished_run_to_find_last_finished(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=2.0,
                stdout_log="x", stderr_log="y", ended_at=2.0, outcome=RunOutcome.SUCCEEDED,
            ),
            RunRecord(
                run_id="2", loop_name=name, started_at=3.0, deadline_at=99999.0,
                stdout_log="x", stderr_log="y",
            ),
        ],
    )
    views = status_mod.snapshot(queue=queue)
    assert views[0].last_outcome == RunOutcome.SUCCEEDED


def test_snapshot_view_defaults_when_no_runs(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(runs_mod, "list_runs", lambda name: [])
    views = status_mod.snapshot(queue=queue)
    assert views[0].last_outcome is None
    assert views[0].denial_count == 0
    assert views[0].final_message == ""


def test_request_run_now_unknown_loop_is_refused(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([], []))
    assert status_mod.request_run_now("nope", queue=queue) == "unknown_loop"


def test_request_run_now_refused_when_disabled(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    disabled_entry = _entry(name="a", enabled=False)
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([disabled_entry], []))
    assert status_mod.request_run_now("a", queue=queue) == HoldReason.DISABLED.value


def test_request_run_now_refused_when_paused(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(pause_mod, "is_paused", lambda name: True)
    assert status_mod.request_run_now("a", queue=queue) == HoldReason.PAUSED.value


def test_request_run_now_refused_when_running(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=99999.0,
                stdout_log="x", stderr_log="y",
            )
        ],
    )
    assert status_mod.request_run_now("a", queue=queue) == HoldReason.RUNNING.value


def test_request_run_now_accepted_sets_pending_flag(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    result = status_mod.request_run_now("a", queue=queue)
    assert result is None
    assert status_mod.pop_run_now_request("a") is True


def test_pop_run_now_request_consumes_flag_once(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.request_run_now("a", queue=queue)
    assert status_mod.pop_run_now_request("a") is True
    assert status_mod.pop_run_now_request("a") is False


def test_pop_run_now_request_false_when_never_requested() -> None:
    assert status_mod.pop_run_now_request("never-requested") is False


def test_clear_run_now_request_removes_pending(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.request_run_now("a", queue=queue)
    status_mod.clear_run_now_request("a")
    assert status_mod.pop_run_now_request("a") is False


def test_snapshot_view_run_now_pending_reflects_flag(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.request_run_now("a", queue=queue)
    views = status_mod.snapshot(queue=queue)
    assert views[0].run_now_pending is True
    status_mod.clear_run_now_request("a")


def test_run_now_pending_true_without_consuming_flag(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.request_run_now("a", queue=queue)
    assert status_mod.run_now_pending("a") is True
    assert status_mod.run_now_pending("a") is True
    assert status_mod.pop_run_now_request("a") is True


def test_run_now_pending_false_when_never_requested() -> None:
    assert status_mod.run_now_pending("never-requested") is False


def test_last_fast_forward_failure_none_when_never_recorded() -> None:
    assert status_mod.last_fast_forward_failure("a") is None


def test_record_and_read_fast_forward_failure_round_trip() -> None:
    status_mod.record_fast_forward_failure("a", "git fetch failed: boom")
    assert status_mod.last_fast_forward_failure("a") == "git fetch failed: boom"


def test_clear_fast_forward_failure_removes_recorded_detail() -> None:
    status_mod.record_fast_forward_failure("a", "git fetch failed: boom")
    status_mod.clear_fast_forward_failure("a")
    assert status_mod.last_fast_forward_failure("a") is None


def test_clear_fast_forward_failure_is_safe_when_never_recorded() -> None:
    status_mod.clear_fast_forward_failure("never-recorded")


def test_fast_forward_failure_is_scoped_per_loop() -> None:
    status_mod.record_fast_forward_failure("a", "boom for a")
    assert status_mod.last_fast_forward_failure("b") is None


def test_status_for_reports_fast_forward_failure_reason(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.record_fast_forward_failure("a", "git fetch failed: boom")
    views = status_mod.snapshot(queue=queue)
    assert views[0].decision.reason == HoldReason.FAST_FORWARD_FAILED
    assert views[0].decision.detail == "git fetch failed: boom"

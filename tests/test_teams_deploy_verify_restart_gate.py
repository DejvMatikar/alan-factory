from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import deploy_signal
from alan.deploy_verify import GateRecord
from alan.queue import Queue
from alan.teams import _deploy_restart_probe
from alan.teams import deploy_verify as deploy_verify_mod
from alan.workspace import Workspace, create
from tests.fake_clock import FakeClock

_TOLERANCE_S = 5.0


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("dv-restart-gate", "deploy restart gate test", root=tmp_path / "ws")


def test_restart_gate_terms_defers_past_restart_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    before = time.time()
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    after = time.time()
    window = deploy_verify_mod.restart_window_s()
    assert eligible_at is not None
    assert before + window - _TOLERANCE_S <= eligible_at <= after + window + _TOLERANCE_S
    assert "PR #42" in reason
    assert f"{int(window)}s" in reason
    assert "restart window" in reason
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "ceiling"


def test_restart_gate_terms_ceiling_records_deferred_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe, "classify_merge", lambda pr_number: (False, "hot_paths", None)
    )
    before = time.time()
    _reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    after = time.time()
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "ceiling"
    assert record.deferred_at is not None
    assert before - _TOLERANCE_S <= record.deferred_at <= after + _TOLERANCE_S
    assert eligible_at == pytest.approx(record.deferred_at + deploy_verify_mod.restart_window_s())


def test_restart_gate_terms_immediate_in_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert instructions is None
    assert "PR #42" in reason


def test_restart_gate_terms_immediate_when_window_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(deploy_verify_mod, "restart_window_s", lambda: 0.0)
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert instructions is None
    assert reason == "post-merge deploy gate for PR #42"


def test_restart_window_is_pinned_to_ten_minutes() -> None:
    assert deploy_verify_mod.restart_window_s() == 600.0


def test_restart_deadline_is_minted_once_and_carried_across_re_entry(
    workspace: Workspace,
) -> None:
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    first = deploy_verify_mod._merged_step(  # noqa: SLF001
        workspace, 42, None, merged_at, "http://x"
    )
    assert first.kind == "wait"
    assert first.deadline is not None
    record = GateRecord(
        merge_sha=None, path="ceiling", deferred_at=None, deadline=first.deadline
    )
    second = deploy_verify_mod._merged_step(  # noqa: SLF001
        workspace, 42, record, merged_at, "http://x"
    )
    assert second.deadline == first.deadline


def test_restart_free_merge_never_mints_a_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", "a" * 40),
    )
    _reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.deadline is None
    assert record.race_deadline is None


def test_delayed_deploy_verify_job_claimable_only_after_eligible_at(
    tmp_path: Path,
) -> None:
    now = time.time()
    fake = FakeClock(epoch=now)
    q = Queue(db_path=tmp_path / "queue.db", clock=fake)
    try:
        _reason, eligible_at, _instructions = deploy_verify_mod.restart_gate_terms(42)
        assert eligible_at is not None
        job = q.enqueue("t", "deploy_verify", eligible_at=eligible_at)
        assert q.claim() is None
        fake.advance(eligible_at - now + 1.0)
        claimed = q.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        q.close()


def test_restart_gate_terms_restart_free_skips_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", "a" * 40),
    )
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert "PR #42" in reason
    assert "restart-free" in reason
    assert "safe_paths_only" in reason
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "restart_free"
    assert record.merge_sha == "a" * 40
    assert record.deferred_at is None


def test_restart_gate_terms_restart_free_never_consults_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", "a" * 40),
    )

    def _fail_if_called(merge_sha: str) -> bool:
        raise AssertionError(
            f"merge_is_served must not be called for {merge_sha} on the restart-free path"
        )

    monkeypatch.setattr(deploy_signal, "merge_is_served", _fail_if_called)
    _reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "restart_free"


def test_restart_gate_terms_signal_path_skips_deferral_even_when_restart_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    sha = "b" * 40
    monkeypatch.setattr(
        _deploy_restart_probe, "classify_merge", lambda pr_number: (False, "hot_paths", sha)
    )
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda merge_sha: merge_sha == sha)
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert "PR #42" in reason
    assert "verifying now" in reason
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "signal"
    assert record.merge_sha == sha
    assert record.deferred_at is None


def test_restart_gate_terms_signal_not_served_falls_back_to_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    sha = "c" * 40
    monkeypatch.setattr(
        _deploy_restart_probe, "classify_merge", lambda pr_number: (False, "hot_paths", sha)
    )
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda merge_sha: False)
    _reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is not None
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "ceiling"
    assert record.merge_sha == sha


def _fail_if_called(pr_number: int) -> tuple[bool, str, str | None]:
    raise AssertionError(f"classify_merge must not be called for PR #{pr_number} here")


def test_restart_gate_terms_dry_run_never_calls_classify_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        _fail_if_called,
    )
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert instructions is None
    assert "PR #42" in reason


def test_restart_gate_terms_window_zero_never_calls_classify_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(deploy_verify_mod, "restart_window_s", lambda: 0.0)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        _fail_if_called,
    )
    reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    assert eligible_at is None
    assert instructions is None
    assert reason == "post-merge deploy gate for PR #42"


@pytest.mark.parametrize(
    "reason",
    [
        "missing_marker",
        "unknown_marker_sha",
        "service_not_loaded",
        "hot_paths",
        "merge_sha_unavailable",
        "fetch_unavailable",
        "classifier_error",
    ],
)
def test_restart_gate_terms_fail_closed_reasons_still_defer(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (False, reason, None),
    )
    result_reason, eligible_at, instructions = deploy_verify_mod.restart_gate_terms(42)
    window = deploy_verify_mod.restart_window_s()
    assert eligible_at is not None
    assert "PR #42" in result_reason
    assert f"{int(window)}s" in result_reason
    assert "restart window" in result_reason
    assert "restart-free" not in result_reason
    assert reason in result_reason
    record = GateRecord.from_json(instructions)
    assert record is not None
    assert record.path == "ceiling"
    assert record.merge_sha is None


def test_restart_free_deploy_verify_job_claimable_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _deploy_restart_probe,
        "classify_merge",
        lambda pr_number: (True, "safe_paths_only", "a" * 40),
    )
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        _reason, eligible_at, _instructions = deploy_verify_mod.restart_gate_terms(42)
        assert eligible_at is None
        job = q.enqueue("t", "deploy_verify", eligible_at=eligible_at)
        claimed = q.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        q.close()

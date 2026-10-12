from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import merge_recovery
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.merge_recovery import MergeBlockClass
from alan.teams import (
    _pr_branch,
    _pr_merge_post_update,
    _pr_reconcile,
    _pr_state_paths,
)
from alan.teams._pr_ready import PrReadiness
from alan.teams.pr_merge import PrMergeOutcome
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result

_FORGE = DEFAULT_FORGE_TARGET
_KNOWN = "sha-known"


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _no_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)


def _outcome_of(kind: _pr_branch.BranchUpdateOutcome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: kind)


def _seed_known_marker() -> _pr_state_paths.SystemHeadMarker:
    _pr_state_paths.save_system_head(42, _KNOWN, "dirty", _FORGE)
    marker = _pr_state_paths.load_system_head(42, _FORGE)
    assert marker is not None
    return marker


def _unknown_marker(rung: str) -> None:
    marker = _pr_state_paths.load_system_head(42, _FORGE)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (None, rung, False)
    assert marker.written_at is not None


def test_behind_plus_transient_update_with_an_unread_head_leaves_an_unknown_head_marker(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "request_branch_update", api_result())
    _no_head(monkeypatch)
    ok, cls, _rec = merge_recovery._recover_behind_plus_transient(42, "task-x", _FORGE)  # noqa: SLF001
    assert (ok, cls) == (True, MergeBlockClass.BEHIND_PLUS_TRANSIENT)
    _unknown_marker("behind_plus_transient")


def test_behind_plus_transient_failed_update_leaves_the_marker_as_it_found_it(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    before = _seed_known_marker()
    always(_active_fake_forge, "request_branch_update", api_result(returncode=1, stderr="boom"))
    _no_head(monkeypatch)
    ok, _cls, _rec = merge_recovery._recover_behind_plus_transient(42, "task-x", _FORGE)  # noqa: SLF001
    assert ok is False
    after = _pr_state_paths.load_system_head(42, _FORGE)
    assert after == before and after is not None and after.written_at == before.written_at


def test_stale_measured_baseline_updated_with_an_unread_head_leaves_an_unknown_head_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _outcome_of(_pr_branch.BranchUpdateOutcome.UPDATED, monkeypatch)
    _no_head(monkeypatch)
    ok, _cls, _rec = merge_recovery._recover_stale_measured_baseline(42, "task-x", _FORGE)  # noqa: SLF001
    assert ok is True
    _unknown_marker("stale_measured_baseline")


@pytest.mark.parametrize(
    "outcome", [_pr_branch.BranchUpdateOutcome.CONFLICT, _pr_branch.BranchUpdateOutcome.FAILED]
)
def test_stale_measured_baseline_without_an_update_leaves_the_marker_as_it_found_it(
    monkeypatch: pytest.MonkeyPatch, outcome: _pr_branch.BranchUpdateOutcome
) -> None:
    before = _seed_known_marker()
    _outcome_of(outcome, monkeypatch)
    _no_head(monkeypatch)
    ok, _cls, _rec = merge_recovery._recover_stale_measured_baseline(42, "task-x", _FORGE)  # noqa: SLF001
    assert ok is False
    after = _pr_state_paths.load_system_head(42, _FORGE)
    assert after == before and after is not None and after.written_at == before.written_at


def _reconciled(post_sha: str | None) -> _pr_reconcile.ReconcileResult:
    outcome = _pr_reconcile.ReconcileOutcome.RECONCILED
    return _pr_reconcile.ReconcileResult(outcome, post_sha=post_sha)


def test_dirty_reconciled_without_a_post_sha_leaves_an_unknown_head_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_reconcile, "reconcile_branch", lambda *_a, **_kw: _reconciled(None))
    ok, cls, _rec = merge_recovery._recover_dirty(42, "task-x", _FORGE, head_sha="pre-sha")  # noqa: SLF001
    assert (ok, cls) == (True, MergeBlockClass.DIRTY)
    _unknown_marker("dirty")


def test_dirty_conflict_leaves_the_marker_as_it_found_it(monkeypatch: pytest.MonkeyPatch) -> None:
    before = _seed_known_marker()
    monkeypatch.setattr(
        _pr_reconcile, "reconcile_branch",
        lambda *_a, **_kw: _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.CONFLICT),
    )
    ok, _cls, _rec = merge_recovery._recover_dirty(42, "task-x", _FORGE, head_sha="pre-sha")  # noqa: SLF001
    assert ok is False
    after = _pr_state_paths.load_system_head(42, _FORGE)
    assert after == before and after is not None and after.written_at == before.written_at


def test_dirty_reconciled_with_a_post_sha_still_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_reconcile, "reconcile_branch", lambda *_a, **_kw: _reconciled("post-1"))
    merge_recovery._recover_dirty(42, "task-x", _FORGE, head_sha="pre-sha")  # noqa: SLF001
    marker = _pr_state_paths.load_system_head(42, _FORGE)
    assert marker is not None and marker.head_sha == "post-1"


def test_merge_time_update_with_an_unread_head_leaves_an_unknown_head_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("mt-unknown", "d", root=tmp_path / "ws")
    _no_head(monkeypatch)
    _pr_merge_post_update.record_merge_time_system_head_if_updated(
        True, ws, 42, forge_target=_FORGE
    )
    _unknown_marker("merge_time_behind")


def test_merge_time_failed_update_leaves_the_marker_as_it_found_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("mt-failed", "d", root=tmp_path / "ws")
    before = _seed_known_marker()

    def boom(*_a: object, **_kw: object) -> str:
        raise AssertionError("must not read the head when the update did not happen")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", boom)
    _pr_merge_post_update.record_merge_time_system_head_if_updated(
        False, ws, 42, forge_target=_FORGE
    )
    after = _pr_state_paths.load_system_head(42, _FORGE)
    assert after == before and after is not None and after.written_at == before.written_at


def _unknown_head_marker() -> _pr_state_paths.SystemHeadMarker:
    _pr_state_paths.save_system_head(42, None, "proactive_rebase", _FORGE)
    marker = _pr_state_paths.load_system_head(42, _FORGE)
    assert marker is not None
    return marker


@pytest.mark.parametrize("live_head", [None, "sha-1", ""])
def test_an_unknown_head_marker_never_matches_a_live_head(live_head: str | None) -> None:
    marker = _unknown_head_marker()
    assert merge_recovery._is_system_head_match(marker, live_head) is False  # noqa: SLF001


@pytest.mark.parametrize("live_head", ["sha-1", ""])
def test_an_unknown_head_marker_is_not_a_system_authored_head(live_head: str) -> None:
    _unknown_head_marker()
    assert (
        _pr_merge_post_update.is_system_authored_head(42, live_head, forge_target=_FORGE) is False
    )


def test_an_unknown_head_marker_does_not_forward_and_costs_no_head_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("fwd-unknown", "d", root=tmp_path / "ws")
    prior = Handover(
        handover_id=f"handover_{ws.task_id}_prior", handover_type="forward", task_id=ws.task_id,
        from_team="pr_merge", from_invocation_id="inv_prior", to_team_suggestions=[], iteration=1,
        timestamp_utc=datetime.now(UTC), full_synthesis="prior", pr_number=42,
    )
    _unknown_head_marker()

    def boom(*_a: object, **_kw: object) -> str:
        raise AssertionError("an unknown-head marker cannot authorise a forward")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", boom)
    outcome = PrMergeOutcome(kind="timeout", pr_number=42, reason="not settling")
    assert _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", outcome) is None


def test_an_unknown_head_marker_classifies_a_red_head_as_genuine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _unknown_head_marker()
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    readiness = PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        failing_checks=("ci/test",), pending_checks=(), failed_reasons=("failing checks: ci/test",),
        check_count=1,
    )
    ok, cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", readiness, forge_target=_FORGE
    )
    assert (ok, cls) == (False, MergeBlockClass.GENUINE)

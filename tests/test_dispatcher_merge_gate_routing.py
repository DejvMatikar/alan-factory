from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.deploy_verify import GateRecord
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import DeployWaitSignal, Handover, OpenQuestion
from alan.queue import Queue
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _build_dispatcher_with_pr_merge,
    _make_collector,
    _pr_merge_handover,
)
from tests._dispatcher_support_teams import _NOOP_TEAMS, _make_handover, _RecordingTeam
from tests._pr_branch_support import _audit_entries
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, always, api_result, gh_unreachable


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _readiness_behind_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams import _pr_ready

    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BEHIND",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=BEHIND",),
            check_count=1,
        ),
    )


def _assert_one_update_attempt_audited(
    forge: ForgeScript, tmp_path: Path, ws: Workspace, action: str,
) -> None:
    assert forge.calls("request_branch_update") == [
        ((99,), {"forge_target": DEFAULT_FORGE_TARGET}),
    ]
    rows = [e for e in _audit_entries(tmp_path) if str(e["action"]).startswith("pr_update_branch_")]
    assert [(e["action"], e["task_id"], e["pr_number"]) for e in rows] == [
        (action, ws.task_id, 99),
    ]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _isolated_pr_state_root(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.isolate_pr_state_root(monkeypatch)


def test_pr_merge_gate_routes_to_implementation_fix_when_blocked(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-blocked", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert recording.called == 0
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams


def test_pr_merge_gate_implementation_fix_route_carries_remediation_brief(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-brief", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    job = next(j for j in queue.list_jobs() if j.team == "implementation_fix")
    assert job.instructions is not None
    assert "genuine" in job.instructions
    assert "ci/test" in job.instructions


def test_pr_merge_gate_dirty_exhausted_ladder_routes_to_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-dirty", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="DIRTY",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",),
        ),
    )
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, None),
    )

    def boom(_p: int, _f: object) -> int:
        raise AssertionError("an exhausted reconcile ladder must not spend a merge-retry attempt")

    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams


def test_pr_merge_gate_auto_updates_branch_when_behind_only(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript
) -> None:
    from alan.teams import _pr_branch

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-behind-only-ok", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _readiness_behind_only(monkeypatch)
    always(forge.fake, "request_branch_update", api_result())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    _assert_one_update_attempt_audited(forge, tmp_path, ws, "pr_update_branch_ok")
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_falls_through_when_auto_update_fails(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript
) -> None:
    ws = workspace_mod.create("pr-gate-behind-update-fail", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _readiness_behind_only(monkeypatch)
    always(forge.fake, "request_branch_update", api_result(returncode=1, stderr="boom"))
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    _assert_one_update_attempt_audited(forge, tmp_path, ws, "pr_update_branch_failed")
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams


def test_pr_merge_gate_falls_through_when_auto_update_conflicts(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript
) -> None:
    ws = workspace_mod.create("pr-gate-behind-update-conflict", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _readiness_behind_only(monkeypatch)
    always(forge.fake, "request_branch_update", api_result(returncode=1, stderr="merge conflict"))
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    _assert_one_update_attempt_audited(forge, tmp_path, ws, "pr_update_branch_failed")
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams


def test_pr_merge_gate_skips_auto_update_when_other_blockers(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-behind-plus-other", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BEHIND",
            review_decision="CHANGES_REQUESTED",
            failed_reasons=(
                "mergeStateStatus=BEHIND",
                "review decision CHANGES_REQUESTED",
            ),
            check_count=1,
        ),
    )
    always(
        forge.fake, "request_branch_update",
        AssertionError("must not update the branch alongside other blockers"),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert forge.calls("request_branch_update") == []
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]


def test_pr_merge_gate_routes_to_implementation_fix_on_failing_checks(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-failing", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("failing checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]


def test_pr_merge_gate_routes_to_pr_merge_retry_on_changes_requested_no_stale_review(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-changes", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",),
            check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    retry_job = next(j for j in queue.list_jobs() if j.team == "pr_merge")
    assert retry_job.eligible_at is not None and retry_job.eligible_at > before
    assert recording.called == 0


def test_pr_merge_gate_lets_pr_merge_poll_on_pending_checks(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-pending", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            pending_checks=("ci/test",),
            failed_reasons=("pending checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams
    assert recording.called == 0


def test_pr_merge_gate_rewrite_still_subject_to_loop_guard(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_ready
    ws = workspace_mod.create(
        "pr-gate-spiral", "d", root=root
    )
    chain_id = f"handover_{ws.task_id}_pr"
    for _ in range(REVISIT_CAP + 1):
        ws = workspace_mod.increment_revisit_count(ws, "implementation_fix", chain_id)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert recording.called == 0
    assert all(j.team != "implementation_fix" for j in queue.list_jobs())


def test_pr_merge_gate_genuine_block_class_falls_back_to_await_human_without_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-no-fix-team", "d", root=root)
    recording = _RecordingTeam()
    teams: dict[str, Team] = {"pr_merge": recording}
    d = Dispatcher(queue, teams, workspaces_root=root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "genuine"
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert all(j.team != "pr_merge" for j in queue.list_jobs())


def test_pr_merge_gate_allows_dispatch_when_ready(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_freshness, _pr_ready

    ws = workspace_mod.create("pr-gate-ready", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=True,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
        ),
    )
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: False)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams


def test_pr_merge_gate_permissive_on_transient_gh_failure(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-transient", "d", root=root)
    gh_unreachable()
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.ReadinessUnavailable("gh not installed"),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams


def test_pr_merge_gate_unavailable_readiness_with_a_stale_baseline_still_enqueues_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_freshness, _pr_ready

    ws = workspace_mod.create("pr-gate-unavailable-stale", "d", root=root)
    gh_unreachable()
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.ReadinessUnavailable("gh not installed"),
    )
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_readiness_forge_rate_limit_passes_decision_through(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _forge_errors, _pr_ready

    ws = workspace_mod.create("pr-gate-forge-rate-limit", "d", root=root)
    gh_unreachable()
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)

    def boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeRateLimited("rate limited", 0.0)

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_readiness_forge_transient_passes_decision_through(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _forge_errors, _pr_ready

    ws = workspace_mod.create("pr-gate-forge-transient", "d", root=root)
    gh_unreachable()
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)

    def boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeTransientError("gh unavailable")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_skipped_when_pr_number_missing(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-no-pr", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _pr_ready.PrReadiness(
            ok=False, merge_state_status="", review_decision="",
        )

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", fake)
    handover = _pr_merge_handover(ws.task_id, pr_number=None)
    d._apply_router_decision(ws, handover)  # noqa: SLF001
    assert called["n"] == 0


def test_pr_merge_gate_auto_updates_when_behind_plus_pending_checks(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript
) -> None:
    from alan.teams import _pr_branch, _pr_ready

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-behind-pending", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BEHIND",
            review_decision="APPROVED",
            pending_checks=("ci/test",),
            failed_reasons=(
                "mergeStateStatus=BEHIND",
                "pending checks: ci/test",
            ),
        ),
    )
    always(forge.fake, "request_branch_update", api_result())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert [args[0] for args, _kw in forge.calls("request_branch_update")] == [99]
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_post_update_red_routes_to_implementation_fix(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_ready, _pr_state_paths

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-post-update-red", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _pr_state_paths.save_system_head(99, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("failing checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "post_update_red"
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_genuine_when_head_sha_does_not_match_marker(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_ready, _pr_state_paths

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-post-update-stale", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _pr_state_paths.save_system_head(99, "sha-old", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("failing checks: ci/test",),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "genuine"
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_sonar_genuine_routes_to_impl_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready, _pr_sonar

    ws = workspace_mod.create("pr-gate-sonar-genuine", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("SonarCloud Code Analysis",),
            failed_reasons=("failing checks: SonarCloud Code Analysis",),
        ),
    )
    monkeypatch.setattr(_pr_sonar, "list_sonarcloud_annotations", lambda _p, **_kw: ())
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams


def test_pr_merge_success_completes_workspace(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("pr-merge-done", "d", root=root)

    class TerminalPrMergeTeam:
        name = "pr_merge"
        supports_resume = False

        def plan_workflow(
            self, _workspace: Workspace, _prior: Handover | None
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id=f"handover_{workspace.task_id}_terminal",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="pr_merge",
                from_invocation_id=inv_id,
                to_team_suggestions=[],
                iteration=2,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="PR merged",
            )

    from alan.teams import TEAMS as REAL_TEAMS
    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["pr_merge"] = TerminalPrMergeTeam()
    d = Dispatcher(queue, teams, workspaces_root=root)
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED
    assert all(j.team != "retrospective" for j in queue.list_jobs())


def test_pr_merge_gate_routes_to_pr_merge_when_blocked_with_pending_checks(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-blocked-pending", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            pending_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "pending checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams
    assert recording.called == 0


def test_pr_merge_gate_routes_to_impl_fix_when_blocked_with_failing_checks(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-blocked-failing", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams
    assert recording.called == 0


def test_pr_merge_gate_no_op_for_non_pr_merge_target(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-other", "d", root=root)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _pr_ready.PrReadiness(
            ok=False, merge_state_status="", review_decision="",
        )

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", fake)
    d._apply_router_decision(ws, _make_handover(ws.task_id))  # noqa: SLF001
    assert called["n"] == 0


def test_pr_merge_gate_await_human_persists_block_class_to_meta_and_event(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("mg-block-class", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    events, collect = _make_collector()
    d.pubsub.subscribe(ws.task_id, collect)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",),
            check_count=1,
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "stale_review"
    await_events = [e for e in events if e.kind == "router_await_human"]
    assert len(await_events) == 1
    assert await_events[0].payload["block_class"] == "stale_review"
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    blocker_kinds = {b.kind for b in reloaded.meta.blockers}
    assert blocker_kinds == {"failed_reason", "review_decision", "merge_state_status", "reason"}
    review_blocker = next(b for b in reloaded.meta.blockers if b.kind == "review_decision")
    assert review_blocker.name == "CHANGES_REQUESTED"
    unblock_blocker = next(b for b in reloaded.meta.blockers if b.kind == "reason")
    assert unblock_blocker.instruction is not None
    assert "re-dispatch pr_merge" in unblock_blocker.instruction


def test_pr_merge_gate_impl_fix_route_persists_block_class_to_meta(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-block-class-fix", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    events, collect = _make_collector()
    d.pubsub.subscribe(ws.task_id, collect)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "genuine"
    enqueue_events = [e for e in events if e.kind == "router_enqueue"]
    assert len(enqueue_events) == 1
    assert enqueue_events[0].payload["next_team"] == "implementation_fix"
    assert enqueue_events[0].payload["block_class"] == "genuine"


def test_stale_merge_block_class_cleared_when_loop_guard_escalates(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.park import ParkReason
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-stale-escalate", "d", root=root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ACTIVE, merge_block_class="stale_review"
    )
    chain_id = f"handover_{ws.task_id}_pr"
    for _ in range(REVISIT_CAP + 1):
        ws = workspace_mod.increment_revisit_count(ws, "implementation_fix", chain_id)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert reloaded.meta.merge_block_class is None
    assert reloaded.meta.park_reason is ParkReason.ESCALATED
    assert recording.called == 0


def test_stale_merge_block_class_cleared_when_parked_for_human_question(
    queue: Queue, root: Path
) -> None:
    from alan.park import ParkReason

    ws = workspace_mod.create("pr-gate-stale-question", "d", root=root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ACTIVE, merge_block_class="dirty"
    )
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    handover = _make_handover(ws.task_id).model_copy(
        update={
            "open_questions": [
                OpenQuestion(
                    id="q1", question="which approach?", resolver_role="human", blocking=True
                )
            ]
        }
    )
    d._apply_router_decision(ws, handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class is None
    assert reloaded.meta.park_reason is ParkReason.QUESTION


def test_stale_merge_block_class_is_overwritten_not_left_by_a_genuine_block(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.park import ParkReason
    from alan.teams import _pr_ready

    ws = workspace_mod.create("pr-gate-stale-overwrite", "d", root=root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ACTIVE, merge_block_class="stale_review"
    )
    recording = _RecordingTeam()
    teams: dict[str, Team] = {"pr_merge": recording}
    d = Dispatcher(queue, teams, workspaces_root=root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=("ci/test",),
            failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "genuine"
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def _deploy_wait_handover(
    task_id: str, *, chain_id: str | None = None, pr_number: int = 42
) -> Handover:
    hid = f"handover_{task_id}_dv"
    return Handover(
        handover_id=hid,
        handover_chain_id=chain_id if chain_id is not None else hid,
        handover_type="terminal",
        task_id=task_id,
        from_team="deploy_verify",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"PR #{pr_number}: awaiting the deploy restart signal",
        pr_number=pr_number,
        deploy_wait=DeployWaitSignal(
            merge_sha="a" * 40,
            deadline=time.time() + 5400.0,
            race_deadline=None,
            reason=f"PR #{pr_number}: awaiting the deploy restart signal",
        ),
    )


def test_deploy_wait_signal_re_enqueues_deploy_verify(
    queue: Queue, root: Path
) -> None:
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("deploy-wait-reenqueue", "d", root=root)
    d = Dispatcher(queue, dict(REAL_TEAMS), workspaces_root=root)
    d._apply_router_decision(ws, _deploy_wait_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "deploy_verify"]
    assert len(jobs) == 1
    assert jobs[0].eligible_at is not None
    record = GateRecord.from_json(jobs[0].instructions)
    assert record is not None
    assert record.path == "awaiting_live"
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.ESCALATED


def test_repeated_deploy_wait_re_entries_never_trip_the_loop_guard(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("deploy-wait-no-spiral", "d", root=root)
    handover = _deploy_wait_handover(ws.task_id)
    for _ in range(REVISIT_CAP + 3):
        ws = workspace_mod.increment_revisit_count(
            ws, "deploy_verify", handover.handover_chain_id
        )
    d = Dispatcher(queue, dict(REAL_TEAMS), workspaces_root=root)
    d._apply_router_decision(ws, handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.ESCALATED
    assert [j for j in queue.list_jobs() if j.team == "deploy_verify"]
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    if audit_file.exists():
        assert "cap_exhausted_no_spiral" not in audit_file.read_text()

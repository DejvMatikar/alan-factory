from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import alan.merge_gate as merge_brief_mod
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _FLAKY_GATE_NAME,
    _build_dispatcher_with_pr_merge,
    _monkeypatch_transient_flake_recovery,
    _monkeypatch_unready_readiness,
    _pr_merge_handover,
)
from tests._dispatcher_support_teams import _NOOP_TEAMS, _make_handover


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


def test_pr_merge_gate_stale_review_dismiss_succeeds_routes_to_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_post, _pr_ready

    ws = workspace_mod.create("pr-gate-stale-ok", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
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
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: True)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_pr_merge_gate_stale_review_dismiss_fails_retries_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-stale-fail", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
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
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]


def test_pr_merge_gate_pre_existing_red_retries_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-pre-existing", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/flaky",),
            quarantined_checks=("ci/flaky",),
            failed_reasons=("failing checks: ci/flaky",),
        ),
    )
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_dirty_conflict_terminal_routes_to_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_reconcile, _pr_state_paths

    ws = workspace_mod.create("pr-gate-dirty-conflict", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="DIRTY",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",),
            head_sha="deadbeef",
        ),
    )
    conflict_result = _pr_reconcile.ReconcileResult(
        _pr_reconcile.ReconcileOutcome.CONFLICT,
        pre_sha="cafefeed",
        conflicted_paths=("alan/foo.py", "alan/bar.py"),
    )
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, conflict_result),
    )

    def boom(_p: int, _f: object) -> int:
        raise AssertionError("a terminal conflict verdict must not spend a merge-retry attempt")

    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "dirty"
    jobs = queue.list_jobs()
    enqueued_teams = [j.team for j in jobs]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams
    assert recording.called == 0
    fix_job = next(j for j in jobs if j.team == "implementation_fix")
    assert fix_job.instructions is not None
    assert "alan/foo.py" in fix_job.instructions
    assert "alan/bar.py" in fix_job.instructions
    assert "cafefeed" in fix_job.instructions


def test_pr_merge_gate_dirty_raced_verdict_still_retries_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_reconcile, _pr_state_paths

    ws = workspace_mod.create("pr-gate-dirty-raced", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
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
    raced_result = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RACED)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (False, MergeBlockClass.DIRTY, raced_result),
    )
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "dirty"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_dirty_reconcile_ladder_exhausted_routes_to_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-dirty-cap", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="DIRTY",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=DIRTY",),
            head_sha="deadbeef",
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
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "dirty"
    jobs = queue.list_jobs()
    enqueued_teams = [j.team for j in jobs]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams
    assert recording.called == 0
    fix_job = next(j for j in jobs if j.team == "implementation_fix")
    assert fix_job.instructions is not None
    assert "deadbeef" in fix_job.instructions


def test_pr_merge_gate_remediation_retry_cap_exhausted_falls_back_to_await_human(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-pre-existing-cap", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=("ci/flaky",),
            quarantined_checks=("ci/flaky",),
            failed_reasons=("failing checks: ci/flaky",),
        ),
    )
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" not in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_retry_job_not_claimable_until_eligible_at(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_post, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-retry-delay", "d", root=root)
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
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert queue.claim() is None
    retry_job = next(j for j in queue.list_jobs() if j.team == "pr_merge")
    assert retry_job.eligible_at is not None
    monkeypatch.setattr(time, "time", lambda: retry_job.eligible_at + 1.0)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == retry_job.id
    assert recording.called == 0


def test_maybe_retry_pr_merge_returns_none_when_pr_number_missing(
    queue: Queue, root: Path
) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert d._maybe_retry_pr_merge(None, "reason", "stale_review", DEFAULT_FORGE_TARGET) is None  # noqa: SLF001


def test_pr_merge_gate_sonar_hotspot_reroutes_to_await_human_with_instruction(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready, _pr_sonar

    ws = workspace_mod.create("pr-gate-sonar-hotspot", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
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
    monkeypatch.setattr(
        _pr_sonar,
        "list_sonarcloud_annotations",
        lambda _p, **_kw: (
            _pr_sonar.SonarAnnotation(
                path="foo.py", line=1, title="Make sure no SSRF", rule_url=""
            ),
        ),
    )
    monkeypatch.setattr(_pr_sonar, "sonar_ack_hotspots", lambda _p, _t: False)
    sonar_target = DEFAULT_FORGE_TARGET.model_copy(
        update={"authoritative_checks": frozenset({"SonarCloud Code Analysis"})}
    )
    monkeypatch.setattr(merge_brief_mod, "forge_target_for_ws", lambda _ws: sonar_target)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" not in enqueued_teams
    assert "pr_merge" not in enqueued_teams
    assert recording.called == 0
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "sonar_hotspot"
    assert any(b.instruction is not None for b in reloaded.meta.blockers)


def test_pr_merge_gate_transient_flake_gate_reruns_exhausted_retries_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-flake-exhausted", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "transient_flake"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]
    assert recording.called == 0


def test_pr_merge_gate_transient_flake_remediation_retry_cap_exhausted_await_human(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-flake-cap", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "transient_flake"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" not in [j.team for j in queue.list_jobs()]
    assert recording.called == 0
    assert any(b.kind == "reason" and b.name == "e2e_detail_unavailable"
               for b in reloaded.meta.blockers)


def test_enrich_transient_flake_blockers_noop_when_pr_number_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.merge_recovery import MergeBlockClass
    from alan.park import Blocker
    from alan.teams import _pr_branch

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("must not fetch e2e tests without a pr_number")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", boom)
    blockers: list[Blocker] = []
    merge_brief_mod.enrich_transient_flake_blockers(
        blockers, MergeBlockClass.TRANSIENT_FLAKE, None, DEFAULT_FORGE_TARGET,
    )
    assert blockers == []


def test_enrich_transient_flake_blockers_noop_for_non_flake_block_class() -> None:
    from alan.merge_recovery import MergeBlockClass
    from alan.park import Blocker

    blockers: list[Blocker] = []
    merge_brief_mod.enrich_transient_flake_blockers(
        blockers, MergeBlockClass.GENUINE, 42, DEFAULT_FORGE_TARGET,
    )
    assert blockers == []


def test_pr_merge_gate_transient_flake_exhausted_names_failing_tests_in_blockers(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import e2e_failures
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-flake-named", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: REVISIT_CAP)
    monkeypatch.setattr(
        _pr_branch,
        "fetch_failing_e2e_tests",
        lambda *_a, **_kw: (
            e2e_failures.FailingE2ETest(
                spec="e2e/explore-chat.spec.ts", title="Explore chat renders"
            ),
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    failing_test_blockers = [b for b in reloaded.meta.blockers if b.kind == "failing_test"]
    assert len(failing_test_blockers) == 1
    assert failing_test_blockers[0].name == "e2e/explore-chat.spec.ts"
    assert failing_test_blockers[0].detail == "Explore chat renders"
    assert not any(b.name == "e2e_detail_unavailable" for b in reloaded.meta.blockers)
    assert recording.called == 0


def test_pr_merge_gate_transient_flake_rerun_succeeds_repolls_pr_merge(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    ws = workspace_mod.create("pr-gate-flake-rerun", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_kw: 1)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert recording.called == 0
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams


def test_recovered_transient_flake_requeues_pr_merge_with_reset_delay(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("pr-gate-flake-reset-delay", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    _monkeypatch_transient_flake_recovery(monkeypatch)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0
    assert recording.called == 0


def test_recovered_transient_flake_delay_does_not_bump_remediation_counter(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_state_paths

    ws = workspace_mod.create("pr-gate-flake-delay-no-bump", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _monkeypatch_transient_flake_recovery(monkeypatch)
    bumps: list[int] = []

    def _record_bump(pr_number: int, _forge: object) -> int:
        bumps.append(pr_number)
        return 1

    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", _record_bump)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert bumps == []
    assert [j.team for j in queue.list_jobs()] == ["pr_merge"]


def test_reset_delay_honors_env_override(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("pr-gate-flake-delay-env", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    _monkeypatch_transient_flake_recovery(monkeypatch)
    monkeypatch.setenv("ALAN_PR_MERGE_REMEDIATION_RETRY_DELAY_S", "5")
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert before + 4.0 <= jobs[0].eligible_at <= before + 30.0


def test_recovered_sonar_hotspot_requeues_with_reset_delay(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass

    ws = workspace_mod.create("pr-gate-sonar-reset-delay", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.SONAR_HOTSPOT, None),
    )
    _monkeypatch_unready_readiness(monkeypatch)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0


def test_recovered_dirty_reconciled_requeues_with_reset_delay(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass
    from alan.teams import _pr_reconcile

    ws = workspace_mod.create("pr-gate-dirty-reset-delay", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    reconciled_result = _pr_reconcile.ReconcileResult(_pr_reconcile.ReconcileOutcome.RECONCILED)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.DIRTY, reconciled_result),
    )
    _monkeypatch_unready_readiness(monkeypatch)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0


def test_recovered_behind_plus_transient_requeues_with_reset_delay(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import merge_recovery
    from alan.merge_recovery import MergeBlockClass

    ws = workspace_mod.create("pr-gate-behind-reset-delay", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        merge_recovery,
        "attempt_mechanical_recovery",
        lambda *_a, **_kw: (True, MergeBlockClass.BEHIND_PLUS_TRANSIENT, None),
    )
    _monkeypatch_unready_readiness(monkeypatch)
    before = time.time()
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "pr_merge"]
    assert jobs and jobs[0].eligible_at is not None
    assert jobs[0].eligible_at >= before + 80.0


def test_pr_merge_gate_survivor_evidence_routes_genuine_to_implementation_fix(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready

    ws = workspace_mod.create("pr-gate-survivor-genuine", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1"
    )
    monkeypatch.setattr(
        _pr_flake_ledger,
        "load_gate_survived_checks",
        lambda *_a, **_kw: {_FLAKY_GATE_NAME: "sha-1"},
    )

    def _no_rerun(*_a: object, **_kw: object) -> bool:
        raise AssertionError("proven-deterministic failure must not be rerun")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", _no_rerun)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "implementation_fix" in enqueued_teams
    assert "pr_merge" not in enqueued_teams
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "genuine"


def test_verification_flaky_only_reroute_inherits_shared_gate_rerun_budget(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-flaky-reroute-shared-budget", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    _pr_state_paths.bump_gate_rerun_count(99, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams
    assert "verification" not in enqueued_teams
    assert recording.called == 0
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE
    assert _pr_state_paths.load_gate_rerun_count(99, DEFAULT_FORGE_TARGET) == 2


def test_verification_flaky_only_reroute_budget_exhausted_retries_bounded_not_fresh(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("pr-gate-flaky-reroute-budget-exhausted", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    _pr_state_paths.bump_gate_rerun_count(99, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    _pr_state_paths.bump_gate_rerun_count(99, DEFAULT_FORGE_TARGET, head_sha="sha-1")
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("rerun must not fire — the shared budget is already spent")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", boom)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == "transient_flake"
    assert "implementation_fix" not in [j.team for j in queue.list_jobs()]
    assert "pr_merge" in [j.team for j in queue.list_jobs()]
    assert "verification" not in [j.team for j in queue.list_jobs()]
    assert recording.called == 0
    assert _pr_state_paths.load_gate_rerun_count(99, DEFAULT_FORGE_TARGET) == 2


def test_stale_merge_block_class_survives_advance_into_fix_leg(
    queue: Queue, root: Path
) -> None:
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("pr-fix-leg-advance", "d", root=root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ESCALATED, merge_block_class="stale_review"
    )
    d = Dispatcher(queue, dict(REAL_TEAMS), workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="verification").model_copy(
        update={"to_team_suggestions": ["implementation_fix"]}
    )
    d._apply_router_decision(ws, handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE
    assert reloaded.meta.merge_block_class == "stale_review"
    assert "implementation_fix" in [j.team for j in queue.list_jobs()]


def test_merge_gate_remediation_brief_prompt_carries_scope_override_authority(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from alan import merge_recovery
    from alan import park as park_mod
    from alan.handover import Handover
    from alan.teams import _fix_prompt, _pr_handover
    from alan.teams import implementation_fix as fix_mod
    from alan.workspace import create

    ws = create("merge-gate-brief-task", "d", root=tmp_path / "ws")
    prior = Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="s",
        pr_number=42,
    )
    blockers = (park_mod.Blocker(kind="failing_check", name="ci/flaky", detail="flaked"),)
    brief = merge_brief_mod.render_remediation_instructions(
        merge_recovery.MergeBlockClass.GENUINE, "failing checks: ci/flaky", blockers
    )
    assert "not a re-derivation of the prior approving review" in brief

    prompt = _pr_handover.compose_user_text(
        ws,
        prior,
        mid_blocks=lambda p: [],
        config=fix_mod._TEAM_CONFIG,  # noqa: SLF001
        instructions=brief,
    )

    assert brief in prompt
    instructions_idx = prompt.index("## User dispatch instructions")
    forbidden_idx = prompt.index("## Forbidden actions")
    authority_idx = prompt.index("outranks")
    assert instructions_idx < authority_idx < forbidden_idx

    from alan.teams.implementation_fix import _SYSTEM_PROMPT

    assert _fix_prompt.SCOPE_DEFERENCE_NOTICE in _SYSTEM_PROMPT


def test_stale_merge_block_class_survives_escalation_rollback_after_advance_failure(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("pr-fix-leg-rollback", "d", root=root)
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ESCALATED, merge_block_class="stale_review"
    )
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="ghost_team", reason="synthetic")
    with pytest.raises(KeyError):
        d._apply_router_decision(ws, handover, decision=decision)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert reloaded.meta.merge_block_class == "stale_review"

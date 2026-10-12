from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET, Posture
from alan.merge_gate import MergeGateBlocked
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.router import TEAM_TRANSITION_POLICY, RouterDecision
from alan.teams import TEAMS, _pr_state_paths
from alan.teams._pr_ready import PrReadiness
from alan.teams._pr_reconcile import ReconcileOutcome, ReconcileResult
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _pr_merge_handover

_CONFLICT_BRANCH = "teams/conflict-fix-routing-inv_1"


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


def _dirty_readiness(**overrides: object) -> PrReadiness:
    base: dict[str, object] = dict(
        ok=False, merge_state_status="DIRTY", review_decision="APPROVED",
        failed_reasons=("mergeStateStatus=DIRTY",), head_sha="deadbeef",
        head_ref_name=_CONFLICT_BRANCH,
    )
    base.update(overrides)
    return PrReadiness(**base)  # type: ignore[arg-type]


def _blocked(
    readiness: PrReadiness, block_class: MergeBlockClass, rec: ReconcileResult | None = None,
) -> MergeGateBlocked:
    return MergeGateBlocked(readiness, block_class, rec, DEFAULT_FORGE_TARGET)


def _ws(root: Path, transition_graph: dict[str, list[str]] | None = None) -> Workspace:
    ws = workspace_mod.create("conflict fix routing", "d", root=root)
    graph = transition_graph if transition_graph is not None else dict(TEAM_TRANSITION_POLICY)
    snapshot = EngineSnapshot(
        posture=Posture.autonomous, transition_graph={k: list(v) for k, v in graph.items()},
    )
    return stamp_engine_snapshot(ws, snapshot)


def test_dirty_exhausted_reconcile_ladder_routes_to_implementation_fix(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY), dict(TEAMS),
    )
    assert result.kind == "enqueue"
    assert result.team == "implementation_fix"
    assert result.instructions is not None
    assert "deadbeef" in result.instructions
    assert (
        f"--force-with-lease={_CONFLICT_BRANCH}:deadbeef origin {_CONFLICT_BRANCH}"
        in result.instructions
    )


def test_dirty_conflict_push_constraint_names_no_other_branch_like_reference(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY), dict(TEAMS),
    )
    assert result.instructions is not None
    line = next(
        line for line in result.instructions.splitlines() if "--force-with-lease" in line
    )
    assert line.count(_CONFLICT_BRANCH) == 2
    assert "main" not in line
    assert "origin/" not in line


def test_dirty_terminal_conflict_verdict_routes_to_implementation_fix(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    rec = ReconcileResult(
        ReconcileOutcome.CONFLICT, pre_sha="cafefeed", conflicted_paths=("alan/foo.py",),
    )
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY, rec), dict(TEAMS),
    )
    assert result.kind == "enqueue"
    assert result.team == "implementation_fix"
    assert result.instructions is not None
    assert "alan/foo.py" in result.instructions
    assert "cafefeed" in result.instructions


def test_dirty_terminal_conflict_posture_admits_but_roster_excludes_awaits_human(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root, transition_graph={"pr_merge": []})
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    rec = ReconcileResult(
        ReconcileOutcome.CONFLICT, pre_sha="cafefeed", conflicted_paths=("alan/foo.py",),
    )
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY, rec), dict(TEAMS),
    )
    assert result.kind == "await_human"


@pytest.mark.parametrize(
    "outcome", [ReconcileOutcome.RACED, ReconcileOutcome.TRANSPORT_FAILED],
)
def test_dirty_non_terminal_reconcile_outcomes_still_take_bounded_retry(
    queue: Queue, root: Path, outcome: ReconcileOutcome, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    rec = ReconcileResult(outcome)
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY, rec), dict(TEAMS),
    )
    assert result.kind == "enqueue"
    assert result.team == "pr_merge"


def test_dirty_posture_withholding_implementation_fix_awaits_human(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    rec = ReconcileResult(ReconcileOutcome.CONFLICT, conflicted_paths=("alan/foo.py",))
    effective = {k: v for k, v in TEAMS.items() if k != "implementation_fix"}
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(_dirty_readiness(), MergeBlockClass.DIRTY, rec), effective,
    )
    assert result.kind == "await_human"


def test_closing_links_missing_routes_to_implementation_fix(
    queue: Queue, root: Path,
) -> None:
    d = Dispatcher(queue, dict(TEAMS), workspaces_root=root)
    ws = _ws(root)
    ho = _pr_merge_handover("t1")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    readiness = PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        failed_reasons=("closing_links_missing #42",),
    )
    result = d._pr_merge_remediation(  # noqa: SLF001
        ws, ho, decision, _blocked(readiness, MergeBlockClass.CLOSING_LINKS_MISSING), dict(TEAMS),
    )
    assert result.kind == "enqueue"
    assert result.team == "implementation_fix"

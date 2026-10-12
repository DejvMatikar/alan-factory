from __future__ import annotations

import dataclasses
import weakref
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from alan import unblock_recipes as recipes_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover, OpenQuestion, save_handover
from alan.loop_guard import SpiralKind
from alan.park import Blocker, BlockerKind, ParkReason, synthetic_blocker
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams._base import TeamEvent
from alan.teams._verdict import DIFF_FINDINGS_CRITICAL_PREFIX
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_CHECKS_PREFIX,
    GATE_ACTIONABLE_YES,
    GATE_BLOCK_MARKER,
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Workspace, create, increment_revisit_count
from tests import _unblock_support as support

_PREFIX = recipes_mod.EXTERNAL_GATE_SPIRAL_PREFIX
_KIND = recipes_mod.EXTERNAL_GATE_SPIRAL_KIND
_BY_NAME = {recipe.name: recipe for recipe in recipes_mod.RECIPES}
_RECIPE = _BY_NAME["external_gate_spiral"]
_NO_COMMITS_SYNTHESIS = recipes_mod.NO_COMMITS_PREFIX + " — escalating. Worker synthesis:\nnone"
_UNCHANGED_TAIL = (
    "unchanged actionable gate checks across 3 verification passes: alpha-check, beta-check"
)
_MARKER_TAIL = f"verifier output contains '{GATE_BLOCK_MARKER}' with 0 critical findings"
_OTHER_KINDS = sorted(set(get_args(SpiralKind)) - {_KIND})


@pytest.fixture(autouse=True)
def _isolated_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", support.VERIFIER)


def _park(
    root: Path,
    detail: str = _PREFIX + _UNCHANGED_TAIL,
    *,
    name: str = "reason",
    status: Status = Status.ESCALATED,
    park_reason: ParkReason = ParkReason.ESCALATED,
) -> Workspace:
    return support.parked_ws(
        root,
        status=status,
        park_reason=park_reason,
        blockers=[Blocker(kind="reason", name=name, detail=detail)],
    )


def _context(
    ws: Workspace,
    *,
    pr_number: int | None = 123,
    questions: list[OpenQuestion] | None = None,
    **overrides: object,
) -> recipes_mod.RecipeContext:
    latest = support.handover(
        ws, from_team="verification", handover_type="escalation", pr_number=pr_number,
        questions=questions,
    )
    return support.context(ws, latest=latest, pr_number=pr_number, **overrides)  # type: ignore[arg-type]


def _ctx_for_detail(root: Path, detail: str) -> recipes_mod.RecipeContext:
    return _context(_park(root, detail))


class _StubTeam:
    supports_resume = False

    def __init__(self, name: str) -> None:
        self.name = name

    def plan_workflow(self, _ws: Workspace, _prior: Handover | None) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        raise AssertionError("the loop guard never runs a team")


def _dispatcher(root: Path) -> Dispatcher:
    root.mkdir(parents=True, exist_ok=True)
    queue = Queue(db_path=root / "queue.db")
    dispatcher = Dispatcher(
        queue=queue,
        teams={
            "verification": _StubTeam("verification"),
            "implementation_fix": _StubTeam("implementation_fix"),
        },
        pubsub=PubSub(),
        workspaces_root=root / "workspaces",
    )
    weakref.finalize(dispatcher, queue.close)
    return dispatcher


def _spiral_handover(
    ws: Workspace, *, team: str, suffix: str, chain_id: str, synthesis: str
) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_{suffix}",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team=team,
        from_invocation_id=f"inv_{suffix}",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
    )


def _gate_synthesis(*, checks: tuple[str, ...] = ()) -> str:
    lines = [GATE_BLOCK_MARKER]
    if checks:
        lines += [GATE_ACTIONABLE_YES, GATE_ACTIONABLE_CHECKS_PREFIX + " | ".join(checks)]
    lines.append(f"{DIFF_FINDINGS_CRITICAL_PREFIX} 0")
    return "\n".join(lines)


def _guard_reason(
    ws: Workspace, dispatcher: Dispatcher, handover: Handover, next_team: str
) -> str:
    decision = dispatcher._enforce_loop_guard(
        ws, handover, RouterDecision(kind="enqueue", team=next_team)
    )
    assert decision.kind == "escalate"
    assert decision.reason is not None
    return decision.reason


def _marker_producer_reason(root: Path) -> str:
    ws = create("marker-producer", "d", root=root / "workspaces")
    chain_id = f"handover_{ws.task_id}_chain"
    save_handover(
        ws,
        _spiral_handover(
            ws, team="verification", suffix="v1", chain_id=chain_id,
            synthesis=_gate_synthesis(),
        ),
    )
    impl_fix = _spiral_handover(
        ws, team="implementation_fix", suffix="f1", chain_id=chain_id,
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    return _guard_reason(ws, _dispatcher(root), impl_fix, "verification")


def _unchanged_checks_producer_reason(root: Path) -> str:
    ws = create("unchanged-producer", "d", root=root / "workspaces")
    chain_id = f"handover_{ws.task_id}_chain"
    synthesis = _gate_synthesis(checks=("alpha-check", "beta-check"))
    for suffix in ("v1", "v2"):
        save_handover(
            ws,
            _spiral_handover(
                ws, team="verification", suffix=suffix, chain_id=chain_id, synthesis=synthesis
            ),
        )
    save_handover(
        ws,
        _spiral_handover(
            ws, team="implementation_fix", suffix="f1", chain_id=chain_id,
            synthesis=_NO_COMMITS_SYNTHESIS,
        ),
    )
    current = _spiral_handover(
        ws, team="verification", suffix="v3", chain_id=chain_id, synthesis=synthesis
    )
    return _guard_reason(ws, _dispatcher(root), current, "implementation_fix")


_PRODUCERS = [
    pytest.param(_marker_producer_reason, id="gate-marker"),
    pytest.param(_unchanged_checks_producer_reason, id="unchanged-checks"),
]


@pytest.mark.parametrize("produce", _PRODUCERS)
def test_the_detectors_park_reason_starts_with_the_recipes_prefix(
    tmp_path: Path, produce: Callable[[Path], str]
) -> None:
    assert produce(tmp_path).startswith(_PREFIX)


def test_the_two_producers_emit_different_evidence_shapes(tmp_path: Path) -> None:
    marker = _marker_producer_reason(tmp_path / "a")
    unchanged = _unchanged_checks_producer_reason(tmp_path / "b")
    assert marker == _PREFIX + _MARKER_TAIL
    assert unchanged == _PREFIX + _UNCHANGED_TAIL
    assert "alpha-check" not in marker


@pytest.mark.parametrize("produce", _PRODUCERS)
def test_a_park_built_from_the_detectors_reason_is_matched_by_this_recipe_alone(
    tmp_path: Path, produce: Callable[[Path], str]
) -> None:
    reason = produce(tmp_path / "detector")
    ws = support.parked_ws(
        tmp_path / "parked",
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=synthetic_blocker(reason),
    )
    matched = recipes_mod.match_recipes(_context(ws))
    assert [recipe.name for recipe in matched] == ["external_gate_spiral"]


def test_the_kind_is_a_member_of_the_spiral_kind_union() -> None:
    assert _KIND in get_args(SpiralKind)


def test_the_prefix_is_the_kind_qualified_outer_prefix() -> None:
    assert _PREFIX == f"spiral:{_KIND}: "


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(_UNCHANGED_TAIL, id="unchanged-checks-tail"),
        pytest.param(_MARKER_TAIL, id="gate-marker-tail-naming-no-checks"),
        pytest.param("", id="empty-tail"),
        pytest.param("x", id="single-character-tail"),
    ],
)
def test_matches_whatever_follows_the_prefix(tmp_path: Path, tail: str) -> None:
    assert _RECIPE.match(_ctx_for_detail(tmp_path, _PREFIX + tail)) is True


def test_matches_a_tail_truncated_mid_check_name(tmp_path: Path) -> None:
    names = [f"required-check-{index:03d}" for index in range(60)]
    tail = "unchanged actionable gate checks across 3 verification passes: " + ", ".join(names)
    detail = (_PREFIX + tail)[:300]
    assert len(detail) == 300
    assert detail.rsplit(", ", 1)[-1] not in names
    assert _RECIPE.match(_ctx_for_detail(tmp_path, detail)) is True


@pytest.mark.parametrize("kind", _OTHER_KINDS)
def test_rejects_every_other_spiral_kind(tmp_path: Path, kind: str) -> None:
    ctx = _ctx_for_detail(tmp_path, f"spiral:{kind}: {_UNCHANGED_TAIL}")
    assert _RECIPE.match(ctx) is False


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("spiral:", id="bare-outer-prefix"),
        pytest.param(f"spiral:{_KIND}", id="no-separator"),
        pytest.param(f"spiral:{_KIND}:", id="colon-without-space"),
        pytest.param(f"spiral:{_KIND}:{_UNCHANGED_TAIL}", id="colon-without-space-then-tail"),
        pytest.param(f"spiral:{_KIND}_x: {_UNCHANGED_TAIL}", id="longer-kind-name"),
        pytest.param(f"note: {_PREFIX}{_UNCHANGED_TAIL}", id="prefix-mid-string"),
        pytest.param(f" {_PREFIX}{_UNCHANGED_TAIL}", id="leading-space"),
        pytest.param(_PREFIX.upper(), id="upper-cased-prefix"),
        pytest.param(_UNCHANGED_TAIL, id="tail-without-prefix"),
    ],
)
def test_rejects_a_detail_that_does_not_begin_with_the_full_prefix(
    tmp_path: Path, detail: str
) -> None:
    assert _RECIPE.match(_ctx_for_detail(tmp_path, detail)) is False


def test_rejects_a_blocker_that_is_not_named_reason(tmp_path: Path) -> None:
    ws = _park(tmp_path, name="escalation")
    assert _RECIPE.match(_context(ws)) is False


@pytest.mark.parametrize("kind", ["failing_check", "pending_check", "failed_reason"])
def test_rejects_a_blocker_that_is_not_of_kind_reason(tmp_path: Path, kind: BlockerKind) -> None:
    ws = support.parked_ws(
        tmp_path,
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=[Blocker(kind=kind, name="reason", detail=_PREFIX + _UNCHANGED_TAIL)],
    )
    ctx = _context(ws)
    assert ctx.blocker is not None
    assert _RECIPE.match(ctx) is False


def test_rejects_a_park_that_is_not_escalated(tmp_path: Path) -> None:
    ws = _park(tmp_path, status=Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION)
    assert _RECIPE.match(_context(ws)) is False


def test_rejects_a_park_without_a_pull_request(tmp_path: Path) -> None:
    assert _RECIPE.match(_context(_park(tmp_path), pr_number=None)) is False


def test_rejects_a_park_with_a_blocking_question(tmp_path: Path) -> None:
    ctx = _context(_park(tmp_path), questions=[support.question()])
    assert ctx.question is not None
    assert _RECIPE.match(ctx) is False


def test_rejects_a_park_without_a_blocker(tmp_path: Path) -> None:
    ws = support.parked_ws(
        tmp_path, status=Status.ESCALATED, park_reason=ParkReason.ESCALATED, blockers=[]
    )
    ctx = _context(ws)
    assert ctx.blocker is None
    assert _RECIPE.match(ctx) is False


def test_rejects_a_park_with_two_blockers(tmp_path: Path) -> None:
    ws = support.parked_ws(
        tmp_path,
        status=Status.ESCALATED,
        park_reason=ParkReason.ESCALATED,
        blockers=[
            Blocker(kind="reason", name="reason", detail=_PREFIX + _UNCHANGED_TAIL),
            Blocker(kind="reason", name="second", detail="another"),
        ],
    )
    ctx = _context(ws)
    assert ctx.blocker is None
    assert _RECIPE.match(ctx) is False


@pytest.mark.parametrize(
    "other",
    [pytest.param(recipe, id=recipe.name) for recipe in recipes_mod.RECIPES
     if recipe.name != "external_gate_spiral"],
)
def test_no_other_recipe_claims_the_spiral_park(tmp_path: Path, other: recipes_mod.Recipe) -> None:
    assert other.match(_context(_park(tmp_path))) is False


def test_match_recipes_returns_exactly_this_recipe_for_the_spiral_park(tmp_path: Path) -> None:
    matched = recipes_mod.match_recipes(_context(_park(tmp_path)))
    assert [recipe.name for recipe in matched] == ["external_gate_spiral"]


def test_this_recipe_does_not_claim_a_merge_park(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    assert _RECIPE.match(_context(ws)) is False


def test_this_recipe_does_not_claim_a_no_commit_escalation(tmp_path: Path) -> None:
    ws = _park(tmp_path, recipes_mod.NO_COMMITS_PREFIX + " — escalating.", name="escalation")
    assert _RECIPE.match(_context(ws)) is False


def test_registry_holds_the_recipe_exactly_once_after_green_not_merging() -> None:
    names = [recipe.name for recipe in recipes_mod.RECIPES]
    assert names.count("external_gate_spiral") == 1
    assert names.index("external_gate_spiral") == names.index("green_not_merging") + 1


def test_registry_names_stay_unique() -> None:
    names = [recipe.name for recipe in recipes_mod.RECIPES]
    assert len(names) == len(set(names))


def test_the_recipe_reuses_the_green_not_merging_probe_by_reference() -> None:
    assert _RECIPE.probe is _BY_NAME["green_not_merging"].probe


def test_the_recipe_does_not_reuse_the_green_not_merging_plan_or_match() -> None:
    assert _RECIPE.plan is not _BY_NAME["green_not_merging"].plan
    assert _RECIPE.match is not _BY_NAME["green_not_merging"].match


def test_the_plan_dispatches_pr_merge_with_nothing_else(tmp_path: Path) -> None:
    plan = _RECIPE.plan(_context(_park(tmp_path)))
    assert (plan.kind, plan.team) == ("dispatch", "pr_merge")
    assert (plan.option, plan.forge_write, plan.bar_lowering) == (None, None, False)


def test_the_plan_names_its_own_shape_and_never_the_green_not_merging_one(
    tmp_path: Path,
) -> None:
    plan = _RECIPE.plan(_context(_park(tmp_path)))
    assert "external_gate_spiral" in plan.instructions
    assert "green_not_merging" not in plan.instructions


def test_the_plan_never_targets_the_parking_teams(tmp_path: Path) -> None:
    plan = _RECIPE.plan(_context(_park(tmp_path)))
    assert plan.team not in {"verification", "implementation_fix"}


def test_probe_is_ready_when_the_pull_request_is_mergeable(tmp_path: Path) -> None:
    ctx = _context(_park(tmp_path), ready=support.readiness())
    assert _RECIPE.probe(ctx) == recipes_mod.ready("the pull request is mergeable")


def test_probe_declines_while_the_named_gate_is_failing(tmp_path: Path) -> None:
    ctx = _context(_park(tmp_path), ready=support.readiness(failing_checks=("e2e",)))
    assert _RECIPE.probe(ctx) == recipes_mod.declined("checks failing: e2e")


def test_probe_waits_while_a_check_is_pending(tmp_path: Path) -> None:
    ctx = _context(_park(tmp_path), ready=support.readiness(pending_checks=("e2e",)))
    assert _RECIPE.probe(ctx) == recipes_mod.not_yet("checks pending: e2e")


def test_probe_waits_while_the_forge_is_unavailable(tmp_path: Path) -> None:
    assert _RECIPE.probe(_context(_park(tmp_path), ready=None)).state == "not_yet"


def test_probe_ignores_the_head_observed_at_the_park(tmp_path: Path) -> None:
    ctx = dataclasses.replace(
        _context(_park(tmp_path), ready=support.readiness(head_sha="moved")),
        park_head="parked",
    )
    assert _RECIPE.probe(ctx).state == "ready"


def test_probe_reads_the_live_head_on_every_call(tmp_path: Path) -> None:
    ws = _park(tmp_path)
    red = _context(ws, ready=support.readiness(head_sha="one", failing_checks=("e2e",)))
    green = _context(ws, ready=support.readiness(head_sha="two"))
    assert [_RECIPE.probe(red).state, _RECIPE.probe(green).state] == ["declined", "ready"]

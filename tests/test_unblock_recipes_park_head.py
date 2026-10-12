from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import unblock_recipes as recipes_mod
from alan.handover import ArtifactRef
from alan.teams import _declared_blocker as declared_blocker_mod
from alan.teams import _pr_state_paths as pr_state_paths_mod
from alan.workspace import Workspace
from tests import _unblock_support as support
from tests._pr_branch_support import _WIDGET_FORGE

_RECIPE = next(r for r in recipes_mod.RECIPES if r.name == "hand_resolved_declared_blocker")
_PARK_HEAD = "parkhead1"
_LIVE_HEAD = "livehead2"
_NO_COMMITS = "the park recorded no worker commits to compare the head against"
_FLEET_MOVED = "the fleet's own rebase machinery moved the head"
_HUMAN_STEP = "the head moved since the park to a commit no fleet actor marked"


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return support.parked_ws(tmp_path / "ws")


def _context(
    ws: Workspace,
    *,
    park_head: str | None = _PARK_HEAD,
    head: str = _LIVE_HEAD,
    commits: tuple[str, ...] = (),
    parked_at: datetime | None = None,
) -> recipes_mod.RecipeContext:
    question = support.question(
        question_id="declared_blocker_abcdef12",
        question_class="fix_escalation",
        text="[BLOCKING] needs a human",
        options=(
            declared_blocker_mod.HAND_RESOLVE_OPTION,
            declared_blocker_mod.BLOCKER_UNFOUNDED_OPTION,
        ),
    )
    latest = support.handover(
        ws,
        from_team="implementation_fix",
        questions=[question],
        timestamp=parked_at,
        artifacts=[ArtifactRef(path=sha, description=f"{sha} msg") for sha in commits],
    )
    ctx = support.context(ws, latest=latest, ready=support.readiness(head_sha=head))
    return replace(ctx, park_head=park_head)


def _mark_unknown_head() -> None:
    pr_state_paths_mod.save_system_head(123, None, "proactive_rebase", _WIDGET_FORGE)


def _mark_head(head: str) -> None:
    pr_state_paths_mod.save_system_head(123, head, "proactive_rebase", _WIDGET_FORGE)


def _long_ago() -> datetime:
    return datetime.now(UTC) - timedelta(hours=2)


def _in_the_future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=2)


def test_a_park_with_no_recorded_head_declines(ws: Workspace) -> None:
    ctx = _context(ws, park_head=None)
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_NO_COMMITS)


def test_an_unmoved_head_declines(ws: Workspace) -> None:
    ctx = _context(ws, park_head=_LIVE_HEAD)
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_NO_COMMITS)


def test_a_moved_head_with_no_marker_is_a_human_step(ws: Workspace) -> None:
    assert _RECIPE.probe(_context(ws)) == recipes_mod.ready(_HUMAN_STEP)


def test_a_moved_head_the_fleet_marker_names_declines(ws: Workspace) -> None:
    _mark_head(_LIVE_HEAD)
    assert _RECIPE.probe(_context(ws)) == recipes_mod.declined(_FLEET_MOVED)


def test_a_moved_head_a_stale_fleet_marker_does_not_name_is_a_human_step(ws: Workspace) -> None:
    _mark_head("olderhead")
    assert _RECIPE.probe(_context(ws)) == recipes_mod.ready(_HUMAN_STEP)


def test_an_unknown_head_marker_written_after_the_park_declines(ws: Workspace) -> None:
    _mark_unknown_head()
    assert _RECIPE.probe(_context(ws, parked_at=_long_ago())) == recipes_mod.declined(_FLEET_MOVED)


def test_an_unknown_head_marker_written_before_the_park_is_a_human_step(ws: Workspace) -> None:
    _mark_unknown_head()
    assert _RECIPE.probe(_context(ws, parked_at=_in_the_future())) == recipes_mod.ready(_HUMAN_STEP)


def test_an_unknown_head_marker_written_at_the_park_instant_declines(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    parked_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    marker = pr_state_paths_mod.SystemHeadMarker(
        head_sha=None, rung="rebase", written_at=parked_at.timestamp()
    )
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: marker)
    assert _RECIPE.probe(_context(ws, parked_at=parked_at)) == recipes_mod.declined(_FLEET_MOVED)


@pytest.mark.parametrize(("offset_s", "state"), [(1.0, "declined"), (-1.0, "ready")])
def test_a_naive_park_timestamp_is_read_as_utc(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch, offset_s: float, state: str
) -> None:
    parked_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    marker = pr_state_paths_mod.SystemHeadMarker(
        head_sha=None, rung="rebase", written_at=parked_utc.timestamp() + offset_s
    )
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: marker)
    ctx = _context(ws, parked_at=parked_utc.replace(tzinfo=None))
    assert _RECIPE.probe(ctx).state == state


def test_an_unknown_head_marker_with_no_write_time_does_not_decline(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = pr_state_paths_mod.SystemHeadMarker(head_sha=None, rung="rebase")
    monkeypatch.setattr(pr_state_paths_mod, "load_system_head", lambda *_a, **_k: marker)
    assert _RECIPE.probe(_context(ws, parked_at=_long_ago())) == recipes_mod.ready(_HUMAN_STEP)


def test_an_unknown_head_marker_does_not_count_without_a_park_time(
    ws: Workspace,
) -> None:
    _mark_unknown_head()
    ctx = replace(_context(ws), blocking=None)
    assert _RECIPE.probe(ctx) == recipes_mod.ready(_HUMAN_STEP)


def test_an_unknown_head_marker_after_the_park_declines_a_park_with_worker_commits(
    ws: Workspace,
) -> None:
    _mark_unknown_head()
    ctx = _context(ws, commits=("b412c46",), parked_at=_long_ago())
    assert _RECIPE.probe(ctx) == recipes_mod.declined(_FLEET_MOVED)


def test_an_unknown_head_marker_before_the_park_leaves_a_worker_commit_park_ready(
    ws: Workspace,
) -> None:
    _mark_unknown_head()
    ctx = _context(ws, commits=("b412c46",), parked_at=_in_the_future())
    assert _RECIPE.probe(ctx).state == "ready"


def test_a_park_with_worker_commits_ignores_the_recorded_park_head(ws: Workspace) -> None:
    ctx = _context(ws, park_head=None, commits=("b412c46",), head="b412c46abcdef")
    assert _RECIPE.probe(ctx) == recipes_mod.declined(
        "the head still matches a worker commit from the park"
    )

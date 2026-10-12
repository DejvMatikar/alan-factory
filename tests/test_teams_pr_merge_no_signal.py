from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_merge_no_signal, _pr_ready
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-no-signal-task", "gate the merge", root=tmp_path / "ws")


def test_timeout_ctx_reports_blocked_no_signal_true(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="",
        ),
    )
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 12.34, 5, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ctx == {
        "elapsed": 12.3,
        "polls": 5,
        "blocked_no_signal": True,
        "timeout_state": (
            "Recorded state at give-up: 0 failing check(s), 0 pending "
            "check(s), review decision none."
        ),
    }


def test_timeout_ctx_reports_blocked_no_signal_false(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="",
        ),
    )
    ctx = _pr_merge_no_signal.timeout_ctx(workspace, 42, 1.0, 0, forge_target=DEFAULT_FORGE_TARGET)
    assert ctx == {
        "elapsed": 1.0,
        "polls": 0,
        "blocked_no_signal": False,
        "timeout_state": (
            "Recorded state at give-up: 0 failing check(s), 0 pending "
            "check(s), review decision none."
        ),
    }


def test_timeout_recipe_shape_matched_when_blocked_no_signal() -> None:
    recipe = _pr_merge_no_signal.timeout_recipe({"blocked_no_signal": True}, "default text")
    assert recipe == _pr_merge_no_signal.BLOCKED_NO_SIGNAL_TIMEOUT_RECIPE
    assert "required status context" in recipe
    assert "none of those live in the diff" in recipe


def test_timeout_recipe_falls_back_to_default_when_absent() -> None:
    assert _pr_merge_no_signal.timeout_recipe({}, "default text") == "default text"


def test_timeout_recipe_falls_back_to_default_when_false() -> None:
    ctx = {"blocked_no_signal": False}
    assert _pr_merge_no_signal.timeout_recipe(ctx, "default text") == "default text"


_HEAD = "0123456789abcdef0123"


def _readiness(
    *,
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    review_decision: str = "CHANGES_REQUESTED",
    merge_state_status: str = "BLOCKED",
    head_sha: str = _HEAD,
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status=merge_state_status, review_decision=review_decision,
        failing_checks=failing_checks, pending_checks=pending_checks,
        head_sha=head_sha,
    )


@pytest.mark.parametrize(
    ("readiness", "expected"),
    [
        (_readiness(), True),
        (_readiness(failing_checks=("Lint",)), False),
        (_readiness(pending_checks=("Lint",)), False),
        (_readiness(review_decision="APPROVED"), False),
        (_readiness(review_decision=""), False),
        (_pr_ready.ReadinessUnavailable("gh unavailable"), False),
    ],
)
def test_settled_review_block_needs_settled_checks_and_a_standing_change_request(
    readiness: _pr_ready.ReadinessResult, expected: bool,
) -> None:
    assert _pr_merge_no_signal.settled_review_block(readiness) is expected


def test_timeout_ctx_names_the_review_decision_and_head_and_flags_the_blocker(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable", lambda *_a, **_kw: _readiness()
    )
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 630.0, 21, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ctx["timeout_blocker"] == _pr_merge_no_signal.CHANGES_REQUESTED_BLOCKER
    assert "CHANGES_REQUESTED" in ctx["timeout_state"]
    assert _HEAD[:12] in ctx["timeout_state"]
    assert _HEAD[:13] not in ctx["timeout_state"]
    assert "settl" not in ctx["timeout_state"]
    assert "not settling" not in ctx["timeout_state"]


def test_timeout_ctx_says_unknown_head_when_the_forge_reported_none(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable", lambda *_a, **_kw: _readiness(head_sha="")
    )
    ctx = _pr_merge_no_signal.timeout_ctx(workspace, 42, 1.0, 1, forge_target=DEFAULT_FORGE_TARGET)
    assert "at head unknown" in ctx["timeout_state"]


def test_timeout_ctx_does_not_collide_with_blocked_without_signal(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _readiness(merge_state_status="BLOCKED")
    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", lambda *_a, **_kw: readiness)
    ctx = _pr_merge_no_signal.timeout_ctx(workspace, 42, 1.0, 1, forge_target=DEFAULT_FORGE_TARGET)
    assert ctx["blocked_no_signal"] is False
    assert ctx["timeout_blocker"] == _pr_merge_no_signal.CHANGES_REQUESTED_BLOCKER


@pytest.mark.parametrize(
    "readiness",
    [
        _readiness(failing_checks=("Lint",)),
        _readiness(pending_checks=("Lint",)),
        _readiness(review_decision=""),
        _pr_ready.ReadinessUnavailable("gh unavailable"),
    ],
)
def test_timeout_ctx_leaves_the_blocker_unset_unless_checks_settled_on_a_change_request(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, readiness: _pr_ready.ReadinessResult,
) -> None:
    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", lambda *_a, **_kw: readiness)
    ctx = _pr_merge_no_signal.timeout_ctx(workspace, 42, 1.0, 1, forge_target=DEFAULT_FORGE_TARGET)
    assert "timeout_blocker" not in ctx


def test_timeout_recipe_returns_the_review_shaped_state_line(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable", lambda *_a, **_kw: _readiness()
    )
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 630.0, 21, forge_target=DEFAULT_FORGE_TARGET
    )
    recipe = _pr_merge_no_signal.timeout_recipe(ctx, "default text")
    assert recipe == ctx["timeout_state"]
    assert "Dismiss it on GitHub if it is stale" in recipe
    assert "re-dispatch pr_merge" in recipe

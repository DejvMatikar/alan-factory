from __future__ import annotations

from pathlib import Path

import pytest

from alan import roots
from alan.self_update import ALLOWED, Reason, Verdict, decide
from tests.self_update_fixtures import (
    Scenario,
    advance_origin,
    build_scenario,
    commit,
    gate_check,
    git,
    push_run,
    redirect_env,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def write_rejected(sha: str) -> None:
    rejected = roots.deploy_marker_root() / "self_update_rejected"
    rejected.parent.mkdir(parents=True, exist_ok=True)
    rejected.write_text(f"{sha}\n", encoding="utf-8")


def advance_twice(scenario: Scenario) -> str:
    mid = advance_origin(scenario.pair, "mid.txt")
    tip = advance_origin(scenario.pair, "tip.txt")
    scenario.fetched = tip
    return mid


def orphan_commit(repo: Path) -> str:
    git(repo, "checkout", "-q", "--orphan", "unrelated")
    git(repo, "commit", "-q", "--allow-empty", "-m", "orphan")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    return sha


def side_commit(repo: Path) -> str:
    git(repo, "checkout", "-q", "-b", "sidebranch")
    sha = commit(repo, "side.txt")
    git(repo, "checkout", "-q", "main")
    return sha


def test_pin_not_on_main_is_a_blocked_reason() -> None:
    assert Reason.PIN_NOT_ON_MAIN in ALLOWED[Verdict.BLOCKED]


def test_operator_forced_is_a_green_reason() -> None:
    assert Reason.OPERATOR_FORCED in ALLOWED[Verdict.GREEN]


def test_a_pin_that_is_not_a_commit_sha_is_blocked(scenario: Scenario) -> None:
    outcome, ctx = decide(scenario.repo, pin="not-a-sha")
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.PIN_NOT_ON_MAIN)
    assert ctx.fetched == scenario.fetched


def test_a_pin_that_does_not_exist_is_blocked(scenario: Scenario) -> None:
    outcome, _ = decide(scenario.repo, pin="0" * 40)
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.PIN_NOT_ON_MAIN)


def test_a_pin_that_does_not_descend_from_head_is_blocked(scenario: Scenario) -> None:
    orphan = orphan_commit(scenario.repo)
    outcome, _ = decide(scenario.repo, pin=orphan)
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.PIN_NOT_ON_MAIN)


def test_a_pin_that_descends_from_head_but_is_not_on_fetched_main_is_blocked(
    scenario: Scenario,
) -> None:
    side = side_commit(scenario.repo)
    outcome, _ = decide(scenario.repo, pin=side)
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.PIN_NOT_ON_MAIN)


def test_a_pin_equal_to_head_is_up_to_date(scenario: Scenario) -> None:
    outcome, ctx = decide(scenario.repo, pin=scenario.head)
    assert (outcome.verdict, outcome.reason) == (Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    assert ctx.fetched == scenario.head


def test_a_pin_between_head_and_the_fetched_tip_is_checked_for_ci_on_its_own_sha(
    scenario: Scenario,
) -> None:
    mid = advance_twice(scenario)
    scenario.set_runs(push_run(mid))
    scenario.set_checks(gate_check(mid))
    outcome, ctx = decide(scenario.repo, pin=mid)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    assert ctx.fetched == mid


def test_a_rejected_pin_is_blocked_even_though_the_tip_is_clean(scenario: Scenario) -> None:
    mid = advance_twice(scenario)
    write_rejected(mid)
    outcome, ctx = decide(scenario.repo, pin=mid)
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.REJECTED_COMMIT)
    assert ctx.fetched == mid


def test_force_skips_the_ci_gate_with_no_github_calls(scenario: Scenario) -> None:
    outcome, ctx = decide(scenario.repo, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.OPERATOR_FORCED)
    assert ctx.fetched == scenario.fetched
    assert scenario.router.calls == []


def test_force_skips_the_rejected_list(scenario: Scenario) -> None:
    write_rejected(scenario.fetched)
    outcome, _ = decide(scenario.repo, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.OPERATOR_FORCED)


def test_force_does_not_skip_the_dirty_tree_check(scenario: Scenario) -> None:
    (scenario.repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    outcome, _ = decide(scenario.repo, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.BLOCKED, Reason.DIRTY_TREE)


def test_a_forced_pinned_and_rejected_commit_still_applies(scenario: Scenario) -> None:
    mid = advance_twice(scenario)
    write_rejected(mid)
    outcome, ctx = decide(scenario.repo, pin=mid, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.OPERATOR_FORCED)
    assert ctx.fetched == mid
    assert scenario.router.calls == []

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import PrRead
from alan.teams import _forge_errors, _pr_branch, _pr_ready
from tests import _pr_branch_support as support
from tests._pr_branch_support import _audit_entries
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read, readiness_fields

_PASSING_CI = {"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}
_FLAKY_FAILURE = {"name": "ci/flaky", "conclusion": "FAILURE", "status": "COMPLETED"}


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_check_pr_merge_readiness_clean_returns_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields(
        "CLEAN", "APPROVED", rollup=[_PASSING_CI],
        reviews=[{"state": "APPROVED", "author": {"login": "x"}}],
    )
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.merge_state_status == "CLEAN"
    assert readiness.failed_reasons == ()
    assert _audit_entries(tmp_path)[-1]["action"] == "pr_readiness_check"


def test_check_pr_merge_readiness_blocked_lists_reasons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [
        {"name": "sonar", "conclusion": "FAILURE", "status": "COMPLETED"},
        {"name": "e2e", "conclusion": "", "status": "IN_PROGRESS"},
        {"context": "ctx-only", "state": "ERROR"},
        "not-a-dict",
    ]
    forge.queue("read_pr", pr_read(readiness_fields("BLOCKED", "CHANGES_REQUESTED", rollup=rollup)))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert "mergeStateStatus=BLOCKED" in readiness.failed_reasons
    assert any("CHANGES_REQUESTED" in r for r in readiness.failed_reasons)
    assert any("sonar" in r for r in readiness.failed_reasons)
    assert any("e2e" in r for r in readiness.failed_reasons)


def test_check_pr_merge_readiness_ignores_commented_reviews(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI])))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.failed_reasons == ()


def test_check_pr_merge_readiness_subprocess_failure_returncode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(returncode=1, stderr="not authorized"))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert "not authorized" in readiness.transient_reason


def test_check_pr_merge_readiness_subprocess_missing_raises_transient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", FileNotFoundError("gh missing"))
    with pytest.raises(_forge_errors.ForgeTransientError, match="gh missing"):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_check_pr_merge_readiness_subprocess_timeout_raises_transient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=1.0))
    with pytest.raises(_forge_errors.ForgeTransientError, match="timed out"):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_readiness_or_unavailable_absorbs_forge_transient_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", FileNotFoundError("gh missing"))
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert "forge unavailable" in readiness.transient_reason


def test_readiness_or_unavailable_absorbs_forge_rate_limited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(returncode=1, stderr="secondary rate limit exceeded"))
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert "forge unavailable" in readiness.transient_reason


def test_readiness_or_unavailable_returns_real_readiness_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI])))
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True


def test_check_pr_merge_readiness_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read({}))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert "unparseable JSON" in readiness.transient_reason


def test_check_pr_merge_readiness_has_hooks_treated_as_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("HAS_HOOKS", "APPROVED", rollup=[_PASSING_CI])))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True


def test_check_pr_verifier_blockers_blocked_with_pending_only_returns_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [{"name": "ci", "conclusion": "", "status": "IN_PROGRESS"}]
    forge.queue("read_pr", pr_read(readiness_fields("BLOCKED", "REVIEW_REQUIRED", rollup=rollup)))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.failed_reasons == ()


def test_check_pr_verifier_blockers_changes_requested_does_not_downgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("BLOCKED", "CHANGES_REQUESTED", rollup=[_PASSING_CI])
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.failed_reasons == ()


def test_check_pr_verifier_blockers_failing_checks_trigger_downgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [{"name": "sonar", "conclusion": "FAILURE", "status": "COMPLETED"}]
    forge.queue("read_pr", pr_read(readiness_fields("BLOCKED", "REVIEW_REQUIRED", rollup=rollup)))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert any("sonar" in r for r in readiness.failed_reasons)


def test_check_pr_verifier_blockers_dirty_triggers_downgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("DIRTY", "APPROVED")))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert any("DIRTY" in r for r in readiness.failed_reasons)


def test_check_pr_verifier_blockers_clean_returns_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI])))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True


def test_check_pr_verifier_blockers_subprocess_failure_is_transient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(returncode=1))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_check_pr_verifier_blockers_invalid_json_is_transient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read({}))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_check_pr_merge_readiness_handles_non_list_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("CLEAN", "APPROVED", statusCheckRollup="not-a-list")
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert "no check results" in readiness.failed_reasons
    assert readiness.failing_checks == ()
    assert readiness.pending_checks == ()


def test_check_pr_verifier_blockers_pre_existing_quarantined_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_FLAKY_FAILURE])))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs", lambda _t, _s, _p, **_kw: [_FLAKY_FAILURE],
    )
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.failed_reasons == ()
    assert "ci/flaky" in readiness.quarantined_checks
    entries = _audit_entries(tmp_path)
    qe = next(e for e in entries if e.get("action") == "flake_quarantine_decision")
    assert qe["quarantined"] is True


def test_check_pr_verifier_blockers_partial_quarantine_still_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [_FLAKY_FAILURE, {"name": "ci/real", "conclusion": "FAILURE", "status": "COMPLETED"}]
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=rollup)))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs",
        lambda _t, _s, _p, **_kw: [
            _FLAKY_FAILURE,
            {"name": "ci/real", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
    )
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert "ci/flaky" in readiness.quarantined_checks
    assert any("ci/real" in r for r in readiness.failed_reasons)


def test_check_pr_verifier_blockers_not_quarantined_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [{"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"}]
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=rollup)))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert readiness.quarantined_checks == ()
    assert any("ci/test" in r for r in readiness.failed_reasons)


def test_check_pr_merge_readiness_pre_existing_annotated_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("BLOCKED", "APPROVED", rollup=[_FLAKY_FAILURE])
    forge.queue("read_pr", pr_read(fields))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: "a" * 40)
    monkeypatch.setattr(
        _pr_branch, "_iter_check_runs", lambda _t, _s, _p, **_kw: [_FLAKY_FAILURE],
    )
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert any("ci/flaky" in r for r in readiness.failed_reasons)
    assert "ci/flaky" in readiness.quarantined_checks


def test_check_pr_merge_readiness_populates_passing_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    neutral = {"name": "neutral-check", "conclusion": "NEUTRAL", "status": "COMPLETED"}
    forge.queue(
        "read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI, neutral])),
    )
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.passing_checks == ("ci",)


def test_resolve_named_check_outcome_fail_or_pending_when_failing() -> None:
    r = _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        failing_checks=("x",),
    )
    assert (
        _pr_ready.resolve_named_check_outcome("x", r) is _pr_ready.CheckOutcome.FAIL_OR_PENDING
    )


def test_resolve_named_check_outcome_fail_or_pending_when_pending() -> None:
    r = _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        pending_checks=("x",),
    )
    assert (
        _pr_ready.resolve_named_check_outcome("x", r) is _pr_ready.CheckOutcome.FAIL_OR_PENDING
    )


def test_resolve_named_check_outcome_pass_when_in_passing_checks() -> None:
    r = _pr_ready.PrReadiness(
        ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        passing_checks=("x",),
    )
    assert _pr_ready.resolve_named_check_outcome("x", r) is _pr_ready.CheckOutcome.PASS


def test_resolve_named_check_outcome_absent_when_unknown() -> None:
    r = _pr_ready.PrReadiness(ok=False, merge_state_status="CLEAN", review_decision="APPROVED")
    assert _pr_ready.resolve_named_check_outcome("x", r) is _pr_ready.CheckOutcome.ABSENT


def test_check_pr_merge_readiness_not_quarantined_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    rollup = [{"name": "ci/test", "conclusion": "FAILURE", "status": "COMPLETED"}]
    forge.queue("read_pr", pr_read(readiness_fields("BLOCKED", "APPROVED", rollup=rollup)))
    monkeypatch.setattr(_pr_ready, "_fetch_pr_base_sha", lambda _p, **_kw: None)
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert readiness.quarantined_checks == ()
    assert any("ci/test" in r for r in readiness.failed_reasons)


_VERIFIER_CHANGES_REQUESTED: dict[str, object] = {
    "author": {"login": "alan-verifier"}, "state": "CHANGES_REQUESTED",
}
_HUMAN_CHANGES_REQUESTED: dict[str, object] = {
    "author": {"login": "human-reviewer"}, "state": "CHANGES_REQUESTED",
}


def _changes_requested_read(latest_reviews: object = None) -> PrRead:
    fields = readiness_fields("BLOCKED", "CHANGES_REQUESTED", rollup=[_PASSING_CI])
    if latest_reviews is not None:
        fields["latestReviews"] = latest_reviews
    return pr_read(fields)


def test_verifier_blockers_request_latest_reviews_in_the_same_pr_view_call(
    forge: ForgeScript,
) -> None:
    forge.queue("read_pr", _changes_requested_read([]))
    _pr_ready.check_pr_verifier_blockers(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    expected_fields = (
        "mergeStateStatus", "reviewDecision", "statusCheckRollup",
        "headRefOid", "headRefName", "latestReviews",
    )
    assert forge.calls("read_pr")[0][0] == (42, expected_fields)


def test_merge_readiness_keeps_the_merge_lane_pr_view_fields(forge: ForgeScript) -> None:
    forge.queue("read_pr", _changes_requested_read())
    _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    expected_fields = (
        "mergeStateStatus", "reviewDecision", "statusCheckRollup", "headRefOid", "headRefName",
    )
    assert forge.calls("read_pr")[0][0] == (42, expected_fields)


def test_verifier_blockers_review_decision_ignores_the_verifier_bots_own_changes_requested(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue("read_pr", _changes_requested_read([
        _VERIFIER_CHANGES_REQUESTED,
        {"author": {"login": "copilot-pull-request-reviewer"}, "state": "COMMENTED"},
    ]))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == ""


def test_verifier_blockers_review_decision_honours_the_verifier_login_override(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "other-bot")
    forge.queue("read_pr", _changes_requested_read([
        {"author": {"login": "other-bot"}, "state": "CHANGES_REQUESTED"},
    ]))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == ""


def test_verifier_blockers_review_decision_keeps_a_human_changes_requested(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue(
        "read_pr", _changes_requested_read([_VERIFIER_CHANGES_REQUESTED, _HUMAN_CHANGES_REQUESTED]),
    )
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == "CHANGES_REQUESTED"


@pytest.mark.parametrize("latest_reviews", [None, "not-a-list"])
def test_verifier_blockers_review_decision_keeps_changes_requested_without_latest_reviews(
    forge: ForgeScript, latest_reviews: object,
) -> None:
    forge.queue("read_pr", _changes_requested_read(latest_reviews))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == "CHANGES_REQUESTED"


def test_verifier_blockers_review_decision_treats_an_authorless_changes_requested_as_human(
    forge: ForgeScript,
) -> None:
    forge.queue(
        "read_pr", _changes_requested_read(["junk", {"state": "CHANGES_REQUESTED", "author": "x"}]),
    )
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == "CHANGES_REQUESTED"


def test_verifier_blockers_review_decision_passes_non_changes_requested_through(
    forge: ForgeScript,
) -> None:
    fields = readiness_fields("CLEAN", "APPROVED", latestReviews=[_VERIFIER_CHANGES_REQUESTED])
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == "APPROVED"


def test_merge_readiness_keeps_the_verifier_bots_changes_requested_as_a_blocker(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
    forge.queue("read_pr", _changes_requested_read([_VERIFIER_CHANGES_REQUESTED]))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.review_decision == "CHANGES_REQUESTED"
    assert "review decision CHANGES_REQUESTED" in readiness.failed_reasons

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_post, _pr_ready, _pr_sonar, _pr_state_paths
from alan.teams._pr_ready import CheckOutcome, PrReadiness
from alan.teams._pr_sonar import SonarAnnotation
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result

_TARGET = DEFAULT_FORGE_TARGET.model_copy(
    update={"authoritative_checks": frozenset({"SonarCloud Code Analysis"})}
)

_WIDGET_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106
    authoritative_checks=frozenset({"Widget CI"}),
)

_EMPTY_CHECKS_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="rustco", repo="cargo-service"),
    credential_env_name="RUSTCO_GH_TOKEN",  # noqa: S106
    authoritative_checks=frozenset(),
)

_FLAKY_GATE = "Frontend E2E (Playwright, blocking)"


def _readiness(
    *,
    ok: bool = False,
    merge_state_status: str = "CLEAN",
    review_decision: str = "APPROVED",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    passing_checks: tuple[str, ...] = (),
    failed_reasons: tuple[str, ...] = (),
    quarantined_checks: tuple[str, ...] = (),
    check_count: int = 1,
) -> PrReadiness:
    return PrReadiness(
        ok=ok,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        passing_checks=passing_checks,
        failed_reasons=failed_reasons,
        quarantined_checks=quarantined_checks,
        check_count=check_count,
    )


def _sonar_ann(title: str = "Make sure this is safe") -> SonarAnnotation:
    return SonarAnnotation(path="foo.py", line=1, title=title, rule_url="")


def test_merge_block_label_ts_keys_match_enum_exactly() -> None:
    ts_path = (
        Path(__file__).resolve().parent.parent
        / "alan"
        / "web"
        / "src"
        / "mergeBlockLabel.ts"
    )
    source = ts_path.read_text(encoding="utf-8")
    labelled = set(re.findall(r'^\s+(\w+):\s*"', source, re.MULTILINE))
    enum_values = {mc.value for mc in MergeBlockClass}
    assert labelled == enum_values


def test_classify_block_dirty() -> None:
    r = _readiness(merge_state_status="DIRTY")
    assert classify_block(r, (), _TARGET) is MergeBlockClass.DIRTY


def test_classify_block_behind_plus_transient_behind_only() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failed_reasons=("mergeStateStatus=BEHIND",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BEHIND_PLUS_TRANSIENT


def test_classify_block_behind_plus_transient_with_pending() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        pending_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BEHIND", "pending checks: ci/test"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BEHIND_PLUS_TRANSIENT


def test_classify_block_stale_review_clean() -> None:
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.STALE_REVIEW


def test_classify_block_stale_review_empty_merge_state() -> None:
    r = _readiness(
        merge_state_status="",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.STALE_REVIEW


def test_classify_block_sonar_hotspot_all_hotspot_annotations() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("Make sure this doesn't leak"), _sonar_ann("make sure no SSRF"))
    assert classify_block(r, anns, _TARGET) is MergeBlockClass.SONAR_HOTSPOT


def test_classify_block_sonar_genuine_mixed_annotations() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("Make sure safe"), _sonar_ann("SQL injection vulnerability"))
    assert classify_block(r, anns, _TARGET) is MergeBlockClass.SONAR_GENUINE


def test_classify_block_sonar_no_annotations_when_empty() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.SONAR_NO_ANNOTATIONS


def test_classify_block_still_computing_pending_only() -> None:
    r = _readiness(
        pending_checks=("ci/test",),
        failed_reasons=("pending checks: ci/test",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.STILL_COMPUTING


def test_classify_block_blocked_no_signal_when_nothing_else_present() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        failed_reasons=("mergeStateStatus=BLOCKED",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BLOCKED_NO_SIGNAL


def test_classify_block_genuine_blocked_with_failing_check() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/tests"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_blocked_pending_with_pending_checks() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        pending_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "pending checks: ci/test"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BLOCKED_PENDING


def test_classify_block_genuine_blocked_with_failing_checks() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        failing_checks=("ci/test",),
        pending_checks=("ci/other",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_genuine_blocked_with_changes_requested() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        pending_checks=("ci/test",),
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
        ),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_genuine_behind_plus_changes_requested() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("mergeStateStatus=BEHIND", "review decision CHANGES_REQUESTED"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_genuine_behind_plus_genuine_failing_check() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BEHIND", "failing checks: ci/test"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_behind_plus_transient_with_pre_existing_red_check() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=BEHIND", "failing checks: ci/tests"),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BEHIND_PLUS_TRANSIENT


def test_classify_block_behind_plus_transient_with_flaky_check() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=("mergeStateStatus=BEHIND", f"failing checks: {_FLAKY_GATE}"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.BEHIND_PLUS_TRANSIENT


def test_classify_block_transient_flake_not_behind_for_backend_check_red_only() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=("Backend tests + coverage",),
        failed_reasons=(
            "mergeStateStatus=BEHIND", "failing checks: Backend tests + coverage",
        ),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.TRANSIENT_FLAKE


def test_classify_block_genuine_behind_unknown_reason() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failed_reasons=("mergeStateStatus=BEHIND", "something unexpected"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_pre_existing_red_all_quarantined() -> None:
    r = _readiness(
        failing_checks=("ci/tests",),
        failed_reasons=("failing checks: ci/tests",),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_classify_block_pre_existing_red_multiple_all_quarantined() -> None:
    r = _readiness(
        failing_checks=("ci/tests", "ci/lint"),
        failed_reasons=("failing checks: ci/tests, ci/lint",),
        quarantined_checks=("ci/tests", "ci/lint"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_classify_block_genuine_when_only_some_quarantined() -> None:
    r = _readiness(
        failing_checks=("ci/tests", "ci/lint"),
        failed_reasons=("failing checks: ci/tests, ci/lint",),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_genuine_when_quarantined_but_changes_requested() -> None:
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        failing_checks=("ci/tests",),
        failed_reasons=("review decision CHANGES_REQUESTED", "failing checks: ci/tests"),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_genuine_when_quarantined_but_no_failing_checks() -> None:
    r = _readiness(
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_sonar_genuine_multiple_failing() -> None:
    r = _readiness(
        failing_checks=("ci/test", "SonarCloud Code Analysis"),
        failed_reasons=("failing checks: ci/test, SonarCloud Code Analysis",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_sonar_blocked_by_changes_requested() -> None:
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=(
            "review decision CHANGES_REQUESTED",
            "failing checks: SonarCloud Code Analysis",
        ),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_sonar_blocked_by_merge_state() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=(
            "mergeStateStatus=BEHIND",
            "failing checks: SonarCloud Code Analysis",
        ),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_stale_review_not_matched_when_has_hooks() -> None:
    r = _readiness(
        merge_state_status="HAS_HOOKS",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.STALE_REVIEW


def test_classify_block_stale_review_behind_is_not_stale() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("mergeStateStatus=BEHIND", "review decision CHANGES_REQUESTED"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_pending_not_stale_when_has_requests() -> None:
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        pending_checks=("ci/test",),
        failed_reasons=(
            "review decision CHANGES_REQUESTED",
            "pending checks: ci/test",
        ),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.STALE_REVIEW


def test_classify_block_transient_flake_single_flaky_check() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.TRANSIENT_FLAKE


def test_classify_block_transient_flake_blocked_required_check() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=("mergeStateStatus=BLOCKED", f"failing checks: {_FLAKY_GATE}"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.TRANSIENT_FLAKE


def test_classify_block_dirty_wins_over_flaky_check() -> None:
    r = _readiness(
        merge_state_status="DIRTY",
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=("mergeStateStatus=DIRTY",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.DIRTY


def test_classify_block_survivor_at_head_defeats_transient_flake() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    assert (
        classify_block(r, (), _TARGET, survived_at_head=frozenset({_FLAKY_GATE}))
        is MergeBlockClass.GENUINE
    )


def test_classify_block_default_survivor_param_keeps_transient_flake() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.TRANSIENT_FLAKE


def test_attempt_recovery_proven_deterministic_never_reruns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1"
    )
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_FLAKY_GATE: "sha-1"}
    )

    def _no_rerun(*_a: object, **_kw: object) -> bool:
        raise AssertionError("proven-deterministic failure must not be rerun")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", _no_rerun)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert (ok, cls) == (False, MergeBlockClass.GENUINE)


def test_attempt_recovery_skips_head_sha_fetch_when_no_allowlisted_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _readiness(
        failing_checks=("SonarCloud (blocking quality gate)",),
        failed_reasons=("failing checks: SonarCloud (blocking quality gate)",),
    )

    def _no_fetch(*_a: object, **_kw: object) -> str:
        raise AssertionError("head-sha fetch must be lazy")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", _no_fetch)
    monkeypatch.setattr(
        _pr_sonar, "list_sonarcloud_annotations", lambda *_a, **_kw: ()
    )
    ok, _cls, _rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=_TARGET
    )
    assert ok is False


def test_attempt_recovery_head_sha_fetch_failure_falls_open_to_transient_flake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {_FLAKY_GATE: "sha-1"}
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_kw: 1)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert (ok, cls) == (True, MergeBlockClass.TRANSIENT_FLAKE)


def test_classify_block_transient_flake_not_eligible_when_changes_requested() -> None:
    r = _readiness(
        review_decision="CHANGES_REQUESTED",
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=("review decision CHANGES_REQUESTED", f"failing checks: {_FLAKY_GATE}"),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_transient_flake_not_eligible_when_mixed_with_genuine_check() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE, "ci/other"),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}, ci/other",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.GENUINE


def test_classify_block_lone_sonar_failure_still_routes_to_sonar_not_flake() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.SONAR_NO_ANNOTATIONS


def test_classify_block_pre_existing_red_when_quarantined_and_blocked() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/tests"),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_classify_block_pre_existing_red_when_quarantined_and_unstable() -> None:
    r = _readiness(
        merge_state_status="UNSTABLE",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=UNSTABLE", "failing checks: ci/tests"),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_classify_block_dirty_wins_over_quarantined_all_failing() -> None:
    r = _readiness(
        merge_state_status="DIRTY",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=DIRTY", "failing checks: ci/tests"),
        quarantined_checks=("ci/tests",),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.DIRTY


def test_is_transient_flake_dirty_returns_false_directly() -> None:
    r = _readiness(merge_state_status="DIRTY", failing_checks=(_FLAKY_GATE,))
    assert merge_recovery._is_transient_flake(r) is False  # noqa: SLF001


def test_is_transient_flake_matches_flaky_only_failing() -> None:
    table = [
        _readiness(),
        _readiness(failing_checks=(_FLAKY_GATE,)),
        _readiness(failing_checks=(_FLAKY_GATE, "ci/other")),
        _readiness(merge_state_status="DIRTY", failing_checks=(_FLAKY_GATE,)),
        _readiness(review_decision="CHANGES_REQUESTED", failing_checks=(_FLAKY_GATE,)),
        _readiness(
            merge_state_status="BLOCKED",
            failing_checks=(_FLAKY_GATE,),
        ),
    ]
    for r in table:
        assert merge_recovery._is_transient_flake(r) == _pr_flake_ledger.flaky_only_failing(r)  # noqa: SLF001


def test_classify_block_pre_existing_red_wins_over_flaky_check() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
        quarantined_checks=(_FLAKY_GATE,),
    )
    assert classify_block(r, (), _TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_needs_sonar_annotations_true_when_sonar_only() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    assert merge_recovery.needs_sonar_annotations(r, _TARGET) is True


def test_needs_sonar_annotations_false_for_other_checks() -> None:
    r = _readiness(
        failing_checks=("ci/test",),
        failed_reasons=("failing checks: ci/test",),
    )
    assert merge_recovery.needs_sonar_annotations(r, _TARGET) is False


def test_needs_sonar_annotations_false_when_default_check_not_declared_by_target() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    assert merge_recovery.needs_sonar_annotations(r, _EMPTY_CHECKS_TARGET) is False


def test_needs_sonar_annotations_true_for_distinct_engine_authoritative_check() -> None:
    r = _readiness(
        failing_checks=("Widget CI",),
        failed_reasons=("failing checks: Widget CI",),
    )
    assert merge_recovery.needs_sonar_annotations(r, _WIDGET_TARGET) is True


def test_resolve_check_outcome_pass_when_declared_and_pr_green() -> None:
    r = _readiness(ok=True, passing_checks=("SonarCloud Code Analysis",))
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",  # noqa: S106
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )
    outcome = _pr_ready.resolve_check_outcome("SonarCloud Code Analysis", r, target)
    assert outcome is CheckOutcome.PASS


def test_resolve_check_outcome_fail_or_pending_when_failing() -> None:
    r = _readiness(failing_checks=("SonarCloud Code Analysis",))
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",  # noqa: S106
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )
    outcome = _pr_ready.resolve_check_outcome("SonarCloud Code Analysis", r, target)
    assert outcome is CheckOutcome.FAIL_OR_PENDING


def test_resolve_check_outcome_fail_or_pending_when_pending() -> None:
    r = _readiness(pending_checks=("SonarCloud Code Analysis",))
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",  # noqa: S106
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )
    outcome = _pr_ready.resolve_check_outcome("SonarCloud Code Analysis", r, target)
    assert outcome is CheckOutcome.FAIL_OR_PENDING


def test_resolve_check_outcome_absent_when_authoritative_checks_empty() -> None:
    r = _readiness(failing_checks=("SonarCloud Code Analysis",))
    outcome = _pr_ready.resolve_check_outcome(
        "SonarCloud Code Analysis", r, _EMPTY_CHECKS_TARGET
    )
    assert outcome is CheckOutcome.ABSENT


def test_resolve_check_outcome_absent_when_declared_check_not_in_rollup() -> None:
    r = _readiness(ok=False, failing_checks=("some/other-check",))
    outcome = _pr_ready.resolve_check_outcome("Widget CI", r, _WIDGET_TARGET)
    assert outcome is CheckOutcome.ABSENT


def test_resolve_check_outcome_never_pass_when_declared_check_missing_and_pr_not_green() -> None:
    r = _readiness(ok=False)
    outcome = _pr_ready.resolve_check_outcome("Widget CI", r, _WIDGET_TARGET)
    assert outcome is CheckOutcome.ABSENT


def test_classify_block_distinct_engine_authoritative_check_hotspot() -> None:
    r = _readiness(
        failing_checks=("Widget CI",),
        failed_reasons=("failing checks: Widget CI",),
    )
    anns = (_sonar_ann("make sure this is safe"),)
    assert classify_block(r, anns, _WIDGET_TARGET) is MergeBlockClass.SONAR_HOTSPOT


def test_classify_block_distinct_engine_authoritative_check_no_annotations() -> None:
    r = _readiness(
        failing_checks=("Widget CI",),
        failed_reasons=("failing checks: Widget CI",),
    )
    assert classify_block(r, (), _WIDGET_TARGET) is MergeBlockClass.SONAR_NO_ANNOTATIONS


def test_classify_block_empty_authoritative_checks_never_recovers_as_approval() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("make sure this is safe"),)
    assert classify_block(r, anns, _EMPTY_CHECKS_TARGET) is MergeBlockClass.GENUINE


def test_classify_block_sonar_authoritative_target_hotspot() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("make sure this is safe"),)
    assert classify_block(r, anns, _TARGET) is MergeBlockClass.SONAR_HOTSPOT


def test_default_forge_target_treats_a_lone_sonar_failure_as_advisory() -> None:
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("make sure this is safe"),)
    assert merge_recovery.needs_sonar_annotations(r, DEFAULT_FORGE_TARGET) is False
    assert classify_block(r, anns, DEFAULT_FORGE_TARGET) not in {
        MergeBlockClass.SONAR_HOTSPOT,
        MergeBlockClass.SONAR_GENUINE,
        MergeBlockClass.SONAR_NO_ANNOTATIONS,
    }


def test_attempt_behind_plus_transient_update_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    always(_active_fake_forge, "request_branch_update", api_result())
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    r = _readiness(
        merge_state_status="BEHIND",
        failed_reasons=("mergeStateStatus=BEHIND",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is True
    assert cls is MergeBlockClass.BEHIND_PLUS_TRANSIENT
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["action"] == "merge_recovery_attempt"
    assert audit["block_class"] == "behind_plus_transient"
    assert audit["recovered"] is True


def test_attempt_behind_plus_transient_update_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    always(_active_fake_forge, "request_branch_update", api_result(returncode=1, stderr="boom"))
    r = _readiness(
        merge_state_status="BEHIND",
        failed_reasons=("mergeStateStatus=BEHIND",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.BEHIND_PLUS_TRANSIENT
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["recovered"] is False


def test_attempt_behind_backend_red_only_does_not_call_update_pr_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not auto-rebase a genuinely broken backend check")

    _active_fake_forge.stub("request_branch_update", boom)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: False)
    r = _readiness(
        merge_state_status="BEHIND",
        failing_checks=("Backend tests + coverage",),
        failed_reasons=(
            "mergeStateStatus=BEHIND", "failing checks: Backend tests + coverage",
        ),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.TRANSIENT_FLAKE


def test_attempt_stale_review_dismiss_ok_returns_stale_verifier_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: True)
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is True
    assert cls is MergeBlockClass.STALE_VERIFIER_REVIEW
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "stale_verifier_review"
    assert audit["recovered"] is True


def test_attempt_stale_review_dismiss_fails_returns_stale_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    r = _readiness(
        merge_state_status="CLEAN",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.STALE_REVIEW
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "stale_review"
    assert audit["recovered"] is False


def test_attempt_genuine_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        merge_state_status="BLOCKED",
        failing_checks=("ci/tests",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/tests"),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.GENUINE
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "genuine"
    assert audit["recovered"] is False


def test_attempt_blocked_pending_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        merge_state_status="BLOCKED",
        pending_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "pending checks: ci/test"),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.BLOCKED_PENDING
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "blocked_pending"
    assert audit["recovered"] is False


def test_attempt_still_computing_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        pending_checks=("ci/test",),
        failed_reasons=("pending checks: ci/test",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.STILL_COMPUTING


def test_attempt_sonar_hotspot_ack_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_sonar, "sonar_ack_hotspots", lambda _p, _t: False)
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("Make sure this is safe"),)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, anns, forge_target=_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.SONAR_HOTSPOT
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "sonar_hotspot"
    assert audit["recovered"] is False


def test_attempt_sonar_hotspot_retrigger_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_sonar, "sonar_ack_hotspots", lambda _p, _t: True)
    monkeypatch.setattr(_pr_sonar, "retrigger_sonar_analysis", lambda _p, _t, **_kw: False)
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("Make sure this is safe"),)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, anns, forge_target=_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.SONAR_HOTSPOT
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "sonar_hotspot"
    assert audit["recovered"] is False


def test_attempt_sonar_hotspot_retrigger_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_sonar, "sonar_ack_hotspots", lambda _p, _t: True)
    monkeypatch.setattr(_pr_sonar, "retrigger_sonar_analysis", lambda _p, _t, **_kw: True)
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    anns = (_sonar_ann("Make sure this is safe"),)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, anns, forge_target=_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.SONAR_HOTSPOT
    audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert audit["block_class"] == "sonar_hotspot"
    assert audit["recovered"] is True


def test_attempt_sonar_no_annotations_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, (), forge_target=_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.SONAR_NO_ANNOTATIONS
    raw_audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert raw_audit["block_class"] == "sonar_no_annotations"


def test_attempt_distinct_engine_authoritative_check_hotspot_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_sonar, "sonar_ack_hotspots", lambda _p, _t: True)
    monkeypatch.setattr(_pr_sonar, "retrigger_sonar_analysis", lambda _p, _t, **_kw: True)
    r = _readiness(
        failing_checks=("Widget CI",),
        failed_reasons=("failing checks: Widget CI",),
    )
    anns = (_sonar_ann("make sure this is safe"),)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, anns, forge_target=_WIDGET_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.SONAR_HOTSPOT


def test_attempt_empty_authoritative_checks_falls_through_to_genuine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        failing_checks=("SonarCloud Code Analysis",),
        failed_reasons=("failing checks: SonarCloud Code Analysis",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, (), forge_target=_EMPTY_CHECKS_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.GENUINE


def test_attempt_pre_existing_red_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    r = _readiness(
        failing_checks=("ci/tests",),
        failed_reasons=("failing checks: ci/tests",),
        quarantined_checks=("ci/tests",),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(42, "task-x", r, forge_target=_TARGET)
    assert ok is False
    assert cls is MergeBlockClass.PRE_EXISTING_RED
    raw_audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert raw_audit["block_class"] == "pre_existing_red"
    assert raw_audit["recovered"] is False


def _flake_readiness() -> PrReadiness:
    return _readiness(
        failing_checks=(_FLAKY_GATE,),
        failed_reasons=(f"failing checks: {_FLAKY_GATE}",),
    )


def test_attempt_transient_flake_budget_exhausted_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)

    def boom(*_a: object, **_k: object) -> bool:
        raise AssertionError("rerun must not be attempted once the budget is exhausted")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", boom)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _flake_readiness(), forge_target=_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.TRANSIENT_FLAKE
    raw_audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert raw_audit["block_class"] == "transient_flake"
    assert raw_audit["recovered"] is False


def test_attempt_transient_flake_rerun_triggers_recovery_without_recording_occurrence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    bumped: list[int] = []

    def _bump(p: int, _f: object = None, **_kw: object) -> int:
        bumped.append(p)
        return len(bumped)

    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    recorded: list[tuple[int, tuple[str, ...]]] = []
    monkeypatch.setattr(
        _pr_flake_ledger,
        "record_flake_occurrence",
        lambda p, checks, _t, **_kw: recorded.append((p, checks)),
    )
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _flake_readiness(), forge_target=_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.TRANSIENT_FLAKE
    assert bumped == [42]
    assert recorded == []
    raw_audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert raw_audit["block_class"] == "transient_flake"
    assert raw_audit["recovered"] is True


def test_attempt_transient_flake_rerun_not_triggered_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not bump the counter when nothing was triggered")

    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", boom)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _flake_readiness(), forge_target=_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.TRANSIENT_FLAKE
    raw_audit = json.loads(
        (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()[-1]
    )
    assert raw_audit["block_class"] == "transient_flake"
    assert raw_audit["recovered"] is False


def test_attempt_transient_flake_uses_real_gate_rerun_count_namespaced_per_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    ok, cls, _ = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _flake_readiness(), (), forge_target=_WIDGET_TARGET
    )
    assert ok is True
    assert cls is MergeBlockClass.TRANSIENT_FLAKE
    assert _pr_state_paths.load_gate_rerun_count(42, _WIDGET_TARGET) == 1
    assert _pr_state_paths.load_gate_rerun_count(42, _TARGET) == 0


def test_recover_transient_flake_previous_sha_exhausted_still_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-old")
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-old")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    ok, cls = merge_recovery._recover_transient_flake(  # noqa: SLF001
        42, "task-x", _flake_readiness(), DEFAULT_FORGE_TARGET, "sha-new",
    )
    assert (ok, cls) == (True, MergeBlockClass.TRANSIENT_FLAKE)
    assert _pr_state_paths.load_gate_rerun_count(42, _TARGET) == 1


def test_recover_transient_flake_same_sha_exhausted_no_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not rerun when the same-SHA budget is exhausted")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", boom)
    ok, cls = merge_recovery._recover_transient_flake(  # noqa: SLF001
        42, "task-x", _flake_readiness(), DEFAULT_FORGE_TARGET, "sha-1",
    )
    assert (ok, cls) == (False, MergeBlockClass.TRANSIENT_FLAKE)


def test_recover_transient_flake_lifetime_exhausted_blocks_regardless_of_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    p = _pr_state_paths.gate_rerun_count_path(42, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-1", "count": 0, "lifetime": 6}))

    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not rerun once the lifetime ceiling is spent")

    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", boom)
    ok, cls = merge_recovery._recover_transient_flake(  # noqa: SLF001
        42, "task-x", _flake_readiness(), DEFAULT_FORGE_TARGET, "sha-new",
    )
    assert (ok, cls) == (False, MergeBlockClass.TRANSIENT_FLAKE)


def test_attempt_mechanical_recovery_fetches_head_sha_once_for_flaky_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    calls: list[int] = []

    def _sha(*_a: object, **_kw: object) -> str:
        calls.append(1)
        return "sha-1"

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", _sha)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    merge_recovery.attempt_mechanical_recovery(
        42, "task-x", _flake_readiness(), forge_target=_TARGET
    )
    assert len(calls) == 1


def test_unblock_instruction_sonar_hotspot_has_instruction_and_url() -> None:
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.SONAR_HOTSPOT, pr_number=42)
    assert blocker is not None
    assert blocker.instruction is not None
    assert "Safe" in blocker.instruction
    assert "re-dispatch pr_merge" in blocker.instruction
    assert blocker.action_url == _pr_sonar.sonar_hotspots_pr_url(42)


def test_unblock_instruction_sonar_hotspot_url_omitted_when_sonar_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_sonar, "_SONAR_HOST", "")
    monkeypatch.setattr(_pr_sonar, "_SONAR_PROJECT_KEY", "")
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.SONAR_HOTSPOT, pr_number=42)
    assert blocker is not None
    assert blocker.action_url is None
    assert blocker.instruction is not None


def test_unblock_instruction_none_for_genuine() -> None:
    assert merge_recovery.unblock_instruction(MergeBlockClass.GENUINE, pr_number=None) is None

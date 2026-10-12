from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

import alan.teams._verification_gates as _verification_gates
from alan import acceptance as _acceptance_mod
from alan import e2e_failures, process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready
from alan.teams import verification as verif_mod
from alan.teams._verification_routing import suggestions_for_verdict
from alan.teams.verification import (
    GATE_BLOCK_MARKER,
    TEAM,
    github_review_event,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_WIDGET_FORGE = support._WIDGET_FORGE
_E2E_GATE = support._E2E_GATE
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover
_blocked_readiness = support.blocked_readiness
_flaky_gate_readiness = support.flaky_gate_readiness
_dirty_readiness = support.dirty_readiness


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _resolve_and_format(
    readiness: _pr_ready.PrReadiness, pr_number: int = 252
) -> str:
    actionable, failing_tests = _verification_gates._resolve_actionable_gate_checks(
        readiness, pr_number, None, forge_target=DEFAULT_FORGE_TARGET
    )
    return _verification_gates._format_gate_block(readiness, actionable, failing_tests)


_HOME_SPEC_FAILURE = e2e_failures.FailingE2ETest(
    spec="alan/web/e2e/home-refetch-stability.spec.ts",
    title="[chromium] › e2e/home-refetch-stability.spec.ts:52:3 › no layout jump",
)
_APPROVE_JUDGE = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
_REQUEST_CHANGES_JUDGE = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"


def _no_evidence_no_diff_join(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        _pr_flake_ledger, "evidenced_actionable_failing_checks", lambda *_a, **_kw: ()
    )
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ())


def test_format_gate_block_includes_status_fields() -> None:
    block = _resolve_and_format(_blocked_readiness())
    assert "External Gate Status" in block
    assert "mergeStateStatus=BLOCKED" in block
    assert "reviewDecision=REVIEW_REQUIRED" in block


def test_format_gate_block_marks_genuine_failing_check_actionable() -> None:
    block = _resolve_and_format(_blocked_readiness())
    assert verif_mod.GATE_ACTIONABLE_YES in block
    assert "sonar" in block


def test_format_gate_block_marks_flaky_only_failure_non_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ()
    )
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=("Frontend E2E (Playwright, blocking)",),
        failed_reasons=("failing checks: Frontend E2E (Playwright, blocking)",),
    )
    block = _resolve_and_format(readiness)
    assert verif_mod.GATE_ACTIONABLE_NO in block
    assert verif_mod.GATE_ACTIONABLE_YES not in block


def test_format_gate_block_marks_quarantined_failure_non_actionable() -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=("sonar",),
        failed_reasons=("failing checks: sonar",),
        quarantined_checks=("sonar",),
    )
    assert verif_mod.GATE_ACTIONABLE_NO in _resolve_and_format(readiness)


def test_format_gate_block_marks_dirty_actionable_with_no_checks_line() -> None:
    block = _resolve_and_format(_dirty_readiness())
    assert verif_mod.GATE_ACTIONABLE_YES in block
    assert verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX not in block
    assert _verification_gates.parse_actionable_gate_checks(block) == ()


def test_format_gate_block_marks_merge_state_only_block_non_actionable() -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BEHIND",
        review_decision="REVIEW_REQUIRED",
        failed_reasons=("mergeStateStatus=BEHIND",),
    )
    assert verif_mod.GATE_ACTIONABLE_NO in _resolve_and_format(readiness)


def test_resolve_actionable_threads_forge_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    def fake(
        _readiness: object, _pr: int, _head_sha: object, *, forge_target: object
    ) -> tuple[str, ...]:
        seen.append(forge_target)
        return ("sonar",)

    monkeypatch.setattr(_pr_flake_ledger, "evidenced_actionable_failing_checks", fake)
    actionable, _tests = _verification_gates._resolve_actionable_gate_checks(
        _blocked_readiness(), 252, None, forge_target=_WIDGET_FORGE
    )
    assert seen == [_WIDGET_FORGE]
    assert actionable == ("sonar",)


def test_resolve_actionable_skips_diff_join_when_evidenced_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_flake_ledger, "evidenced_actionable_failing_checks", lambda *_a, **_kw: ("sonar",)
    )

    def _boom(*_a: object, **_kw: object) -> tuple[str, ...]:
        raise AssertionError("diff-join must not run when evidence already promoted")

    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", _boom)
    actionable, _tests = _verification_gates._resolve_actionable_gate_checks(
        _blocked_readiness(), 252, None, forge_target=DEFAULT_FORGE_TARGET
    )
    assert actionable == ("sonar",)


def test_resolve_actionable_falls_back_to_diff_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_flake_ledger, "evidenced_actionable_failing_checks", lambda *_a, **_kw: ()
    )
    monkeypatch.setattr(
        _pr_flake_ledger,
        "diff_attributable_e2e_checks",
        lambda *_a, **_kw: ("Frontend E2E (Playwright, blocking)",),
    )
    actionable, _tests = _verification_gates._resolve_actionable_gate_checks(
        _blocked_readiness(), 252, None, forge_target=DEFAULT_FORGE_TARGET
    )
    assert actionable == ("Frontend E2E (Playwright, blocking)",)


def test_resolve_actionable_fetches_failing_tests_once_and_threads_them_into_the_diff_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches: list[int] = []
    received: list[object] = []

    def fake_fetch(pr: int, **_kw: object) -> tuple[e2e_failures.FailingE2ETest, ...]:
        fetches.append(pr)
        return (_HOME_SPEC_FAILURE,)

    def fake_diff_join(*_a: object, **kw: object) -> tuple[str, ...]:
        received.append(kw["tests"])
        return (_E2E_GATE,)

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", fake_fetch)
    monkeypatch.setattr(
        _pr_flake_ledger, "evidenced_actionable_failing_checks", lambda *_a, **_kw: ()
    )
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", fake_diff_join)
    actionable, tests = _verification_gates._resolve_actionable_gate_checks(
        _flaky_gate_readiness(), 252, None, forge_target=DEFAULT_FORGE_TARGET
    )
    assert fetches == [252]
    assert received == [(_HOME_SPEC_FAILURE,)]
    assert actionable == (_E2E_GATE,)
    assert tests == (_HOME_SPEC_FAILURE,)


def test_resolve_actionable_skips_the_fetch_when_the_e2e_check_is_not_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("failing-test fetch must not run without a red E2E check")

    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", _boom)
    actionable, tests = _verification_gates._resolve_actionable_gate_checks(
        _blocked_readiness(), 252, None, forge_target=DEFAULT_FORGE_TARGET
    )
    assert actionable == ("sonar",)
    assert tests == ()


def test_format_gate_block_lists_failing_tests_under_non_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: (_HOME_SPEC_FAILURE,)
    )
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ())
    block = _resolve_and_format(_flaky_gate_readiness())
    assert verif_mod.GATE_ACTIONABLE_NO in block
    assert (
        f"{_verification_gates.GATE_FAILING_TEST_PREFIX} {_HOME_SPEC_FAILURE.spec} — "
        f"{_HOME_SPEC_FAILURE.title}"
    ) in block


def test_format_gate_block_lists_failing_tests_under_actionable_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: (_HOME_SPEC_FAILURE,)
    )
    monkeypatch.setattr(
        _pr_flake_ledger, "evidenced_actionable_failing_checks", lambda *_a, **_kw: (_E2E_GATE,)
    )
    block = _resolve_and_format(_flaky_gate_readiness())
    assert verif_mod.GATE_ACTIONABLE_YES in block
    assert _verification_gates.GATE_FAILING_TEST_PREFIX in block
    assert _verification_gates.parse_actionable_gate_checks(block) == (_E2E_GATE,)


def test_format_gate_block_omits_failing_test_lines_when_none_were_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ())
    block = _resolve_and_format(_flaky_gate_readiness())
    assert _verification_gates.GATE_FAILING_TEST_PREFIX not in block


def test_suggestions_non_actionable_downgrade_routes_to_pr_merge() -> None:
    assert suggestions_for_verdict(
        _REQUEST_CHANGES_JUDGE, 252, gate_non_actionable=True
    ) == ("pr_merge",)


def test_suggestions_non_actionable_without_pr_number_routes_to_implementation_fix() -> None:
    assert suggestions_for_verdict(
        _REQUEST_CHANGES_JUDGE, None, gate_non_actionable=True
    ) == ("implementation_fix",)


def test_suggestions_actionable_downgrade_still_routes_to_implementation_fix() -> None:
    assert suggestions_for_verdict(_REQUEST_CHANGES_JUDGE, 252) == ("implementation_fix",)


def test_downgrade_result_non_actionable_true_when_nothing_is_attributable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_evidence_no_diff_join(monkeypatch)
    result = _verification_gates._downgrade_result(
        _APPROVE_JUDGE, _flaky_gate_readiness(), 252, "task-x",
        forge_target=support._WIDGET_FORGE,
    )
    assert result.non_actionable is True
    assert result.flaky_only is True
    assert result.actionable_checks == ()


def test_downgrade_result_non_actionable_survives_a_changes_requested_veto_of_flaky_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_evidence_no_diff_join(monkeypatch)
    result = _verification_gates._downgrade_result(
        _APPROVE_JUDGE, _flaky_gate_readiness(review="CHANGES_REQUESTED"), 252, "task-x",
        forge_target=support._WIDGET_FORGE,
    )
    assert result.flaky_only is False
    assert result.non_actionable is True


def test_downgrade_result_non_actionable_false_when_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    result = _verification_gates._downgrade_result(
        _APPROVE_JUDGE, _dirty_readiness(), 252, "task-x", forge_target=support._WIDGET_FORGE,
    )
    assert result.dirty is True
    assert result.non_actionable is False


def test_downgrade_result_non_actionable_false_when_a_check_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    result = _verification_gates._downgrade_result(
        _APPROVE_JUDGE, _blocked_readiness(), 252, "task-x", forge_target=support._WIDGET_FORGE,
    )
    assert result.actionable_checks == ("sonar",)
    assert result.non_actionable is False


@pytest.mark.parametrize(
    "readiness", [_flaky_gate_readiness(), _dirty_readiness(), _blocked_readiness()]
)
def test_downgrade_result_non_actionable_flag_agrees_with_the_gate_actionable_no_line(
    monkeypatch: pytest.MonkeyPatch, readiness: _pr_ready.PrReadiness,
) -> None:
    _no_evidence_no_diff_join(monkeypatch)
    result = _verification_gates._downgrade_result(
        _APPROVE_JUDGE, readiness, 252, "task-x", forge_target=support._WIDGET_FORGE,
    )
    assert (verif_mod.GATE_ACTIONABLE_NO in result.output) is result.non_actionable


def test_downgrade_result_sets_dirty_true_for_dirty_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    result = _verification_gates._downgrade_result(
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n", _dirty_readiness(), 252, "task-x",
        forge_target=support._WIDGET_FORGE,
    )
    assert result.dirty is True
    assert result.flaky_only is False


def test_downgrade_result_dirty_false_for_non_dirty_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    result = _verification_gates._downgrade_result(
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n", _blocked_readiness(), 252, "task-x",
        forge_target=support._WIDGET_FORGE,
    )
    assert result.dirty is False


def test_github_review_event_gate_case_returns_comment() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"{GATE_BLOCK_MARKER}\nblocked on CI\n"
    )
    assert github_review_event(judge) == "COMMENT"


def test_github_review_event_genuine_defect_returns_request_changes() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    assert github_review_event(judge) == "REQUEST_CHANGES"


def test_github_review_event_gate_case_raw_nonzero_in_scope_zero_returns_comment() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        f"{GATE_BLOCK_MARKER}\nblocked on CI\n"
    )
    assert github_review_event(judge) == "COMMENT"


def test_github_review_event_gate_case_in_scope_nonzero_returns_request_changes() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 2\n"
        f"{GATE_BLOCK_MARKER}\nblocked on CI\n"
    )
    assert github_review_event(judge) == "REQUEST_CHANGES"


def test_github_review_event_request_changes_no_marker_returns_request_changes() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert github_review_event(judge) == "REQUEST_CHANGES"


def test_github_review_event_approve_returns_approve() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert github_review_event(judge) == "APPROVE"


def test_github_review_event_missing_verdict_returns_comment() -> None:
    assert github_review_event("no verdict line here") == "COMMENT"


def test_github_review_event_actionable_named_check_returns_request_changes() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"{GATE_BLOCK_MARKER}\n{verif_mod.GATE_ACTIONABLE_YES}\n"
        f"{verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX}pip-audit\n"
    )
    assert github_review_event(judge) == "REQUEST_CHANGES"


def test_github_review_event_actionable_yes_with_no_checks_line_returns_comment() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"{GATE_BLOCK_MARKER}\n{verif_mod.GATE_ACTIONABLE_YES}\n"
    )
    assert github_review_event(judge) == "COMMENT"


def test_github_review_event_actionable_yes_with_empty_checks_line_returns_comment() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"{GATE_BLOCK_MARKER}\n{verif_mod.GATE_ACTIONABLE_YES}\n"
        f"{verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX}\n"
    )
    assert github_review_event(judge) == "COMMENT"


def test_github_review_event_actionable_yes_with_whitespace_checks_line_returns_comment() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"{GATE_BLOCK_MARKER}\n{verif_mod.GATE_ACTIONABLE_YES}\n"
        f"{verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX}    \n"
    )
    assert github_review_event(judge) == "COMMENT"


def test_run_live_actionable_external_gate_posts_request_changes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _blocked_readiness()
    )
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    payloads = forge.review_payloads()
    assert [p["event"] for p in payloads] == ["REQUEST_CHANGES"]
    body = payloads[0]["body"]
    assert isinstance(body, str)
    assert "PARKED ON EXTERNAL GATES" in body
    assert GATE_BLOCK_MARKER in body
    assert handover.to_team_suggestions == ["implementation_fix"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_ok"
        and e["verdict"] == "REQUEST_CHANGES"
        and e["event"] == "REQUEST_CHANGES"
        for e in entries
    )


def test_flaky_only_downgrade_posts_comment_not_request_changes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda _pr, _f=None: {})
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert [p["event"] for p in forge.review_payloads()] == ["COMMENT"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_ok"
        and e["verdict"] == "REQUEST_CHANGES"
        and e["event"] == "COMMENT"
        for e in entries
    )


def test_run_live_dirty_downgrade_routes_to_pr_merge(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _dirty_readiness()
    )
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert "GATE_ACTIONABLE: yes" in handover.full_synthesis
    assert [p["event"] for p in forge.review_payloads()] == ["COMMENT"]


def test_run_live_non_actionable_downgrade_with_human_changes_requested_routes_to_pr_merge(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(review="CHANGES_REQUESTED"),
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda _pr, _f=None: {})
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ())
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert verif_mod.GATE_ACTIONABLE_NO in handover.full_synthesis
    assert [p["event"] for p in forge.review_payloads()] == ["COMMENT"]


def test_run_live_flaky_only_with_survivor_evidence_routes_to_implementation_fix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda _pr, _f=None: {_E2E_GATE: "sha-1"}
    )

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]


def test_run_live_survivor_evidence_with_budget_remaining_routes_to_implementation_fix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(
        _pr_flake_ledger, "load_gate_survived_checks", lambda _pr, _f=None: {_E2E_GATE: "sha-1"}
    )

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert "GATE_ACTIONABLE: yes" in handover.full_synthesis


def test_run_live_gate_park_scoped_out_of_scope_criticals_posts_request_changes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _acceptance_mod,
        "scoped_dod",
        lambda _meta, _tid: (["owned outcome"], ["sibling outcome"]),
    )
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _blocked_readiness()
    )
    judge_out = (
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_out)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [p["event"] for p in forge.review_payloads()] == ["REQUEST_CHANGES"]
    assert handover.to_team_suggestions == ["implementation_fix"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "review_ok"
        and e["verdict"] == "REQUEST_CHANGES"
        and e["event"] == "REQUEST_CHANGES"
        for e in entries
    )


def test_parse_actionable_gate_checks_reads_names_from_gate_block() -> None:
    block = _resolve_and_format(_blocked_readiness())
    assert verif_mod.parse_actionable_gate_checks(block) == ("sonar",)


def test_parse_actionable_gate_checks_splits_multiple_names() -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=("SonarCloud (blocking quality gate)", "SonarCloud Code Analysis"),
        failed_reasons=("failing checks: sonar",),
    )
    block = _resolve_and_format(readiness)
    assert verif_mod.parse_actionable_gate_checks(block) == (
        "SonarCloud (blocking quality gate)",
        "SonarCloud Code Analysis",
    )


def test_parse_actionable_gate_checks_empty_when_gate_not_actionable() -> None:
    synthesis = f"## External Gate Status\n{verif_mod.GATE_ACTIONABLE_NO}\nnothing to do"
    assert verif_mod.parse_actionable_gate_checks(synthesis) == ()


def test_parse_actionable_gate_checks_empty_when_marker_absent() -> None:
    assert verif_mod.parse_actionable_gate_checks("## External Gate Status\nCI red") == ()


def test_parse_actionable_gate_checks_empty_when_checks_line_missing() -> None:
    synthesis = f"## External Gate Status\n{verif_mod.GATE_ACTIONABLE_YES}\nno list here"
    assert verif_mod.parse_actionable_gate_checks(synthesis) == ()


def test_parse_actionable_gate_checks_drops_blank_entries() -> None:
    synthesis = (
        f"## External Gate Status\n{verif_mod.GATE_ACTIONABLE_YES}\n"
        f"{verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX}ci/one |  | ci/two\n"
    )
    assert verif_mod.parse_actionable_gate_checks(synthesis) == ("ci/one", "ci/two")


def test_parse_actionable_gate_checks_ignores_marker_outside_gate_block() -> None:
    synthesis = (
        "## Rationale\n"
        f"The prior round emitted {verif_mod.GATE_ACTIONABLE_YES} and "
        f"{verif_mod.GATE_ACTIONABLE_CHECKS_PREFIX}some check\n"
        "## External Gate Status\n"
        f"{verif_mod.GATE_ACTIONABLE_NO}\nnothing actionable here\n"
    )
    assert verif_mod.parse_actionable_gate_checks(synthesis) == ()


def test_gate_block_section_extracts_only_the_gate_block() -> None:
    synthesis = (
        "## Rationale\nprose\n"
        "## External Gate Status\nblocked on CI\n"
        "## Open questions\nnone\n"
    )
    section = verif_mod.gate_block_section(synthesis)
    assert "blocked on CI" in section
    assert "prose" not in section
    assert "Open questions" not in section


def test_gate_block_section_empty_when_no_gate_block() -> None:
    assert verif_mod.gate_block_section("## Rationale\nprose") == ""

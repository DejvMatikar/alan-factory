from __future__ import annotations

import json
from pathlib import Path

import pytest

import alan.teams._clause_actionability as ca_actionability_mod
import alan.teams._clause_audit as ca_mod
import alan.teams._forge_errors as _forge_errors
import alan.teams._pr_ready as _pr_ready
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams._clause_line_parse import ClauseVerdict

_APPROVE = "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
_CHECK_NAME = "Backend tests + coverage"
_HEAD = "a" * 40


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _decision_read_returns_the_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    def _read(*_args: object, **_kwargs: object) -> _pr_ready.ReadinessResult:
        context = ca_mod._clause_audit_var.get()  # noqa: SLF001
        assert context is not None
        assert context.readiness is not None
        return context.readiness

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _read)


def _row(
    req_id: str = "req-a", verdict: ClauseVerdict = "FAIL", evidence: str = "missing",
) -> ca_mod.ClauseAuditRow:
    return ca_mod.ClauseAuditRow(req_id=req_id, verdict=verdict, evidence=evidence)


def _context(
    *, result: ca_mod.ClauseAuditResult | None = None,
    readiness: _pr_ready.ReadinessResult | None = None,
) -> ca_mod.ClauseAuditContext:
    return ca_mod.ClauseAuditContext(
        requirements=(), diff_files=None, pr_state=None,
        pr_number=1, task_id="t1", mode="enforce", forge_target=DEFAULT_FORGE_TARGET,
        readiness=readiness, result=result,
    )


def _corroborating_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        passing_checks=(_CHECK_NAME,), head_sha=_HEAD,
    )


def _apply_with_context(context: ca_mod.ClauseAuditContext) -> ca_mod.ClauseAuditOutcome:
    token = ca_mod._clause_audit_var.set(context)  # noqa: SLF001
    try:
        return ca_mod.apply(_APPROVE)
    finally:
        ca_mod._clause_audit_var.reset(token)  # noqa: SLF001


def test_check_contradicted_fail_stands_down() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'local pytest run failed check="{_CHECK_NAME}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    assert outcome.escalate_unactionable is False
    assert (
        outcome.unactionable_rows[0].classification
        is ca_actionability_mod.ClauseActionability.CHECK_CONTRADICTED
    )
    assert outcome.unactionable_rows[0].check_name == _CHECK_NAME
    assert "Downgrading" not in outcome.judge_output
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" in outcome.judge_output
    assert "a local command result contradicted by a green required check" in outcome.judge_output


def test_host_env_fail_stands_down() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    assert outcome.escalate_unactionable is False
    assert (
        outcome.unactionable_rows[0].classification
        is ca_actionability_mod.ClauseActionability.HOST_ENV
    )
    assert "Downgrading" not in outcome.judge_output
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" in outcome.judge_output
    assert "host-environment pollution in its own checkout" in outcome.judge_output


def test_pr_state_plus_corroborated_batch_still_escalates() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(req_id="req-pr", evidence="PR_STATE: isDraft is false"),
            _row(req_id="req-env", evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),
        ),
        reported_failures=2, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    assert outcome.escalate_unactionable is True
    assert len(outcome.unactionable_rows) == 2
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" not in outcome.judge_output


def test_stand_down_note_survives_request_changes_input() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    token = ca_mod._clause_audit_var.set(  # noqa: SLF001
        _context(result=result, readiness=_corroborating_readiness())
    )
    try:
        outcome = ca_mod.apply(
            "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
        )
    finally:
        ca_mod._clause_audit_var.reset(token)  # noqa: SLF001
    assert outcome.escalate_unactionable is False
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" in outcome.judge_output
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output


def test_uncorroborated_env_nomination_stays_actionable_no_escalation() -> None:
    uncorroborated = _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="APPROVED",
        failing_checks=(_CHECK_NAME,), head_sha=_HEAD,
    )
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=uncorroborated))
    assert outcome.escalate_unactionable is False
    assert outcome.unactionable_rows == ()


def test_genuine_plus_unactionable_still_blocks_without_escalating() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(req_id="req-genuine", evidence="the file is missing"),
            _row(req_id="req-env", evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),
        ),
        reported_failures=2, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.escalate_unactionable is False
    assert len(outcome.unactionable_rows) == 1
    assert outcome.unactionable_rows[0].row.req_id == "req-env"
    banner_line = next(
        line for line in outcome.judge_output.splitlines() if "Evidenced FAIL on:" in line
    )
    assert "req-genuine" in banner_line
    assert "req-env" not in banner_line
    assert "req-env" in outcome.judge_output
    assert "tracked separately" in outcome.judge_output


def test_no_readiness_and_no_check_citation_stays_actionable() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence="the diff omits the rollback plan"),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=None))
    assert outcome.escalate_unactionable is False
    assert outcome.unactionable_rows == ()


def test_demoted_clause_writes_audit_row(audit_root: Path) -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "clause_demoted_unactionable")
    assert row["req_id"] == "req-a"
    assert row["demotion_class"] == "host_env"
    assert row["corroborating_check"] == _CHECK_NAME
    assert row["check_outcome"] == "pass"


def test_operator_only_only_batch_escalates_unactionable() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(req_id="req-a", evidence="OPERATOR_ONLY: 10-PR batch on day 1"),
            _row(req_id="req-b", evidence="OPERATOR_ONLY: 10-PR batch on day 2"),
            _row(req_id="req-c", evidence="OPERATOR_ONLY: concurrency group live on main"),
            _row(req_id="req-d", evidence="OPERATOR_ONLY: needs live forge access"),
        ),
        reported_failures=4, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=None))
    assert outcome.escalate_unactionable is True
    assert len(outcome.unactionable_rows) == 4
    assert all(
        d.classification is ca_actionability_mod.ClauseActionability.OPERATOR_ONLY
        for d in outcome.unactionable_rows
    )
    assert "ACCEPTANCE CLAUSE AUDIT — FAILED, but not attributable" in outcome.judge_output
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" not in outcome.judge_output
    assert outcome.clause_outcome is not None
    assert outcome.clause_outcome.actionable_ids == ()
    assert outcome.clause_outcome.operator_only_ids == ("req-a", "req-b", "req-c", "req-d")


def test_operator_only_with_genuine_defect_keeps_defect_actionable() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(req_id="req-genuine", evidence="the endpoint returns 500"),
            _row(req_id="req-op", evidence="OPERATOR_ONLY: needs a live traffic measurement"),
        ),
        reported_failures=2, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=None))
    assert outcome.escalate_unactionable is False
    banner_line = next(
        line for line in outcome.judge_output.splitlines() if "Evidenced FAIL on:" in line
    )
    assert "req-genuine" in banner_line
    assert "req-op" not in banner_line
    assert "req-op" in outcome.judge_output
    assert "tracked separately" in outcome.judge_output


def test_clause_outcome_unactionable_ids_populated_from_demoted_rows() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(req_id="req-genuine", evidence="the file is missing"),
            _row(req_id="req-env", evidence=f'ENV: stale build cache check="{_CHECK_NAME}"'),
        ),
        reported_failures=2, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=_corroborating_readiness()))
    assert outcome.clause_outcome is not None
    assert outcome.clause_outcome.actionable_ids == ("req-genuine",)
    assert outcome.clause_outcome.unactionable_ids == ("req-env",)


def test_clause_outcome_unactionable_ids_empty_when_batch_fully_actionable() -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence="the diff omits the rollback plan"),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome = _apply_with_context(_context(result=result, readiness=None))
    assert outcome.clause_outcome is not None
    assert outcome.clause_outcome.unactionable_ids == ()


def test_actionable_fail_writes_no_demotion_audit_row(audit_root: Path) -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence="the file is missing"),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    _apply_with_context(_context(result=result, readiness=None))
    log_path = audit_root / "verification_audit.jsonl"
    entries = (
        [json.loads(line) for line in log_path.read_text().splitlines()]
        if log_path.exists() else []
    )
    assert not any(e["action"] == "clause_demoted_unactionable" for e in entries)


_E2E_CHECK = "Frontend E2E (Playwright, blocking)"
_MOVED_HEAD = "b" * 40


def _readiness_at(
    head_sha: str, *, passing: tuple[str, ...] = (), pending: tuple[str, ...] = (),
    failing: tuple[str, ...] = (),
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=not failing and not pending, merge_state_status="CLEAN",
        review_decision="APPROVED", passing_checks=passing, pending_checks=pending,
        failing_checks=failing, head_sha=head_sha,
    )


def _not_observed_result() -> ca_mod.ClauseAuditResult:
    return ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'end-to-end run not observed in this pass check="{_E2E_CHECK}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )


def _decide_with(
    monkeypatch: pytest.MonkeyPatch, anchor: _pr_ready.ReadinessResult | None,
    decision: _pr_ready.ReadinessResult, result: ca_mod.ClauseAuditResult,
) -> tuple[ca_mod.ClauseAuditOutcome, list[int]]:
    reads: list[int] = []

    def _read(pr_number: int, *_args: object, **_kwargs: object) -> _pr_ready.ReadinessResult:
        reads.append(pr_number)
        return decision

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _read)
    return _apply_with_context(_context(result=result, readiness=anchor)), reads


def test_check_pending_at_begin_and_green_at_decision_stands_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, pending=(_E2E_CHECK,)),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert reads == [1]
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" in outcome.judge_output
    assert "Downgrading" not in outcome.judge_output
    assert (
        outcome.unactionable_rows[0].classification
        is ca_actionability_mod.ClauseActionability.CHECK_CONTRADICTED
    )


def test_check_run_not_yet_created_at_begin_and_green_at_decision_stands_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert "ACCEPTANCE CLAUSE AUDIT — stood down" in outcome.judge_output
    assert outcome.unactionable_rows[0].check_name == _E2E_CHECK


def test_check_green_at_begin_and_failing_at_decision_stays_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _readiness_at(_HEAD, failing=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_check_still_pending_at_decision_stays_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, pending=(_E2E_CHECK,)),
        _readiness_at(_HEAD, pending=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_head_moved_between_begin_and_decision_stays_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _readiness_at(_MOVED_HEAD, passing=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert reads == [1]
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_decision_read_forge_failure_stays_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _pr_ready.ReadinessUnavailable("gh unavailable"),
        _not_observed_result(),
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_decision_read_forge_failure_reporting_the_same_head_stays_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _pr_ready.ReadinessUnavailable("gh unavailable"),
        _not_observed_result(),
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_decision_read_raising_a_transient_forge_error_stays_actionable(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,  # noqa: ARG001
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeTransientError("gh pr view raised a 503")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _raise)
    outcome = _apply_with_context(
        _context(
            result=_not_observed_result(),
            readiness=_readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        )
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_decision_read_hitting_a_forge_rate_limit_stays_actionable(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,  # noqa: ARG001
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeRateLimited("secondary rate limit", 0.0)

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _raise)
    outcome = _apply_with_context(
        _context(
            result=_not_observed_result(),
            readiness=_readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        )
    )
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output
    assert outcome.unactionable_rows == ()


def test_begin_read_without_a_head_sha_skips_the_decision_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, reads = _decide_with(
        monkeypatch,
        _pr_ready.ReadinessUnavailable("gh unavailable"),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _not_observed_result(),
    )
    assert reads == []
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output


def test_missing_begin_read_skips_the_decision_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, reads = _decide_with(
        monkeypatch, None, _readiness_at(_HEAD, passing=(_E2E_CHECK,)), _not_observed_result(),
    )
    assert reads == []
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output


def test_uncited_fail_skips_the_decision_read(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence="the diff omits the rollback plan"),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome, reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        result,
    )
    assert reads == []
    assert "VERDICT: REQUEST_CHANGES" in outcome.judge_output


def test_operator_only_fail_citing_a_check_skips_the_decision_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(_row(evidence=f'OPERATOR_ONLY: baseline needs a CI artifact check="{_E2E_CHECK}"'),),
        reported_failures=1, count_mismatch=False, parse_failed=False,
    )
    outcome, reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        result,
    )
    assert reads == []
    assert outcome.escalate_unactionable is True


def test_ci_proven_clause_beside_an_operator_only_clause_escalates_to_a_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ca_mod.ClauseAuditResult(
        rows=(
            _row(
                req_id="req-e2e",
                evidence=f'end-to-end run not observed in this pass check="{_E2E_CHECK}"',
            ),
            _row(req_id="req-baseline", evidence="OPERATOR_ONLY: baseline needs a CI artifact"),
        ),
        reported_failures=2, count_mismatch=False, parse_failed=False,
    )
    outcome, _reads = _decide_with(
        monkeypatch,
        _readiness_at(_HEAD),
        _readiness_at(_HEAD, passing=(_E2E_CHECK,)),
        result,
    )
    assert outcome.escalate_unactionable is True
    assert outcome.clause_outcome is not None
    assert outcome.clause_outcome.actionable_ids == ()
    assert outcome.clause_outcome.operator_only_ids == ("req-baseline",)
    assert "Evidenced FAIL on:" not in outcome.judge_output

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import ForgeTarget
from alan.teams import _pr_ready
from alan.teams import _verification_gates as gates
from tests import _advisory_check_runs_support as support

_JUDGE = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
_SONAR_LISTING = [support.check_run(support.SONAR, support.SONAR_APP)]
_BASE_REF_READ = ("baseRefOid",)


@pytest.fixture(autouse=True)
def _quiet_gate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", "0")
    support.serve_no_head_sha()
    support.serve_no_failing_jobs()


def _resolve(forge_target: ForgeTarget = support.FORGE) -> gates.GateDowngradeResult:
    return gates.resolve_external_gates(_JUDGE, 42, "t", forge_target=forge_target)


def _gate_readiness() -> _pr_ready.ReadinessResult | None:
    return gates._resolve_gate_readiness(  # noqa: SLF001
        42, "t", forge_target=support.FORGE
    )


def _readiness_reads(reads: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [fields for fields in reads if fields != _BASE_REF_READ]


def _sonar_red(merge_state: str = "BLOCKED") -> dict[str, Any]:
    return support.pr_payload(
        [support.failing_run(support.SONAR), support.passing_run("ci")], merge_state=merge_state,
    )


def test_advisory_only_red_check_does_not_downgrade_an_approving_verdict() -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    result = _resolve()
    assert result.downgraded is False
    assert result.output == _JUDGE


def test_red_required_check_still_downgrades() -> None:
    support.serve_check_runs([support.check_run(support.BACKEND, support.ACTIONS_APP)])
    support.serve_pr_reads(support.pr_payload([support.failing_run(support.BACKEND)]))
    result = _resolve()
    assert result.downgraded is True
    assert f"failing checks: {support.BACKEND}" in result.output


def test_red_actions_check_that_github_does_not_require_still_downgrades() -> None:
    support.serve_check_runs([support.check_run(support.DRIFT_GATE, support.ACTIONS_APP)])
    support.serve_pr_reads(support.pr_payload([support.failing_run(support.DRIFT_GATE)]))
    result = _resolve()
    assert result.downgraded is True
    assert f"failing checks: {support.DRIFT_GATE}" in result.output


def test_red_check_with_unknown_app_identity_still_downgrades() -> None:
    support.serve_check_runs([support.app_less_run(support.SONAR)])
    support.serve_pr_reads(_sonar_red())
    assert _resolve().downgraded is True


def test_red_commit_status_context_still_downgrades() -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload([support.failing_context(support.SONAR)]))
    assert _resolve().downgraded is True


def test_red_check_missing_from_the_listing_still_downgrades() -> None:
    support.serve_check_runs([])
    support.serve_pr_reads(_sonar_red())
    assert _resolve().downgraded is True


def test_unreadable_listing_still_downgrades() -> None:
    support.serve_check_runs(None)
    support.serve_pr_reads(_sonar_red())
    assert _resolve().downgraded is True


def test_authoritative_check_downgrades_whatever_app_posted_it() -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    assert _resolve(support.AUTHORITATIVE_SONAR_FORGE).downgraded is True
    assert shas == []


def test_pending_wait_with_only_advisory_red_matches_a_pr_without_red() -> None:
    support.serve_check_runs(_SONAR_LISTING)
    pending = support.pr_payload([support.pending_run("slow"), support.failing_run(support.SONAR)])
    settled = support.pr_payload([support.passing_run("slow"), support.failing_run(support.SONAR)])
    reads = support.serve_pr_reads(pending, settled)
    assert _resolve().downgraded is False
    assert len(_readiness_reads(reads)) == 2


def test_pending_wait_without_any_red_check_polls_the_same_number_of_times() -> None:
    shas = support.serve_check_runs([])
    pending = support.pr_payload([support.pending_run("slow")], merge_state="CLEAN")
    settled = support.pr_payload([support.passing_run("slow")], merge_state="CLEAN")
    reads = support.serve_pr_reads(pending, settled)
    assert _resolve().downgraded is False
    assert len(_readiness_reads(reads)) == 2
    assert shas == []


def test_dirty_pr_with_only_advisory_red_is_still_downgraded() -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red("DIRTY"))
    result = _resolve()
    assert result.downgraded is True
    assert result.dirty is True
    assert "mergeStateStatus=DIRTY" in result.output
    assert "failing checks" not in result.output


def test_flaky_rerun_stage_receives_a_red_required_check_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs([support.check_run(support.E2E_GATE, support.ACTIONS_APP)])
    support.serve_pr_reads(support.pr_payload([support.failing_run(support.E2E_GATE)]))
    received: list[_pr_ready.PrReadiness] = []

    def record(
        _pr: int, _task: str, readiness: _pr_ready.PrReadiness, **_kw: object,
    ) -> _pr_ready.PrReadiness:
        received.append(readiness)
        return readiness

    monkeypatch.setattr(gates, "_maybe_rerun_flaky_gates", record)
    _gate_readiness()
    [readiness] = received
    assert readiness.failing_checks == (support.E2E_GATE,)
    assert readiness.failed_reasons == (f"failing checks: {support.E2E_GATE}",)


def test_blocking_and_advisory_reds_downgrade_exactly_as_without_the_filter() -> None:
    payload = support.pr_payload([
        support.failing_run(support.SONAR), support.failing_run(support.BACKEND),
    ])
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    support.serve_pr_reads(payload)
    filtered = _resolve()
    support.serve_check_runs(None)
    support.serve_pr_reads(payload)
    unfiltered = _resolve()
    assert filtered == unfiltered
    assert filtered.downgraded is True
    assert f"failing checks: {support.SONAR}, {support.BACKEND}" in filtered.output


def test_allowlisted_flaky_red_next_to_advisory_red_gets_no_rerun_and_is_downgraded() -> None:
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.E2E_GATE, support.ACTIONS_APP),
    ])
    support.serve_pr_reads(support.pr_payload([
        support.failing_run(support.E2E_GATE), support.failing_run(support.SONAR),
    ]))
    readiness = _gate_readiness()
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.failing_checks == (support.E2E_GATE, support.SONAR)
    assert support.forge_methods_called() == {"read_pr", "head_sha", "commit_check_runs_page"}
    result = _resolve()
    assert result.downgraded is True
    assert result.flaky_only is False
    assert "rerun" not in " ".join(support.forge_methods_called())


def test_no_red_check_reads_no_listing() -> None:
    shas = support.serve_check_runs([])
    support.serve_pr_reads(support.pr_payload([support.passing_run("ci")], merge_state="CLEAN"))
    assert _resolve().downgraded is False
    assert shas == []

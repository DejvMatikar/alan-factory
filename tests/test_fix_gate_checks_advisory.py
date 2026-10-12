from __future__ import annotations

from pathlib import Path

import pytest

from alan.e2e_failures import FailingE2ETest
from alan.teams import _fix_gate_checks, _pr_branch
from tests import _advisory_check_runs_support as support

_SONAR_DETAIL = _pr_branch.CheckFailureDetail(
    name=support.SONAR, title="Quality Gate failed", summary="1 New issue", details_url="u1",
)
_BACKEND_DETAIL = _pr_branch.CheckFailureDetail(
    name=support.BACKEND, title="failed", summary="pytest", details_url="u2",
)
_E2E_TEST = FailingE2ETest(spec="e2e/a.spec.ts", title="loads")


@pytest.fixture(autouse=True)
def _quiet_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    support.serve_no_head_sha()
    support.serve_no_failing_jobs()


def _checks() -> _fix_gate_checks.FailingChecks:
    return _fix_gate_checks.FailingChecks(
        details=(_SONAR_DETAIL, _BACKEND_DETAIL), failing_tests=(_E2E_TEST,),
    )


def _filtered(pr_number: int | None = 42) -> _fix_gate_checks.FailingChecks:
    return _fix_gate_checks.without_advisory(
        _checks(), pr_number, support.TASK_ID, forge_target=support.FORGE,
    )


def _sonar_and_backend_red() -> None:
    support.serve_pr_reads(support.pr_payload(
        [support.failing_run(support.SONAR), support.failing_run(support.BACKEND)],
        merge_state="BLOCKED",
    ))


def test_an_advisory_sonar_red_is_dropped_while_a_blocking_red_stays() -> None:
    support.serve_check_runs([
        support.check_run(support.SONAR, support.SONAR_APP),
        support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    _sonar_and_backend_red()
    filtered = _filtered()
    assert filtered.details == (_BACKEND_DETAIL,)
    assert filtered.failing_tests == (_E2E_TEST,)


def test_a_sonar_check_posted_by_github_actions_stays_listed() -> None:
    support.serve_check_runs([
        support.check_run(support.SONAR, support.ACTIONS_APP),
        support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    _sonar_and_backend_red()
    assert _filtered().details == (_SONAR_DETAIL, _BACKEND_DETAIL)


def test_an_authoritative_sonar_check_stays_listed() -> None:
    shas = support.serve_check_runs([support.check_run(support.SONAR, support.SONAR_APP)])
    support.serve_pr_reads(support.pr_payload(
        [support.failing_run(support.SONAR)], merge_state="BLOCKED",
    ))
    filtered = _fix_gate_checks.without_advisory(
        _fix_gate_checks.FailingChecks(details=(_SONAR_DETAIL,)), 42, support.TASK_ID,
        forge_target=support.AUTHORITATIVE_SONAR_FORGE,
    )
    assert filtered.details == (_SONAR_DETAIL,)
    assert shas == []


def test_unavailable_readiness_keeps_every_check() -> None:
    support.serve_unavailable_pr_reads()
    assert _filtered() == _checks()


def test_no_pr_number_keeps_every_check_without_reading_the_forge() -> None:
    assert _filtered(pr_number=None) == _checks()
    assert support.forge_methods_called() == set()


def test_no_listed_checks_reads_nothing() -> None:
    empty = _fix_gate_checks.FailingChecks(failing_tests=(_E2E_TEST,))
    assert _fix_gate_checks.without_advisory(
        empty, 42, support.TASK_ID, forge_target=support.FORGE,
    ) == empty
    assert support.forge_methods_called() == set()

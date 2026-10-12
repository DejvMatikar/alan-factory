from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import engine_preflight as ep
from alan.app import workspace_creation
from tests.readiness_support import DEFAULT_SLUG, REVIEWER, Install


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    return Install.isolated(tmp_path, monkeypatch)


def _expires_in(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def test_reviewer_expiry_warning_days_constant() -> None:
    assert ep.REVIEWER_EXPIRY_WARNING_DAYS == 14


@pytest.mark.parametrize(
    ("days_until_expiry", "expected_states"),
    [
        (15, ()),
        (14, ("warning",)),
        (1, ("warning",)),
        (0, ("not_ready",)),
        (-1, ("not_ready",)),
    ],
)
def test_reviewer_expiry_warning_window_boundaries(
    install: Install, days_until_expiry: int, expected_states: tuple[str, ...]
) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(days_until_expiry))
    install.save_preflight("pass")
    issues = ep.evaluate_readiness(install.default_cfg())
    assert tuple(issue.state for issue in issues) == expected_states


def test_no_recorded_expiry_produces_no_warning(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("pass")
    assert ep.evaluate_readiness(install.default_cfg()) == ()


def test_the_warning_names_the_iso_expiry_date_and_the_fix(install: Install) -> None:
    install.require(REVIEWER)
    expiry = _expires_in(5)
    install.store_reviewer(expires_at=expiry)
    install.save_preflight("pass")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "warning"
    assert expiry in issue.message
    assert issue.fix == "./setup.sh"


def test_an_expired_reviewer_credential_reports_alone_not_a_warning(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(-2))
    install.save_preflight("pass")
    (issue,) = ep.evaluate_readiness(install.default_cfg())
    assert issue.state == "not_ready"
    assert "expired" in issue.message


def test_the_warning_coexists_with_a_blocking_distinctness_issue(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(3))
    install.save_preflight("fail")
    issues = ep.evaluate_readiness(install.default_cfg())
    assert {issue.state for issue in issues} == {"not_ready", "warning"}
    assert len(issues) == 2


def test_the_warning_coexists_with_an_unknown_distinctness_issue(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(3))
    issues = ep.evaluate_readiness(install.default_cfg())
    assert {issue.state for issue in issues} == {"unknown", "warning"}
    assert len(issues) == 2


def test_blocking_issues_ignores_a_warning_only_report(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(3))
    install.save_preflight("pass")
    assert ep.blocking_issues(install.default_cfg()) == ()


def test_workspace_creation_proceeds_when_only_a_warning_is_present(install: Install) -> None:
    install.require(REVIEWER)
    install.store_reviewer(expires_at=_expires_in(3))
    install.save_preflight("pass")
    assert workspace_creation.resolve_engine_key(None, None, engine_key_provided=False) is None


def test_readiness_finding_is_pass_for_a_warning_only_report() -> None:
    issue = ep.ReadinessIssue(
        engine=DEFAULT_SLUG,
        requirement="reviewer_credential",
        state="warning",
        message="the reviewer credential expires on 2031-02-03",
        fix="./setup.sh",
    )
    row = ep._readiness_finding((issue,))  # noqa: SLF001
    assert row.status == "pass"
    assert "expires on 2031-02-03" in row.message
    assert row.remedy == "./setup.sh"


def test_readiness_finding_fail_wins_over_a_coexisting_warning() -> None:
    not_ready = ep.ReadinessIssue(
        engine=DEFAULT_SLUG,
        requirement="reviewer_credential",
        state="not_ready",
        message="the reviewer resolves to the same account as the author",
        fix="./setup.sh",
    )
    warning = ep.ReadinessIssue(
        engine=DEFAULT_SLUG,
        requirement="reviewer_credential",
        state="warning",
        message="the reviewer credential expires on 2031-02-03",
        fix="./setup.sh",
    )
    row = ep._readiness_finding((not_ready, warning))  # noqa: SLF001
    assert row.status == "fail"


def test_readiness_finding_unknown_wins_over_a_coexisting_warning() -> None:
    unknown = ep.ReadinessIssue(
        engine=DEFAULT_SLUG,
        requirement="reviewer_credential",
        state="unknown",
        message="m",
        fix="./setup.sh",
    )
    warning = ep.ReadinessIssue(
        engine=DEFAULT_SLUG,
        requirement="reviewer_credential",
        state="warning",
        message="the reviewer credential expires on 2031-02-03",
        fix="./setup.sh",
    )
    row = ep._readiness_finding((unknown, warning))  # noqa: SLF001
    assert row.status == "unknown"

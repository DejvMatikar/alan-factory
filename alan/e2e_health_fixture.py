from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from alan.engine_config import EngineConfig, RepoIdentity, lookup_by_slug, register
from alan.health import HealthFinding, HealthState, save_health_state
from alan.queue import Queue

_HEALTH_FIXTURE_ENV = "ALAN_E2E_HEALTH_FIXTURE"
_DESKTOP_MAX_HEALTH_FIXTURE_ENV = "ALAN_E2E_DESKTOP_MAX_HEALTH_FIXTURE"
_HEALTH_REPOS_FIXTURE_ENV = "ALAN_E2E_HEALTH_REPOS_FIXTURE"
_HEALTH_FIXTURE_PAUSE_DAYS = 365
_ALL_HEALTH_FIXTURE_ENVS = (
    _HEALTH_FIXTURE_ENV,
    _DESKTOP_MAX_HEALTH_FIXTURE_ENV,
    _HEALTH_REPOS_FIXTURE_ENV,
)


def _seed_state(queue: Queue, findings: list[HealthFinding], in_flight_count: int) -> None:
    now = datetime.now(UTC)
    state = HealthState(
        verdict="degraded",
        blocked_count=len(findings),
        in_flight_count=in_flight_count,
        findings=findings,
        computed_at=now.isoformat(),
    )
    save_health_state(queue, state)
    queue.pause_until((now + timedelta(days=_HEALTH_FIXTURE_PAUSE_DAYS)).timestamp())


def _seed_health_fixture_if_enabled(queue: Queue) -> None:
    if os.environ.get(_HEALTH_FIXTURE_ENV, "").strip() != "1":
        return
    findings = [
        HealthFinding(
            finding_id="e2e-fixture-critical",
            kind="build_task",
            severity="critical",
            description="Build task e2e-fixture-critical has failing CI checks",
            workspace_id="e2e-fixture-critical",
            pr_number=101,
            blocker_flags=["needs_human"],
            action="dispatch_remediation",
        ),
        HealthFinding(
            finding_id="e2e-fixture-high",
            kind="stale_job",
            severity="high",
            description="Job e2e-fixture-high has been running unusually long",
            workspace_id="e2e-fixture-high",
            pr_number=None,
            blocker_flags=[],
            action="deep_link",
            in_flight=True,
        ),
        HealthFinding(
            finding_id="e2e-fixture-medium",
            kind="recurring_errors",
            severity="medium",
            description="Recurring error pattern detected for e2e-fixture-medium",
            workspace_id=None,
            pr_number=None,
            blocker_flags=[],
            action="deep_link",
        ),
    ]
    _seed_state(queue, findings, in_flight_count=1)


_DESKTOP_MAX_FIXTURE_REPO = "acme-platform/checkout-service"

_DESKTOP_MAX_FIXTURE_FINDINGS = [
    HealthFinding(
        finding_id="e2e-desktop-max-critical",
        kind="build_task",
        severity="critical",
        description=(
            "Build task e2e-desktop-max-critical has failing CI checks on "
            + _DESKTOP_MAX_FIXTURE_REPO
        ),
        workspace_id="e2e-desktop-max-critical",
        pr_number=4201,
        blocker_flags=["needs_human", "merge_conflict"],
        action="dispatch_remediation",
        repo=_DESKTOP_MAX_FIXTURE_REPO,
    ),
    HealthFinding(
        finding_id="e2e-desktop-max-high",
        kind="stale_job",
        severity="high",
        description=(
            "Job e2e-desktop-max-high has been running unusually long against "
            + _DESKTOP_MAX_FIXTURE_REPO
        ),
        workspace_id="e2e-desktop-max-high",
        pr_number=None,
        blocker_flags=["needs_human"],
        action="deep_link",
        in_flight=True,
        repo=_DESKTOP_MAX_FIXTURE_REPO,
    ),
    HealthFinding(
        finding_id="e2e-desktop-max-medium",
        kind="recurring_errors",
        severity="medium",
        description=(
            "Recurring error pattern detected for e2e-desktop-max-medium across the nightly sweep"
        ),
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
        repo=_DESKTOP_MAX_FIXTURE_REPO,
    ),
    HealthFinding(
        finding_id="e2e-desktop-max-second-critical",
        kind="dependency_vulnerability",
        severity="critical",
        description=(
            "A high-severity advisory was published for a direct dependency of "
            + _DESKTOP_MAX_FIXTURE_REPO
        ),
        workspace_id=None,
        pr_number=4207,
        blocker_flags=["needs_human"],
        action="deep_link",
        repo=_DESKTOP_MAX_FIXTURE_REPO,
    ),
]


def _seed_desktop_max_health_fixture_if_enabled(queue: Queue) -> None:
    if os.environ.get(_DESKTOP_MAX_HEALTH_FIXTURE_ENV, "").strip() != "1":
        return
    _seed_state(queue, list(_DESKTOP_MAX_FIXTURE_FINDINGS), in_flight_count=1)


_HEALTH_REPOS_FIXTURE_BUSY_REPO = "acme-platform/checkout-service"
_HEALTH_REPOS_FIXTURE_QUIET_REPO = "acme-platform/quiet-repo"

_HEALTH_REPOS_FIXTURE_FINDINGS = [
    HealthFinding(
        finding_id="e2e-repos-busy-critical",
        kind="build_task",
        severity="critical",
        description=(
            "Build task e2e-repos-busy-critical has failing CI checks on "
            + _HEALTH_REPOS_FIXTURE_BUSY_REPO
        ),
        workspace_id="e2e-repos-busy-critical",
        pr_number=301,
        blocker_flags=["needs_human"],
        action="dispatch_remediation",
        repo=_HEALTH_REPOS_FIXTURE_BUSY_REPO,
        engine_key=_HEALTH_REPOS_FIXTURE_BUSY_REPO,
    ),
    HealthFinding(
        finding_id="e2e-repos-busy-high",
        kind="stale_job",
        severity="high",
        description=(
            "Job e2e-repos-busy-high has been running unusually long against "
            + _HEALTH_REPOS_FIXTURE_BUSY_REPO
        ),
        workspace_id="e2e-repos-busy-high",
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
        in_flight=True,
        repo=_HEALTH_REPOS_FIXTURE_BUSY_REPO,
        engine_key=_HEALTH_REPOS_FIXTURE_BUSY_REPO,
    ),
    HealthFinding(
        finding_id="e2e-repos-quiet-medium",
        kind="recurring_errors",
        severity="medium",
        description=(
            f"Recurring error pattern detected for {_HEALTH_REPOS_FIXTURE_QUIET_REPO}"
        ),
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
        repo=_HEALTH_REPOS_FIXTURE_QUIET_REPO,
        engine_key=_HEALTH_REPOS_FIXTURE_QUIET_REPO,
    ),
]


def _register_health_repos_fixture_engines() -> None:
    for slug in (_HEALTH_REPOS_FIXTURE_BUSY_REPO, _HEALTH_REPOS_FIXTURE_QUIET_REPO):
        if lookup_by_slug(slug) is not None:
            continue
        register(EngineConfig(repo=RepoIdentity.from_slug(slug)), validate_env=False)


def _seed_health_repos_fixture_if_enabled(queue: Queue) -> None:
    if os.environ.get(_HEALTH_REPOS_FIXTURE_ENV, "").strip() != "1":
        return
    _register_health_repos_fixture_engines()
    _seed_state(queue, list(_HEALTH_REPOS_FIXTURE_FINDINGS), in_flight_count=1)


def _enabled_health_fixture_envs() -> list[str]:
    enabled: list[str] = []
    if os.environ.get(_HEALTH_FIXTURE_ENV, "").strip() == "1":
        enabled.append(_HEALTH_FIXTURE_ENV)
    if os.environ.get(_DESKTOP_MAX_HEALTH_FIXTURE_ENV, "").strip() == "1":
        enabled.append(_DESKTOP_MAX_HEALTH_FIXTURE_ENV)
    if os.environ.get(_HEALTH_REPOS_FIXTURE_ENV, "").strip() == "1":
        enabled.append(_HEALTH_REPOS_FIXTURE_ENV)
    return enabled


def seed_e2e_health_fixtures(queue: Queue) -> None:
    enabled = _enabled_health_fixture_envs()
    if len(enabled) > 1:
        raise RuntimeError(f"at most one of {_ALL_HEALTH_FIXTURE_ENVS} may be set to \"1\"")
    _seed_health_fixture_if_enabled(queue)
    _seed_desktop_max_health_fixture_if_enabled(queue)
    _seed_health_repos_fixture_if_enabled(queue)

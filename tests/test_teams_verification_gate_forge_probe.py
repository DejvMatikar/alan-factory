from __future__ import annotations

import subprocess

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _forge_errors, _pr_ready
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, always, pr_read, readiness_fields

_RATE_LIMIT_STDERR = "You have exceeded a secondary rate limit (HTTP 403)"
_TRANSIENT_STDERR = "dial tcp: i/o timeout"
_AUTH_STDERR = "Bad credentials"
_UNKNOWN_STDERR = "Pull request is not mergeable"
_DOMAIN_ABSENT_STDERR = "no pull requests found for branch main"


def _gh_failure(fake: FakeForge, stderr: str) -> None:
    always(fake, "read_pr", pr_read(returncode=1, stderr=stderr))


def test_merge_readiness_probe_rate_limited_raises(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _RATE_LIMIT_STDERR)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_verifier_blockers_probe_rate_limited_raises(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _RATE_LIMIT_STDERR)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_ready.check_pr_verifier_blockers(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_merge_readiness_probe_transient_network_failure_raises(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _TRANSIENT_STDERR)
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_verifier_blockers_probe_transient_network_failure_raises(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _TRANSIENT_STDERR)
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_ready.check_pr_verifier_blockers(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_merge_readiness_probe_subprocess_missing_raises_transient(
    _active_fake_forge: FakeForge,
) -> None:
    ForgeScript(_active_fake_forge).queue("read_pr", FileNotFoundError("gh missing"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_verifier_blockers_probe_subprocess_timeout_raises_transient(
    _active_fake_forge: FakeForge,
) -> None:
    ForgeScript(_active_fake_forge).queue(
        "read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=1.0)
    )
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_ready.check_pr_verifier_blockers(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_merge_readiness_probe_auth_failure_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _AUTH_STDERR)
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_verifier_blockers_probe_auth_failure_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _AUTH_STDERR)
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_merge_readiness_probe_unknown_failure_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _UNKNOWN_STDERR)
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_verifier_blockers_probe_unknown_failure_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _UNKNOWN_STDERR)
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_merge_readiness_probe_domain_absent_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _DOMAIN_ABSENT_STDERR)
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_verifier_blockers_probe_domain_absent_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _DOMAIN_ABSENT_STDERR)
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_merge_readiness_probe_unparseable_json_keeps_no_verdict_shape(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({}))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_readiness_or_unavailable_absorbs_rate_limit_without_raising(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _RATE_LIMIT_STDERR)
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_readiness_or_unavailable_absorbs_transient_without_raising(
    _active_fake_forge: FakeForge,
) -> None:
    _gh_failure(_active_fake_forge, _TRANSIENT_STDERR)
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)


def test_readiness_or_unavailable_returns_real_result_when_gh_succeeds(
    _active_fake_forge: FakeForge,
) -> None:
    fields = readiness_fields(
        review_decision="APPROVED",
        rollup=[{"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}],
    )
    always(_active_fake_forge, "read_pr", pr_read(fields))
    readiness = _pr_ready.readiness_or_unavailable(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True

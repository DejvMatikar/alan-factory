from __future__ import annotations

import time

import pytest

from alan.claude_cli import ServerErrorRetryable, UsageLimitError
from alan.teams import _forge_errors

_INCIDENT_STDERR = "GraphQL: API rate limit already exceeded for user ID 24228929.\n"


def test_incident_stderr_classifies_rate_limited() -> None:
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=_INCIDENT_STDERR)
        == "rate_limited"
    )


def test_secondary_rate_limit_classifies_rate_limited() -> None:
    stderr = "You have exceeded a secondary rate limit. Please wait."
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=stderr)
        == "rate_limited"
    )


def test_http_429_classifies_rate_limited() -> None:
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr="HTTP 429")
        == "rate_limited"
    )


def test_rate_limited_wins_over_auth_for_403_rate_responses() -> None:
    stderr = "HTTP 403: API rate limit exceeded for installation."
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=stderr)
        == "rate_limited"
    )


def test_graphql_pullrequest_not_found_classifies_domain_absent() -> None:
    stderr = (
        "GraphQL: Could not resolve to a PullRequest with the number of 999999. "
        "(repository.pullRequest)"
    )
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=stderr)
        == "domain_absent"
    )


def test_rest_404_classifies_domain_absent() -> None:
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr="gh: Not Found (HTTP 404)")
        == "domain_absent"
    )


def test_bad_credentials_classifies_auth() -> None:
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr="Bad credentials")
        == "auth"
    )


def test_http_401_classifies_auth() -> None:
    assert _forge_errors.classify_forge_failure(returncode=1, stderr="HTTP 401") == "auth"


def test_dial_tcp_classifies_transient() -> None:
    stderr = 'Post "https://api.github.com/graphql": dial tcp 140.82.121.6:443: connect: refused'
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=stderr) == "transient"
    )


def test_dns_failure_classifies_transient_not_domain_absent() -> None:
    stderr = "could not resolve host: api.github.com"
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr=stderr) == "transient"
    )


def test_http_502_classifies_transient() -> None:
    assert (
        _forge_errors.classify_forge_failure(returncode=1, stderr="HTTP 502 Bad Gateway")
        == "transient"
    )


def test_unrecognized_stderr_classifies_unknown() -> None:
    assert (
        _forge_errors.classify_forge_failure(
            returncode=1, stderr="Pull request is not mergeable"
        )
        == "unknown"
    )


def test_none_returncode_classifies_transient() -> None:
    assert _forge_errors.classify_forge_failure(returncode=None, stderr=None) == "transient"


def test_raise_for_infra_raises_rate_limited_with_reset_epoch() -> None:
    before = time.time()
    with pytest.raises(_forge_errors.ForgeRateLimited) as excinfo:
        _forge_errors.raise_for_infra("gh pr view", returncode=1, stderr=_INCIDENT_STDERR)
    expected = before + _forge_errors.FORGE_RATE_LIMIT_PAUSE_S
    assert abs(excinfo.value.reset_at - expected) < 5.0
    assert "gh pr view failed (rc=1)" in excinfo.value.raw_message


def test_raise_for_infra_raises_transient_on_network_failure() -> None:
    with pytest.raises(_forge_errors.ForgeTransientError):
        _forge_errors.raise_for_infra("gh api", returncode=1, stderr="dial tcp: i/o timeout")


def test_forge_rate_limit_reset_at_no_payload_uses_default() -> None:
    before = time.time()
    result = _forge_errors.forge_rate_limit_reset_at()
    expected = before + _forge_errors.FORGE_RATE_LIMIT_PAUSE_S
    assert abs(result - expected) < 5.0


def test_forge_rate_limit_reset_at_unparseable_payload_uses_default() -> None:
    before = time.time()
    result = _forge_errors.forge_rate_limit_reset_at("nothing useful here")
    expected = before + _forge_errors.FORGE_RATE_LIMIT_PAUSE_S
    assert abs(result - expected) < 5.0


def test_forge_rate_limit_reset_at_parses_near_future_payload() -> None:
    target = time.time() + 120.0
    payload = f'"reset":{int(target)}'
    result = _forge_errors.forge_rate_limit_reset_at(payload)
    assert abs(result - target) < 5.0


def test_forge_rate_limit_reset_at_clamps_far_future_payload() -> None:
    now = time.time()
    payload = f'"reset":{int(now) + 999999}'
    result = _forge_errors.forge_rate_limit_reset_at(payload)
    assert result <= now + _forge_errors._MAX_FORGE_PAUSE_S + 5.0  # noqa: SLF001
    assert result >= now + _forge_errors._MAX_FORGE_PAUSE_S - 5.0  # noqa: SLF001


def test_forge_rate_limit_reset_at_floors_past_payload_at_now() -> None:
    before = time.time()
    payload = f'"reset":{int(before) - 999999}'
    result = _forge_errors.forge_rate_limit_reset_at(payload)
    assert result >= before


def test_raise_for_infra_returns_domain_absent_without_raising() -> None:
    result = _forge_errors.raise_for_infra(
        "gh pr view", returncode=1, stderr="gh: Not Found (HTTP 404)"
    )
    assert result == "domain_absent"


def test_raise_for_infra_returns_auth_without_raising() -> None:
    result = _forge_errors.raise_for_infra(
        "gh pr view", returncode=1, stderr="Bad credentials"
    )
    assert result == "auth"


def test_raise_for_infra_returns_unknown_without_raising() -> None:
    result = _forge_errors.raise_for_infra(
        "gh pr merge", returncode=1, stderr="Pull request is not mergeable"
    )
    assert result == "unknown"


def test_forge_rate_limited_is_a_usage_limit_error() -> None:
    exc = _forge_errors.ForgeRateLimited("limited", 123.0)
    assert isinstance(exc, UsageLimitError)
    assert exc.reset_at == 123.0


def test_forge_transient_error_is_a_server_error_retryable() -> None:
    assert isinstance(_forge_errors.ForgeTransientError("x"), ServerErrorRetryable)


def test_infra_failure_detail_truncates_stderr() -> None:
    detail = _forge_errors.infra_failure_detail(
        "gh pr view", returncode=1, stderr="x" * 500
    )
    assert detail.startswith("gh pr view failed (rc=1): ")
    assert len(detail) <= len("gh pr view failed (rc=1): ") + 200

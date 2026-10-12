from __future__ import annotations

import time

import pytest

from alan.teams import _forge_errors, _pr_feedback_failure


def test_classify_feedback_failure_success_returns_none() -> None:
    assert _pr_feedback_failure.classify_feedback_failure(returncode=0, stderr="") is None


def test_classify_feedback_failure_rate_limited() -> None:
    result = _pr_feedback_failure.classify_feedback_failure(returncode=1, stderr="HTTP 429")
    assert result == "rate_limited"


def test_classify_feedback_failure_none_returncode_is_transient() -> None:
    result = _pr_feedback_failure.classify_feedback_failure(returncode=None, stderr=None)
    assert result == "transient"


def test_classify_feedback_failure_auth() -> None:
    result = _pr_feedback_failure.classify_feedback_failure(
        returncode=1, stderr="Bad credentials"
    )
    assert result == "auth"


def test_raise_for_graphql_errors_empty_list_does_not_raise() -> None:
    _pr_feedback_failure.raise_for_graphql_errors([], context="ctx")


def test_raise_for_graphql_errors_non_list_payload_does_not_raise() -> None:
    _pr_feedback_failure.raise_for_graphql_errors(None, context="ctx")


def test_raise_for_graphql_errors_raises_on_rate_limited_type() -> None:
    payload = [{"type": "RATE_LIMITED", "message": "slow down"}]
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")


def test_raise_for_graphql_errors_raises_on_rate_limited_message_text() -> None:
    payload = [{"type": "SOME_OTHER_TYPE", "message": "API rate limit exceeded"}]
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")


def test_raise_for_graphql_errors_does_not_raise_on_unrelated_error() -> None:
    payload = [{"type": "GRAPHQL_VALIDATION_FAILED", "message": "Field 'x' doesn't exist"}]
    _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")


def test_raise_for_graphql_errors_skips_non_dict_entries() -> None:
    payload = ["not-a-dict", {"type": "RATE_LIMITED", "message": "m"}]
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")


def test_raise_for_graphql_errors_entry_without_message_key_still_raises() -> None:
    payload = [{"type": "RATE_LIMITED"}]
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")


def test_raise_for_graphql_errors_reset_at_falls_back_to_default() -> None:
    before = time.time()
    payload = [{"type": "RATE_LIMITED", "message": "no timestamp here"}]
    with pytest.raises(_forge_errors.ForgeRateLimited) as excinfo:
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="ctx")
    expected = before + _forge_errors.FORGE_RATE_LIMIT_PAUSE_S
    assert abs(excinfo.value.reset_at - expected) < 5.0


def test_raise_for_graphql_errors_detail_includes_context_and_message() -> None:
    payload = [{"type": "RATE_LIMITED", "message": "please slow down"}]
    with pytest.raises(_forge_errors.ForgeRateLimited) as excinfo:
        _pr_feedback_failure.raise_for_graphql_errors(payload, context="gh api graphql feedback")
    assert "gh api graphql feedback" in excinfo.value.raw_message
    assert "please slow down" in excinfo.value.raw_message

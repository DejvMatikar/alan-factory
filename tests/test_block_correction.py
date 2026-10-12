from __future__ import annotations

import pytest

from alan.block_correction import BlockCorrectionError, run_correction


def test_clean_on_first_success() -> None:
    outcome = run_correction("hello", validate=lambda t: t.upper(), re_ask=None, max_attempts=1)
    assert outcome.outcome == "clean"
    assert outcome.value == "HELLO"
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].number == 1
    assert outcome.attempts[0].error is None
    assert outcome.final_error is None


def test_max_attempts_one_reproduces_today_behaviour_without_reask() -> None:
    calls: list[str] = []

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        calls.append(error)
        return "should never be called"

    def _validate(text: str) -> str:
        raise ValueError("bad output")

    outcome = run_correction("x", validate=_validate, re_ask=_reask, max_attempts=1)
    assert outcome.outcome == "exhausted"
    assert outcome.value is None
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].error == "bad output"
    assert outcome.final_error == "bad output"
    assert calls == []


def test_unreachable_author_when_reask_is_none_and_budget_remains() -> None:
    def _validate(text: str) -> str:
        raise ValueError("bad")

    outcome = run_correction("x", validate=_validate, re_ask=None, max_attempts=2)
    assert outcome.outcome == "unreachable_author"
    assert len(outcome.attempts) == 1


def test_unreachable_author_when_reask_returns_none() -> None:
    def _validate(text: str) -> str:
        raise ValueError("bad")

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        return None

    outcome = run_correction("x", validate=_validate, re_ask=_reask, max_attempts=3)
    assert outcome.outcome == "unreachable_author"
    assert len(outcome.attempts) == 1


def test_unchanged_stops_early_without_retrying() -> None:
    def _validate(text: str) -> str:
        raise ValueError("bad")

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        return prior_text

    outcome = run_correction("x", validate=_validate, re_ask=_reask, max_attempts=3)
    assert outcome.outcome == "unchanged"
    assert len(outcome.attempts) == 1


def test_same_error_stops_early_after_second_identical_failure() -> None:
    def _validate(text: str) -> str:
        raise ValueError("same error text")

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        return prior_text + "_v2"

    outcome = run_correction("x", validate=_validate, re_ask=_reask, max_attempts=3)
    assert outcome.outcome == "same_error"
    assert len(outcome.attempts) == 2


def test_corrected_on_second_attempt() -> None:
    calls: list[str] = []

    def _validate(text: str) -> str:
        if text == "bad":
            raise ValueError("nope")
        return text.upper()

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        calls.append(error)
        return "good"

    outcome = run_correction("bad", validate=_validate, re_ask=_reask, max_attempts=2)
    assert outcome.outcome == "corrected"
    assert outcome.value == "GOOD"
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].error == "nope"
    assert outcome.attempts[1].error is None
    assert calls == ["nope"]


def test_exhausted_after_max_attempts_with_varying_errors() -> None:
    errors = iter(["err1", "err2", "err3"])

    def _validate(text: str) -> str:
        raise ValueError(next(errors))

    counter = iter(range(100))

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        return f"retry-{next(counter)}"

    outcome = run_correction("x", validate=_validate, re_ask=_reask, max_attempts=3)
    assert outcome.outcome == "exhausted"
    assert len(outcome.attempts) == 3
    assert [a.error for a in outcome.attempts] == ["err1", "err2", "err3"]


def test_failure_kind_defaults_to_validation_error() -> None:
    def _validate(text: str) -> str:
        raise ValueError("boom")

    outcome = run_correction("x", validate=_validate, re_ask=None, max_attempts=1)
    assert outcome.attempts[0].kind == "validation_error"


def test_failure_kind_honors_block_correction_error() -> None:
    def _validate(text: str) -> str:
        raise BlockCorrectionError("bad yaml", kind="parse_error")

    outcome = run_correction("x", validate=_validate, re_ask=None, max_attempts=1)
    assert outcome.attempts[0].kind == "parse_error"


def test_successful_attempt_has_no_kind() -> None:
    outcome = run_correction("x", validate=lambda t: t, re_ask=None, max_attempts=1)
    assert outcome.attempts[0].kind is None


def test_max_attempts_below_one_raises_value_error() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        run_correction("x", validate=lambda t: t, re_ask=None, max_attempts=0)


def test_reask_receives_attempt_number_prior_text_and_error() -> None:
    seen: list[tuple[int, str, str]] = []

    def _validate(text: str) -> str:
        if text == "final":
            return text
        raise ValueError(f"bad:{text}")

    def _reask(attempt_number: int, prior_text: str, error: str) -> str | None:
        seen.append((attempt_number, prior_text, error))
        return "final"

    outcome = run_correction("start", validate=_validate, re_ask=_reask, max_attempts=2)
    assert outcome.outcome == "corrected"
    assert seen == [(1, "start", "bad:start")]


def test_block_correction_error_default_kind_is_validation_error() -> None:
    exc = BlockCorrectionError("msg")
    assert exc.kind == "validation_error"
    assert str(exc) == "msg"

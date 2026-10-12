from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

FailureKind = Literal["parse_error", "validation_error"]

Outcome = Literal[
    "clean",
    "repaired",
    "corrected",
    "exhausted",
    "unreachable_author",
    "unchanged",
    "same_error",
]

ReAsk = Callable[[int, str, str], "str | None"]


class BlockCorrectionError(Exception):
    def __init__(self, message: str, *, kind: FailureKind = "validation_error") -> None:
        super().__init__(message)
        self.kind: FailureKind = kind


@dataclass(frozen=True)
class Attempt:
    number: int
    text: str
    error: str | None
    kind: FailureKind | None = None


@dataclass(frozen=True)
class CorrectionOutcome(Generic[T]):
    outcome: Outcome
    value: T | None
    attempts: tuple[Attempt, ...]
    final_error: str | None


def _kind_of(exc: Exception) -> FailureKind:
    if isinstance(exc, BlockCorrectionError):
        return exc.kind
    return "validation_error"


@dataclass(frozen=True)
class _Continue:
    next_text: str
    next_attempt_number: int


def _classify_failure(
    attempts: tuple[Attempt, ...],
    *,
    max_attempts: int,
    re_ask: ReAsk | None,
    attempt_number: int,
    current_text: str,
    error: str,
) -> CorrectionOutcome[Any] | _Continue:
    if len(attempts) >= 2 and attempts[-2].error == error:
        return CorrectionOutcome("same_error", None, attempts, error)
    if attempt_number >= max_attempts:
        return CorrectionOutcome("exhausted", None, attempts, error)
    if re_ask is None:
        return CorrectionOutcome("unreachable_author", None, attempts, error)
    next_text = re_ask(attempt_number, current_text, error)
    if next_text is None:
        return CorrectionOutcome("unreachable_author", None, attempts, error)
    if next_text == current_text:
        return CorrectionOutcome("unchanged", None, attempts, error)
    return _Continue(next_text, attempt_number + 1)


def _failure_step(
    attempts: list[Attempt],
    exc: Exception,
    *,
    max_attempts: int,
    re_ask: ReAsk | None,
    attempt_number: int,
    current_text: str,
) -> CorrectionOutcome[Any] | _Continue:
    error = str(exc)
    attempts.append(Attempt(attempt_number, current_text, error, _kind_of(exc)))
    return _classify_failure(
        tuple(attempts),
        max_attempts=max_attempts,
        re_ask=re_ask,
        attempt_number=attempt_number,
        current_text=current_text,
        error=error,
    )


def _clean_step(
    attempts: list[Attempt], value: T, *, attempt_number: int, current_text: str
) -> CorrectionOutcome[T]:
    attempts.append(Attempt(attempt_number, current_text, None))
    outcome: Outcome = "clean" if attempt_number == 1 else "corrected"
    return CorrectionOutcome(outcome, value, tuple(attempts), None)


def run_correction(
    text: str,
    *,
    validate: Callable[[str], T],
    re_ask: ReAsk | None,
    max_attempts: int,
) -> CorrectionOutcome[T]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    attempts: list[Attempt] = []
    current_text = text
    attempt_number = 1
    while True:
        try:
            value = validate(current_text)
        except Exception as exc:  # noqa: BLE001
            step = _failure_step(
                attempts,
                exc,
                max_attempts=max_attempts,
                re_ask=re_ask,
                attempt_number=attempt_number,
                current_text=current_text,
            )
            if isinstance(step, _Continue):
                current_text = step.next_text
                attempt_number = step.next_attempt_number
                continue
            return step
        return _clean_step(
            attempts, value, attempt_number=attempt_number, current_text=current_text
        )


__all__ = [
    "Attempt",
    "BlockCorrectionError",
    "CorrectionOutcome",
    "FailureKind",
    "Outcome",
    "ReAsk",
    "run_correction",
]

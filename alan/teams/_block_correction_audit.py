from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from alan import health_repo_metrics
from alan.block_correction import Attempt, CorrectionOutcome, run_correction
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit

T = TypeVar("T")

UNREACHABLE_MAX_ATTEMPTS = 2


def audit_path() -> Path:
    return logs_root() / health_repo_metrics.BLOCK_CORRECTION_AUDIT_FILE


def _last_failure_kind(attempts: tuple[Attempt, ...]) -> str | None:
    for attempt in reversed(attempts):
        if attempt.kind is not None:
            return attempt.kind
    return None


def record(
    *,
    site: str,
    block: str,
    role: str,
    outcome: str,
    attempts: int,
    failure_kind: str | None = None,
    task_id: str | None = None,
) -> None:
    append_jsonl_audit(
        audit_path(),
        {
            "site": site,
            "block": block,
            "role": role,
            "outcome": outcome,
            "attempts": attempts,
            "failure_kind": failure_kind,
            "task_id": task_id,
        },
        log_label="block_correction",
    )


def record_from_outcome(
    *,
    site: str,
    block: str,
    role: str,
    outcome: CorrectionOutcome[Any],
    task_id: str | None = None,
    outcome_override: str | None = None,
) -> None:
    record(
        site=site,
        block=block,
        role=role,
        outcome=outcome_override or outcome.outcome,
        attempts=len(outcome.attempts),
        failure_kind=_last_failure_kind(outcome.attempts),
        task_id=task_id,
    )


def record_unreachable_attempt(
    *,
    site: str,
    role: str,
    validate: Callable[[str], T],
    text: str,
    task_id: str | None = None,
) -> T | None:
    outcome = run_correction(
        text, validate=validate, re_ask=None, max_attempts=UNREACHABLE_MAX_ATTEMPTS
    )
    record_from_outcome(site=site, block=site, role=role, outcome=outcome, task_id=task_id)
    return outcome.value


__all__ = [
    "UNREACHABLE_MAX_ATTEMPTS",
    "audit_path",
    "record",
    "record_from_outcome",
    "record_unreachable_attempt",
]

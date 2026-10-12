from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from alan.block_correction import CorrectionOutcome, run_correction
from alan.claude_cli import ClaudeCliError
from alan.teams._block_correction_audit import record_from_outcome

T = TypeVar("T")

MAX_ATTEMPTS = 2
SITE = "EXTRACT_DISPATCH_YAML"


def correction_user_text(error: str) -> str:
    return (
        "Your last response could not be parsed as a valid dispatch "
        f"proposal. Parser error: {error}\n\n"
        "Re-emit ONLY the corrected YAML document, in the exact shape "
        "requested, with no other prose."
    )


def run_dispatch_correction(
    first_result_text: str,
    *,
    validate: Callable[[str], T],
    do_turn_again: Callable[[str], str | None],
    role: str,
    task_id: str | None = None,
) -> CorrectionOutcome[T]:
    def _re_ask(attempt_number: int, prior_text: str, error: str) -> str | None:
        del attempt_number, prior_text
        try:
            return do_turn_again(error)
        except ClaudeCliError:
            return None

    outcome = run_correction(
        first_result_text, validate=validate, re_ask=_re_ask, max_attempts=MAX_ATTEMPTS
    )
    record_from_outcome(site=SITE, block=SITE, role=role, outcome=outcome, task_id=task_id)
    return outcome


__all__ = ["MAX_ATTEMPTS", "SITE", "correction_user_text", "run_dispatch_correction"]

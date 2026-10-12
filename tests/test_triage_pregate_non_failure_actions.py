from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from alan.teams.triage_pregate import (
    _is_failure_line,  # pyright: ignore[reportPrivateUsage]
    gather_recurring_errors,
)


def test_verifier_settle_fail_is_not_a_failure_line() -> None:
    assert _is_failure_line("verifier_settle_fail") is False


def test_verifier_settle_timeout_is_still_a_failure_line() -> None:
    assert _is_failure_line("verifier_settle_timeout") is True


def test_verifier_settle_green_is_not_a_failure_line() -> None:
    assert _is_failure_line("verifier_settle_green") is False


def test_pr_create_failed_still_counts_as_failure() -> None:
    assert _is_failure_line("pr_create_failed") is True


def test_push_failed_still_counts_as_failure() -> None:
    assert _is_failure_line("push_failed") is True


def test_merge_failed_still_counts_as_failure() -> None:
    assert _is_failure_line("merge_failed") is True


def test_review_post_failed_still_counts_as_failure() -> None:
    assert _is_failure_line("review_post_failed") is True


def test_verifier_checkout_failed_still_counts_as_failure() -> None:
    assert _is_failure_line("verifier_checkout_failed") is True


def test_action_name_containing_verifier_settle_fail_as_substring_still_counts_as_failure() -> None:
    assert _is_failure_line("verifier_settle_fail_extra") is True


def _write_audit_rows(path: Path, actions: list[str]) -> None:
    now = datetime.now(UTC).isoformat()
    lines = [json.dumps({"action": action, "timestamp_utc": now}) for action in actions]
    path.write_text("\n".join(lines) + "\n")


def test_two_verifier_settle_fail_entries_produce_no_recurring_finding(tmp_path: Path) -> None:
    _write_audit_rows(
        tmp_path / "verification_audit.jsonl",
        ["verifier_settle_fail", "verifier_settle_fail"],
    )
    surface = gather_recurring_errors(tmp_path, hours=1)
    assert surface.is_blocked is False
    assert surface.obs["recurring_signatures"] == []


def test_one_excluded_and_two_non_excluded_entries_produce_one_finding_naming_non_excluded(
    tmp_path: Path,
) -> None:
    _write_audit_rows(
        tmp_path / "verification_audit.jsonl",
        ["verifier_settle_fail", "push_failed", "push_failed"],
    )
    surface = gather_recurring_errors(tmp_path, hours=1)
    assert surface.is_blocked is True
    assert surface.obs["recurring_signatures"] == ["push_failed"]

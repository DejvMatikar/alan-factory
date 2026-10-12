from __future__ import annotations

import pytest
from pydantic import ValidationError

from alan.park import (
    Blocker,
    ParkReason,
    blockers_from_readiness,
    park_reason_for_decision,
    synthetic_blocker,
)


def test_park_reason_is_closed_eight_value_set() -> None:
    assert {r.value for r in ParkReason} == {
        "question",
        "crash",
        "merge_block",
        "stall",
        "routing_error",
        "escalated",
        "frozen_chain",
        "credential",
        "unarchived",
    }


def test_park_reason_is_str_backed() -> None:
    assert ParkReason.CRASH.value == "crash"
    assert isinstance(ParkReason.CRASH, str)


def test_blocker_requires_non_empty_name_and_detail() -> None:
    with pytest.raises(ValidationError):
        Blocker(kind="reason", name="", detail="x")
    with pytest.raises(ValidationError):
        Blocker(kind="reason", name="x", detail="")


def test_blocker_instruction_and_action_url_default_to_none_for_backcompat() -> None:
    legacy = {"kind": "reason", "name": "reason", "detail": "boom"}
    blocker = Blocker.model_validate(legacy)
    assert blocker.instruction is None
    assert blocker.action_url is None


def test_blocker_accepts_instruction_and_action_url() -> None:
    blocker = Blocker(
        kind="reason",
        name="unblock",
        detail="x",
        instruction="do the thing",
        action_url="https://example.com",
    )
    assert blocker.instruction == "do the thing"
    assert blocker.action_url == "https://example.com"


def test_synthetic_blocker_wraps_reason_text() -> None:
    blockers = synthetic_blocker("worker crashed: boom")
    assert len(blockers) == 1
    assert blockers[0].kind == "reason"
    assert blockers[0].detail == "worker crashed: boom"


def test_synthetic_blocker_falls_back_on_blank_text() -> None:
    blockers = synthetic_blocker("   ")
    assert len(blockers) == 1
    assert blockers[0].detail == "no details available"


def test_blockers_from_readiness_empty_when_no_data() -> None:
    assert blockers_from_readiness() == []


def test_blockers_from_readiness_projects_failing_checks() -> None:
    blockers = blockers_from_readiness(failing_checks=("Tests", "Lint"))
    assert [b.kind for b in blockers] == ["failing_check", "failing_check"]
    assert [b.name for b in blockers] == ["Tests", "Lint"]


def test_blockers_from_readiness_projects_pending_checks() -> None:
    blockers = blockers_from_readiness(pending_checks=("Build",))
    assert blockers[0].kind == "pending_check"
    assert blockers[0].name == "Build"


def test_blockers_from_readiness_failing_check_detail_does_not_claim_required() -> None:
    blockers = blockers_from_readiness(failing_checks=("Tests",))
    assert blockers[0].detail == "check failing: Tests"


def test_blockers_from_readiness_pending_check_detail_does_not_claim_required() -> None:
    blockers = blockers_from_readiness(pending_checks=("Build",))
    assert blockers[0].detail == "check pending: Build"


def test_blockers_from_readiness_projects_failed_reasons() -> None:
    blockers = blockers_from_readiness(failed_reasons=("mergeStateStatus=DIRTY",))
    assert blockers[0].kind == "failed_reason"
    assert blockers[0].detail == "mergeStateStatus=DIRTY"


def test_blockers_from_readiness_projects_review_decision_when_present() -> None:
    blockers = blockers_from_readiness(review_decision="CHANGES_REQUESTED")
    assert blockers[0].kind == "review_decision"
    assert blockers[0].name == "CHANGES_REQUESTED"


def test_blockers_from_readiness_skips_empty_review_decision() -> None:
    assert blockers_from_readiness(review_decision="") == []


def test_blockers_from_readiness_projects_merge_state_status_when_present() -> None:
    blockers = blockers_from_readiness(merge_state_status="BEHIND")
    assert blockers[0].kind == "merge_state_status"
    assert blockers[0].name == "BEHIND"


def test_blockers_from_readiness_skips_empty_merge_state_status() -> None:
    assert blockers_from_readiness(merge_state_status="") == []


def test_blockers_from_readiness_projects_quarantined_checks() -> None:
    blockers = blockers_from_readiness(quarantined_checks=("Flaky E2E",))
    assert blockers[0].kind == "quarantined_check"
    assert blockers[0].name == "Flaky E2E"


def test_blocker_accepts_failing_test_kind() -> None:
    blocker = Blocker(
        kind="failing_test", name="e2e/explore-chat.spec.ts", detail="Explore chat renders",
    )
    assert blocker.kind == "failing_test"
    assert blocker.name == "e2e/explore-chat.spec.ts"
    assert blocker.detail == "Explore chat renders"


def test_blockers_from_readiness_is_the_complete_picture() -> None:
    blockers = blockers_from_readiness(
        failing_checks=("Tests",),
        pending_checks=("Build",),
        failed_reasons=("mergeStateStatus=DIRTY",),
        review_decision="CHANGES_REQUESTED",
        merge_state_status="DIRTY",
        quarantined_checks=("Flaky",),
    )
    assert [b.kind for b in blockers] == [
        "failing_check",
        "pending_check",
        "failed_reason",
        "review_decision",
        "merge_state_status",
        "quarantined_check",
    ]


def test_park_reason_for_decision_merge_block_when_block_class_set() -> None:
    assert park_reason_for_decision("await_human", "genuine") is ParkReason.MERGE_BLOCK


def test_park_reason_for_decision_escalated_for_escalate_kind() -> None:
    assert park_reason_for_decision("escalate", None) is ParkReason.ESCALATED


def test_park_reason_for_decision_escalated_never_folds_into_stall() -> None:
    assert park_reason_for_decision("escalate", None) is not ParkReason.STALL


def test_park_reason_for_decision_question_for_plain_await_human() -> None:
    assert park_reason_for_decision("await_human", None) is ParkReason.QUESTION


def test_park_reason_for_decision_merge_block_takes_priority_over_escalate() -> None:
    assert park_reason_for_decision("escalate", "genuine") is ParkReason.MERGE_BLOCK


def test_park_reason_for_decision_credential_when_block_class_credential() -> None:
    assert park_reason_for_decision("await_human", "credential") is ParkReason.CREDENTIAL


def test_park_reason_for_decision_credential_takes_priority_over_merge_block() -> None:
    assert park_reason_for_decision("await_human", "credential") is not ParkReason.MERGE_BLOCK


def test_park_reason_for_decision_signature_takes_no_workspace_or_metadata() -> None:
    import inspect

    sig = inspect.signature(park_reason_for_decision)
    assert list(sig.parameters) == ["kind", "block_class"]

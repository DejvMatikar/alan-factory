from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, OpenQuestion
from alan.teams import _pr_state_paths
from alan.teams import _verification_capability as vc_mod
from alan.teams import verification as verif_mod
from alan.workspace import Workspace, create

_REASON = "needs vault access."


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return create("capability-boundary-task", "d", root=tmp_path / "ws")


@pytest.fixture(autouse=True)
def _isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _prior_handover(
    ws: Workspace, *, pr_number: int | None, open_questions: list[OpenQuestion] | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_prior",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior",
        pr_number=pr_number,
        open_questions=open_questions or [],
    )


def _answered_capability_question(
    question_id: str, reason: str = _REASON, answer: str = "waive",
) -> OpenQuestion:
    return OpenQuestion(
        id=question_id,
        question=vc_mod.capability_boundary_question_text(reason),
        proposed_answer_space=["resolve", answer],
        resolver_role="human",
        blocking=True,
        answer=answer,
    )


def _unanswered_capability_question(question_id: str) -> OpenQuestion:
    return OpenQuestion(
        id=question_id,
        question="unresolvable",
        proposed_answer_space=["resolve", "waive"],
        resolver_role="human",
        blocking=True,
    )


def _record_waiver(pr_number: int, question: OpenQuestion) -> None:
    _pr_state_paths.record_capability_boundary_waiver(
        pr_number, vc_mod.capability_boundary_waiver_key_for_question(question),
        DEFAULT_FORGE_TARGET,
    )


class TestIsCapabilityBoundaryQuestion:
    def test_true_for_prefixed_id(self) -> None:
        q = _unanswered_capability_question("verif-capability-boundary-abc12345")
        assert vc_mod.is_capability_boundary_question(q) is True

    def test_false_for_other_id(self) -> None:
        q = _unanswered_capability_question("clause-stall-req-a")
        assert vc_mod.is_capability_boundary_question(q) is False


class TestCapabilityBoundaryWaived:
    def test_false_when_prior_is_none(self, ws: Workspace) -> None:
        assert vc_mod.capability_boundary_waived(ws, None, _REASON) is False

    def test_false_when_prior_has_no_pr_number(self, ws: Workspace) -> None:
        prior = _prior_handover(ws, pr_number=None)
        assert vc_mod.capability_boundary_waived(ws, prior, _REASON) is False

    def test_false_when_no_waiver_was_ever_recorded(self, ws: Workspace) -> None:
        prior = _prior_handover(ws, pr_number=7)
        assert vc_mod.capability_boundary_waived(ws, prior, _REASON) is False

    def test_true_when_waiver_recorded_for_this_pr_and_reason(self, ws: Workspace) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        prior = _prior_handover(ws, pr_number=7, open_questions=[q])
        _record_waiver(7, q)
        assert vc_mod.capability_boundary_waived(ws, prior, _REASON) is True

    def test_true_when_the_later_handover_carries_no_open_questions_at_all(
        self, ws: Workspace
    ) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        _record_waiver(7, q)
        later_pass = _prior_handover(ws, pr_number=7, open_questions=[])
        assert vc_mod.capability_boundary_waived(ws, later_pass, _REASON) is True

    def test_false_for_a_different_reason_on_the_same_pr(self, ws: Workspace) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        _record_waiver(7, q)
        prior = _prior_handover(ws, pr_number=7)
        assert vc_mod.capability_boundary_waived(ws, prior, "a wholly different reason.") is False

    def test_recorded_waiver_for_a_different_pr_does_not_count(self, ws: Workspace) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        _record_waiver(8, q)
        prior = _prior_handover(ws, pr_number=7)
        assert vc_mod.capability_boundary_waived(ws, prior, _REASON) is False


class TestCapabilityBoundaryHandoverFold:
    def test_returns_none_when_verdict_is_not_request_changes(self, ws: Workspace) -> None:
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, None, lambda _e: None, "inv_z", "NO_FLEET_ROLE_CAN_FIX: x", "APPROVE",
        )
        assert result is None

    def test_returns_none_when_no_boundary_declared(self, ws: Workspace) -> None:
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, None, lambda _e: None, "inv_z", "nothing declared here", "REQUEST_CHANGES",
        )
        assert result is None

    def test_emits_escalation_when_boundary_declared_and_not_waived(self, ws: Workspace) -> None:
        prior = _prior_handover(ws, pr_number=7)
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, prior, lambda _e: None, "inv_z",
            "NO_FLEET_ROLE_CAN_FIX: needs vault access.", "REQUEST_CHANGES",
        )
        assert result is not None
        assert len(result.open_questions) == 1

    def test_returns_none_when_boundary_declared_but_already_waived(self, ws: Workspace) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        prior = _prior_handover(ws, pr_number=7, open_questions=[q])
        _record_waiver(7, q)
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, prior, lambda _e: None, "inv_z",
            "NO_FLEET_ROLE_CAN_FIX: needs vault access.", "REQUEST_CHANGES",
        )
        assert result is None

    def test_still_returns_none_when_waived_on_a_later_handover_with_no_open_questions(
        self, ws: Workspace
    ) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        _record_waiver(7, q)
        later_pass = _prior_handover(ws, pr_number=7, open_questions=[])
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, later_pass, lambda _e: None, "inv_z",
            "NO_FLEET_ROLE_CAN_FIX: needs vault access.", "REQUEST_CHANGES",
        )
        assert result is None

    def test_still_escalates_when_waived_but_no_pr_number(self, ws: Workspace) -> None:
        q = _answered_capability_question("verif-capability-boundary-abc12345")
        prior = _prior_handover(ws, pr_number=None, open_questions=[q])
        result = verif_mod._capability_boundary_handover(  # noqa: SLF001
            ws, prior, lambda _e: None, "inv_z",
            "NO_FLEET_ROLE_CAN_FIX: needs vault access.", "REQUEST_CHANGES",
        )
        assert result is not None

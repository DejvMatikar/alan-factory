from __future__ import annotations

from datetime import UTC, datetime

from alan import deploy_wait_gate
from alan.deploy_verify import GateRecord
from alan.handover import DeployWaitSignal, Handover
from alan.router import RouterDecision


def _terminal_handover(
    *, from_team: str = "deploy_verify", deploy_wait: DeployWaitSignal | None = None
) -> Handover:
    return Handover(
        handover_id="handover_t1_001",
        handover_type="terminal",
        task_id="t1",
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
        pr_number=7,
        deploy_wait=deploy_wait,
    )


def test_merged_wait_signal_re_enqueues_as_awaiting_live_with_merged_at() -> None:
    handover = _terminal_handover(
        deploy_wait=DeployWaitSignal(
            merge_sha="a" * 40,
            deadline=2000.0,
            race_deadline=None,
            reason="PR #7: awaiting the deploy restart signal",
            merged_at=1500.0,
        )
    )
    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    assert decision.kind == "enqueue"
    assert decision.team == "deploy_verify"
    assert decision.eligible_at == 2000.0
    assert decision.reason == "PR #7: awaiting the deploy restart signal"
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.path == "awaiting_live"
    assert record.merge_sha == "a" * 40
    assert record.deadline == 2000.0
    assert record.race_deadline is None
    assert record.merged_at == 1500.0


def test_merged_wait_signal_with_no_merged_at_still_re_enqueues_as_awaiting_live() -> None:
    handover = _terminal_handover(
        deploy_wait=DeployWaitSignal(
            merge_sha="a" * 40,
            deadline=2000.0,
            race_deadline=None,
            reason="PR #7: awaiting the deploy restart signal",
        )
    )
    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.path == "awaiting_live"
    assert record.merged_at is None


def test_merge_race_wait_signal_re_enqueues_with_race_deadline_as_eligible_at() -> None:
    handover = _terminal_handover(
        deploy_wait=DeployWaitSignal(
            merge_sha=None,
            deadline=1300.0,
            race_deadline=1300.0,
            reason="PR #7: mergedAt not yet available",
        )
    )
    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    assert decision.kind == "enqueue"
    assert decision.eligible_at == 1300.0
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.path == "awaiting_restart"
    assert record.merge_sha is None
    assert record.deadline is None
    assert record.race_deadline == 1300.0
    assert record.merged_at is None


def test_confirmed_handover_without_deploy_wait_passes_through() -> None:
    handover = _terminal_handover(deploy_wait=None)
    decision = RouterDecision(kind="done", reason="terminal handover")
    assert deploy_wait_gate.apply(handover, decision) is decision


def test_escalation_decision_passes_through_even_with_deploy_wait_field() -> None:
    handover = _terminal_handover(
        deploy_wait=DeployWaitSignal(
            merge_sha=None, deadline=100.0, race_deadline=None, reason="x"
        )
    )
    decision = RouterDecision(kind="escalate", reason="frozen")
    assert deploy_wait_gate.apply(handover, decision) is decision


def test_non_deploy_verify_from_team_passes_through() -> None:
    handover = _terminal_handover(
        from_team="pr_merge",
        deploy_wait=DeployWaitSignal(
            merge_sha=None, deadline=100.0, race_deadline=None, reason="x"
        ),
    )
    decision = RouterDecision(kind="done", reason="terminal handover")
    assert deploy_wait_gate.apply(handover, decision) is decision

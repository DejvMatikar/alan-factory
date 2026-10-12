from __future__ import annotations

import pytest

from alan import deploy_gate_route
from alan.teams import deploy_verify as deploy_verify_mod


def test_deploy_gate_decision_builds_enqueue_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deploy_verify_mod,
        "restart_gate_terms",
        lambda pr_number: (f"reason for {pr_number}", 123.0, "the-instructions"),
    )
    decision = deploy_gate_route.deploy_gate_decision(42)
    assert decision.kind == "enqueue"
    assert decision.team == "deploy_verify"
    assert decision.reason == "reason for 42"
    assert decision.eligible_at == 123.0
    assert decision.instructions == "the-instructions"


def test_deploy_gate_decision_passes_through_none_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deploy_verify_mod,
        "restart_gate_terms",
        lambda pr_number: ("immediate", None, None),
    )
    decision = deploy_gate_route.deploy_gate_decision(7)
    assert decision.kind == "enqueue"
    assert decision.team == "deploy_verify"
    assert decision.eligible_at is None
    assert decision.instructions is None

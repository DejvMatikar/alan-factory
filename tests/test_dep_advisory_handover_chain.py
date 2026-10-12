from __future__ import annotations

from pathlib import Path

import pytest

from alan import scenario_binding
from alan import workflow as workflow_mod
from alan.handover import Handover, list_handovers
from alan.queue import Queue
from alan.teams import _pr_handover, implementation
from alan.teams import pr_merge as pr_merge_mod
from alan.teams._base import TeamEvent
from alan.teams.verification import TEAM as VERIFICATION
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests.dep_advisory_support import (
    audit_row,
    prepare,
    run_check,
    sec_workspaces,
    vuln_entry,
)
from tests.fakes.forge import FakeForge

_PR = 77


def _emit(event: TeamEvent) -> None:
    del event


@pytest.fixture
def chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> tuple[Workspace, Handover]:
    prepare(
        monkeypatch, tmp_path, _active_fake_forge,
        audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.8.0")),
    )
    run_check(queue, ws_root)
    (ws,) = sec_workspaces(ws_root)
    return ws, list_handovers(ws)[0]


def _implementation_forward(ws: Workspace, bootstrap: Handover) -> Handover:
    return _pr_handover.finalize_handover(
        implementation._TEAM_CONFIG,  # noqa: SLF001
        ws, bootstrap, "inv_001", "raised urllib3", ["abcd123 fix(deps): raise urllib3"], _PR,
        iteration_push_failed=False,
    )


def _failed_outcome() -> pr_merge_mod.PrMergeOutcome:
    return pr_merge_mod.PrMergeOutcome(kind="failed", pr_number=_PR, reason="checks failed")


def test_the_bootstrap_handover_is_dependency_bot_authorized(
    chain: tuple[Workspace, Handover],
) -> None:
    _, bootstrap = chain

    assert bootstrap.dependency_bot_authorized is True


def test_implementation_keeps_the_flag_on_its_forward_handover(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain

    forward = _implementation_forward(ws, bootstrap)

    assert forward.handover_type == "forward"
    assert forward.to_team_suggestions == ["verification"]
    assert forward.dependency_bot_authorized is True


def test_implementation_keeps_the_flag_when_it_escalates_without_commits(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain

    handover = _pr_handover.finalize_handover(
        implementation._TEAM_CONFIG,  # noqa: SLF001
        ws, bootstrap, "inv_001", "nothing to do", [], None, iteration_push_failed=False,
    )

    assert handover.handover_type == "escalation"
    assert handover.dependency_bot_authorized is True


def test_implementation_keeps_the_flag_when_the_push_fails(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain

    handover = _pr_handover.finalize_handover(
        implementation._TEAM_CONFIG,  # noqa: SLF001
        ws, bootstrap, "inv_001", "pushed nothing", ["abcd123 fix(deps): x"], _PR,
        iteration_push_failed=True,
    )

    assert handover.handover_type == "escalation"
    assert handover.dependency_bot_authorized is True


def test_scenarios_are_waived_for_verification_of_the_implementation_output(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain
    forward = _implementation_forward(ws, bootstrap)

    assert scenario_binding._legacy_waived(ws, bootstrap, task_id=None) is True  # noqa: SLF001
    assert scenario_binding._legacy_waived(ws, forward, task_id=None) is True  # noqa: SLF001
    assert scenario_binding.scenario_mode(ws, forward) == "WAIVED"


def test_verification_keeps_the_flag_when_the_pr_precondition_fails(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain
    forward = _implementation_forward(ws, bootstrap)
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="verification",
        tasks=[PendingTaskSpec(id="judge", role="judge", model="opus")],
    )

    handover = VERIFICATION._pr_precondition_failed_escalation(  # noqa: SLF001
        ws, forward, _emit, inv_id, _PR
    )

    assert handover.dependency_bot_authorized is True


def test_pr_merge_failure_routes_the_authorized_chain_back_to_implementation_fix(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain
    forward = _implementation_forward(ws, bootstrap)

    handover = pr_merge_mod._build_handover(  # noqa: SLF001
        ws, forward, "inv_9", _failed_outcome()
    )

    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert handover.dependency_bot_authorized is True


def test_pr_merge_keeps_the_flag_on_its_escalation_and_await_human_handovers(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain
    forward = _implementation_forward(ws, bootstrap)
    outcome = pr_merge_mod.PrMergeOutcome(kind="closed", pr_number=_PR, reason="closed")

    escalation = pr_merge_mod._escalation_handover(ws, forward, "inv_9", outcome)  # noqa: SLF001
    awaiting = pr_merge_mod._await_human_handover(ws, forward, "inv_9", _failed_outcome())  # noqa: SLF001

    assert escalation.dependency_bot_authorized is True
    assert awaiting.dependency_bot_authorized is True


def test_the_pr_merge_directive_hop_keeps_scenarios_waived_for_the_next_verification(
    chain: tuple[Workspace, Handover],
) -> None:
    ws, bootstrap = chain
    forward = _implementation_forward(ws, bootstrap)
    rebase = pr_merge_mod._build_handover(ws, forward, "inv_9", _failed_outcome())  # noqa: SLF001

    assert scenario_binding._legacy_waived(ws, rebase, task_id=None) is True  # noqa: SLF001

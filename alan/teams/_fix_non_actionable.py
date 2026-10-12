from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from alan.block_correction import BlockCorrectionError
from alan.engine_config import ForgeTarget
from alan.handover import Handover, TestScenario
from alan.teams import (
    _fix_escalation,
    _fix_forward,
    _pr_audit_log,
    _pr_flake_ledger,
    _pr_ready,
    _pr_state_paths,
    _scenario_block,
)
from alan.teams import _scenario_rebind_block as _rebind_block
from alan.teams._base import chain_id_for, make_handover_id
from alan.teams._block_correction_audit import record, record_unreachable_attempt
from alan.workspace import Workspace

_TEAM_NAME = _fix_forward.TEAM_NAME
_DEFAULT_FORBIDDEN_ACTIONS = _fix_forward.DEFAULT_FORBIDDEN_ACTIONS
_NON_ACTIONABLE_FENCE_MARKER = "non_actionable_feedback.yaml"


@dataclass(frozen=True)
class _BlockingState:
    contradicted: tuple[str, ...] = ()
    ceiling_reached: bool = False
    head_sha: str = ""


_NO_BLOCKING_STATE = _BlockingState()


def _validate_test_scenarios_block(text: str) -> list[TestScenario]:
    result = _fix_forward.parse_fix_scenarios(text)
    if not result:
        raise BlockCorrectionError(
            "test_scenarios.yaml block present but empty or malformed"
        )
    return result


def _test_scenarios_block_attempted(synthesis: str) -> bool:
    return (
        _scenario_block.SCENARIOS_BLOCK_RE.search(synthesis) is not None
        or _scenario_block.SCENARIOS_HEADING_RE.search(synthesis) is not None
        or _fix_forward._BARE_YAML_BLOCK_RE.search(synthesis) is not None  # pyright: ignore[reportPrivateUsage]
    )


def _maybe_audit_test_scenarios_attempt(
    workspace: Workspace, synthesis: str, extra_scenarios: list[TestScenario]
) -> None:
    if not extra_scenarios and not _test_scenarios_block_attempted(synthesis):
        return
    record_unreachable_attempt(
        site="test_scenarios.yaml",
        role=_TEAM_NAME,
        validate=_validate_test_scenarios_block,
        text=synthesis,
        task_id=workspace.task_id,
    )


def emit_malformed_fence_audits(workspace: Workspace, synthesis: str) -> list[TestScenario]:
    extra_scenarios = _fix_forward.parse_fix_scenarios(synthesis)
    _maybe_audit_test_scenarios_attempt(workspace, synthesis, extra_scenarios)
    unparsed_row = _scenario_block.unparsed_audit_row(
        synthesis, extra_scenarios, task_id=workspace.task_id, team=_TEAM_NAME
    )
    if unparsed_row is not None:
        _pr_audit_log.append_audit(unparsed_row)
    rebinds = _rebind_block.parse_rebinds(synthesis)
    unparsed_rebind_row = _rebind_block.unparsed_rebind_audit_row(
        synthesis, rebinds, task_id=workspace.task_id, team=_TEAM_NAME
    )
    if unparsed_rebind_row is not None:
        _pr_audit_log.append_audit(unparsed_rebind_row)
    return extra_scenarios


def _fix_cycle_ceiling_reached(pr_number: int, forge_target: ForgeTarget) -> bool:
    state = _pr_state_paths.load_fix_cycle_state(pr_number, forge_target)
    return state.count >= _pr_state_paths.fix_cycle_budget(pr_number, forge_target)


def _blocking_state(
    prior_pr: int | None, task_id: str, forge_target: ForgeTarget
) -> _BlockingState:
    if prior_pr is None:
        return _NO_BLOCKING_STATE
    readiness = _pr_ready.readiness_or_unavailable(
        prior_pr, task_id, forge_target=forge_target
    )
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return _NO_BLOCKING_STATE
    contradicted = _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, prior_pr, readiness.head_sha or None, forge_target=forge_target
    )
    if not contradicted:
        return _NO_BLOCKING_STATE
    return _BlockingState(
        contradicted,
        _fix_cycle_ceiling_reached(prior_pr, forge_target),
        readiness.head_sha,
    )


def _non_actionable_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    reason: str,
    blocking: _BlockingState = _NO_BLOCKING_STATE,
) -> Handover:
    iteration = prior.iteration + 1 if prior is not None else 1
    revisit_of = prior.handover_id if prior is not None else None
    test_scenarios = list(prior.test_scenarios) if prior is not None else []
    prior_pr = prior.pr_number if prior is not None else None
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=[],
        iteration=iteration,
        revisit_of=revisit_of,
        timestamp_utc=datetime.now(UTC),
        forbidden_actions=list(_DEFAULT_FORBIDDEN_ACTIONS),
        full_synthesis=synthesis,
        pr_number=prior_pr,
        test_scenarios=test_scenarios,
        open_questions=[
            _fix_escalation.non_actionable_question(
                prior, inv_id, reason,
                contradicted=blocking.contradicted,
                ceiling_reached=blocking.ceiling_reached,
                head_sha=blocking.head_sha,
            )
        ],
    )


def _maybe_audit_non_actionable_block(
    workspace: Workspace, synthesis: str, non_actionable_reason: str | None
) -> None:
    if _NON_ACTIONABLE_FENCE_MARKER not in synthesis:
        return
    outcome = "clean" if non_actionable_reason is not None else "unreachable_author"
    record(
        site=_NON_ACTIONABLE_FENCE_MARKER,
        block=_NON_ACTIONABLE_FENCE_MARKER,
        role=_TEAM_NAME,
        outcome=outcome,
        attempts=1,
        failure_kind=None if non_actionable_reason is not None else "parse_error",
        task_id=workspace.task_id,
    )


def check_non_actionable(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    commits: list[str],
    non_actionable_reason: str | None,
    *,
    authored_body: str | None,
    forge_target: ForgeTarget,
) -> Handover | None:
    _maybe_audit_non_actionable_block(workspace, synthesis, non_actionable_reason)
    if authored_body is not None:
        return None
    if commits or non_actionable_reason is None:
        return None
    emit_malformed_fence_audits(workspace, synthesis)
    _pr_audit_log.append_audit({
        "task_id": workspace.task_id,
        "action": "non_actionable",
        "reason": non_actionable_reason,
        "team": _TEAM_NAME,
    })
    blocking = _blocking_state(
        prior.pr_number if prior is not None else None, workspace.task_id, forge_target
    )
    return _non_actionable_handover(
        workspace, prior, inv_id, synthesis, non_actionable_reason, blocking
    )


__all__ = [
    "check_non_actionable",
    "emit_malformed_fence_audits",
]

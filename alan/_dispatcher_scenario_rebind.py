from __future__ import annotations

import logging
from dataclasses import dataclass, field

from alan import scenario_binding
from alan import workspace as workspace_mod
from alan.block_correction import BlockCorrectionError
from alan.engine_config import EngineConfig
from alan.handover import Handover, TestScenario
from alan.roots import logs_root
from alan.router import RouterDecision
from alan.teams import _pr_forge
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import Team
from alan.teams._block_correction_audit import record_unreachable_attempt
from alan.teams._scenario_rebind_block import (
    REBIND_BLOCK_RE,
    RULE_ALLOWLIST,
    RULE_PROSE_WHEN,
    RULE_REBIND_CAP,
    RULE_TEST_REF_CONFLICT,
    RULE_UNKNOWN_SCENARIO,
    RULE_WAIVED_CONFLICT,
    parse_rebinds,
)
from alan.teams._scenario_runner import (
    _check_allowlist,  # pyright: ignore[reportPrivateUsage]
    _is_prose_rejection,  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import ScenarioBinding, Workspace

logger = logging.getLogger("alan.dispatcher")

_MAX_REBINDS_PER_HANDOVER = 10
_CAP_DETAIL = (
    f"more than {_MAX_REBINDS_PER_HANDOVER} scenario rebinds in one handover; "
    "rejecting the remainder"
)
_TEST_REF_CONFLICT_DETAIL = (
    "scenario_id already carries an operator-set test_ref repoint; a fix rebind "
    "cannot override a deliberate reference repoint"
)
_WAIVED_CONFLICT_DETAIL = (
    "scenario_id is currently waived; a fix rebind cannot un-waive a scenario"
)


@dataclass(frozen=True)
class RebindOutcome:
    accepted: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]


def _audit_row(
    action: str,
    workspace_id: str,
    scenario_id: str,
    *,
    when: str | None,
    reason: str | None,
    rule: str | None = None,
) -> None:
    append_jsonl_audit(
        logs_root() / scenario_binding.AUDIT_FILE,
        {
            "kind": "binding",
            "action": action,
            "workspace_id": workspace_id,
            "scenario_id": scenario_id,
            "when": when,
            "test_ref": None,
            "waived": None,
            "reason": reason,
            "rule": rule,
        },
        log_label="scenario_binding_override",
    )


def _reject(ws: Workspace, sid: str, cmd: str, rule: str, reason: str) -> None:
    _audit_row("fix_rebind_rejected", ws.workspace_id, sid, when=cmd, reason=reason, rule=rule)


def _accept(ws: Workspace, sid: str, cmd: str) -> None:
    _audit_row("fix_rebind", ws.workspace_id, sid, when=cmd, reason="fix_rebind")


def _pre_checks_rejection(
    sid: str,
    base_by_id: dict[str, TestScenario],
    cmd: str,
    cfg: EngineConfig | None,
    *,
    validated_count: int,
) -> tuple[str, str] | None:
    if sid not in base_by_id:
        return RULE_UNKNOWN_SCENARIO, scenario_binding.UNKNOWN_SCENARIO_DETAIL
    allowlist_diag = _check_allowlist(cmd, cfg)
    if allowlist_diag is not None:
        if _is_prose_rejection(cmd, cfg):
            return RULE_PROSE_WHEN, scenario_binding.PROSE_WHEN_DETAIL
        return RULE_ALLOWLIST, allowlist_diag
    if validated_count >= _MAX_REBINDS_PER_HANDOVER:
        return RULE_REBIND_CAP, _CAP_DETAIL
    return None


def _conflict_rejection(existing: ScenarioBinding | None) -> tuple[str, str] | None:
    if existing is None:
        return None
    if existing.waived:
        return RULE_WAIVED_CONFLICT, _WAIVED_CONFLICT_DETAIL
    if existing.test_ref is not None:
        return RULE_TEST_REF_CONFLICT, _TEST_REF_CONFLICT_DETAIL
    return None


def _persist_rebind(ws: Workspace, sid: str, cmd: str) -> Workspace:
    existing = ws.meta.scenario_bindings.get(sid)
    binding = (
        existing.model_copy(
            update={"when": cmd, "written_by": "fix_rebind", "reason": "fix_rebind"}
        )
        if existing is not None
        else ScenarioBinding(when=cmd, written_by="fix_rebind", reason="fix_rebind")
    )
    return workspace_mod.update_scenario_binding(ws, sid, binding)


def _validate_rebind_block(text: str) -> dict[str, str]:
    result = parse_rebinds(text)
    if not result:
        raise BlockCorrectionError(
            "scenario_rebind.yaml block present but empty or malformed"
        )
    return result


def _maybe_audit_rebind_attempt(synthesis: str, *, task_id: str) -> None:
    if REBIND_BLOCK_RE.search(synthesis) is None:
        return
    record_unreachable_attempt(
        site="scenario_rebind.yaml",
        role="implementation_fix",
        validate=_validate_rebind_block,
        text=synthesis,
        task_id=task_id,
    )


def apply_rebinds(
    ws: Workspace, handover: Handover, task_id: str
) -> tuple[Workspace, RebindOutcome]:
    if handover.from_team != "implementation_fix":
        return ws, RebindOutcome()
    _maybe_audit_rebind_attempt(handover.full_synthesis, task_id=task_id)
    rebinds = parse_rebinds(handover.full_synthesis)
    if not rebinds:
        return ws, RebindOutcome()
    base_by_id = {
        s.id: s
        for s in scenario_binding.base_scenarios(ws, handover, task_id=task_id)
    }
    cfg = _pr_forge.governing_config_for_ws(ws)
    accepted: dict[str, str] = {}
    validated_count = 0
    for sid, cmd in rebinds.items():
        pre_check = _pre_checks_rejection(
            sid, base_by_id, cmd, cfg, validated_count=validated_count,
        )
        if pre_check is not None:
            rule, reason = pre_check
            _reject(ws, sid, cmd, rule, reason)
            continue
        validated_count += 1
        conflict = _conflict_rejection(ws.meta.scenario_bindings.get(sid))
        if conflict is not None:
            rule, reason = conflict
            _reject(ws, sid, cmd, rule, reason)
            continue
        ws = _persist_rebind(ws, sid, cmd)
        accepted[sid] = cmd
        _accept(ws, sid, cmd)
    return ws, RebindOutcome(accepted=accepted)


def route_after_rebind(
    decision: RouterDecision,
    handover: Handover,
    outcome: RebindOutcome,
    teams: dict[str, Team],
) -> RouterDecision:
    if not outcome.accepted or decision.kind != "escalate":
        return decision
    if handover.from_team != "implementation_fix" or "verification" not in teams:
        return decision
    return RouterDecision(
        kind="enqueue",
        team="verification",
        reason="scenario rebind accepted; routing to verification",
    )


def _skip_keep_manifest(handover: Handover, decision: RouterDecision) -> bool:
    return (
        handover.from_team != "decomposer"
        or decision.team != "implementation"
        or not handover.files_to_edit
    )


def maybe_populate_keep_manifest(
    ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
) -> Workspace:
    if _skip_keep_manifest(handover, decision):
        return ws
    task = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if task is None:
        return ws
    normalized = list(dict.fromkeys(f.strip() for f in handover.files_to_edit))
    return workspace_mod.update_task_files(ws, task_id, normalized)


__all__ = [
    "RebindOutcome",
    "apply_rebinds",
    "maybe_populate_keep_manifest",
    "route_after_rebind",
]

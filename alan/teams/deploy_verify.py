from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from alan import deploy_observe, deploy_signal
from alan import deploy_runs as deploy_runs_mod
from alan import engine_config as _engine_config_mod
from alan import fleet_timing as fleet_timing_mod
from alan import workflow as workflow_mod
from alan.claude_cli import OnProcStarted
from alan.deploy_runs import parse_iso_datetime as _parse_iso_datetime
from alan.deploy_verify import DeployObservation, GateRecord, is_live, waited_s
from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge
from alan.handover import DeployWaitSignal, Handover
from alan.teams import (
    _deploy_restart_probe,
    _forge_errors,
    _never_live_budget,
    _pr_audit_log,
    _pr_forge,
)
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import (
    EmitFn,
    build_terminal_handover,
    chain_id_for,
    emit_node,
    make_handover_id,
    reject_resume_inv_id,
    workflow_lifecycle,
)
from alan.teams._env import float_env
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, mark_delivered

logger = logging.getLogger(__name__)

_TEAM_NAME = "deploy_verify"
_NODE_ROLE = "deploy_verify"
_MODEL = "none"
_AUDIT_FILE = "deploy_verify_audit.jsonl"
_DRY_RUN_ENV = "ALAN_DEPLOY_VERIFY_DRY_RUN"
_AUDIT_DISABLE_ENV = "ALAN_DEPLOY_VERIFY_AUDIT_DISABLE"
_MERGE_RACE_BUDGET_ENV = "ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S"
_HEALTH_URL_ENV = "ALAN_HEALTH_URL"
_GH_TIMEOUT_S = 60.0
_DEFAULT_HEALTH_URL = "http://127.0.0.1:8585/api/health"
_DEFAULT_MERGE_RACE_BUDGET_S = 300.0
_TRUTHY = frozenset({"1", "true", "yes", "on"})

OutcomeKind = Literal["no_pr", "confirmed", "escalate", "wait"]


@dataclass(frozen=True)
class DeployVerifyOutcome:
    kind: OutcomeKind
    reason: str
    pr_number: int | None
    deadline: float | None = None
    race_deadline: float | None = None
    merge_sha: str | None = None
    merged_at: datetime | None = None
    repo: str | None = None


@dataclass(frozen=True)
class _PrMergeInfo:
    gh_ok: bool
    state: str | None
    merged_at: datetime | None
    merge_sha: str | None = None


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in _TRUTHY


def _audit_disabled() -> bool:
    return os.environ.get(_AUDIT_DISABLE_ENV, "").strip().lower() in _TRUTHY


def _audit(entry: dict[str, object]) -> None:
    if _audit_disabled():
        return
    append_jsonl_audit(
        _pr_audit_log.audit_dir() / _AUDIT_FILE, entry, log_label="deploy_verify"
    )


def _budget_s() -> float:
    return _never_live_budget.effective_budget_s()


def _merge_race_budget_s() -> float:
    return float_env(_MERGE_RACE_BUDGET_ENV, _DEFAULT_MERGE_RACE_BUDGET_S)


def _health_url() -> str:
    val = os.environ.get(_HEALTH_URL_ENV, "").strip()
    return val if val else _DEFAULT_HEALTH_URL


_RESTART_WINDOW_S: float = 600.0


def restart_window_s() -> float:
    return _RESTART_WINDOW_S


def _restart_free_terms(pr_number: int, window: float) -> tuple[str, float | None, str]:
    restart_free, reason, merge_sha = _deploy_restart_probe.classify_merge(pr_number)
    _audit({
        "action": "restart_gate_decision",
        "pr_number": pr_number,
        "reason": reason,
        "deferred": not restart_free,
    })
    if restart_free:
        record = GateRecord(merge_sha=merge_sha, path="restart_free", deferred_at=None)
        return (
            f"post-merge deploy gate for PR #{pr_number} — restart-free deploy "
            f"({reason}) — verifying now",
            None,
            record.to_json(),
        )
    if merge_sha is not None and deploy_signal.merge_is_served(merge_sha):
        record = GateRecord(merge_sha=merge_sha, path="signal", deferred_at=None)
        return (
            f"post-merge deploy gate for PR #{pr_number} — the live service already "
            f"serves this merge — verifying now",
            None,
            record.to_json(),
        )
    deferred_at = time.time()
    record = GateRecord(merge_sha=merge_sha, path="ceiling", deferred_at=deferred_at)
    return (
        f"post-merge deploy gate for PR #{pr_number} — deferred "
        f"{int(window)}s past the deploy restart window ({reason})",
        deferred_at + window,
        record.to_json(),
    )


def restart_gate_terms(pr_number: int) -> tuple[str, float | None, str | None]:
    window = 0.0 if dry_run_enabled() else restart_window_s()
    if window <= 0:
        return f"post-merge deploy gate for PR #{pr_number}", None, None
    return _restart_free_terms(pr_number, window)


class DeployVerifyTeam:
    name = _TEAM_NAME
    supports_resume = False
    restart_rederivable = True

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)]

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del task_id, on_proc_started
        reject_resume_inv_id(self.name, resume_inv_id)
        prior_pr = prior.pr_number if prior is not None else None
        if prior_pr is None:
            return _no_pr_handover_with_workflow(workspace, prior, emit, inv_id)
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
            workflow_mod.save_workflow(workspace, graph.doc)
            emit_node(emit, "node_started", inv_id, node)
            outcome = _evaluate_once(workspace, prior_pr, instructions)
            done = graph.complete_node(_NODE_ROLE)
            workflow_mod.save_workflow(workspace, graph.doc)
            emit_node(emit, "node_completed", inv_id, done)
        return _build_handover(workspace, prior, inv_id, outcome)


TEAM = DeployVerifyTeam()


def _no_pr_handover_with_workflow(
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
) -> Handover:
    with workflow_lifecycle(
        workspace, team_name=_TEAM_NAME, inv_id=inv_id, emit=emit
    ) as graph:
        node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
        workflow_mod.save_workflow(workspace, graph.doc)
        emit_node(emit, "node_started", inv_id, node)
        done = graph.complete_node(_NODE_ROLE)
        workflow_mod.save_workflow(workspace, graph.doc)
        emit_node(emit, "node_completed", inv_id, done)
    outcome = DeployVerifyOutcome(
        kind="no_pr",
        pr_number=None,
        reason="deploy_verify dispatched without a PR number — escalating",
    )
    return _build_handover(workspace, prior, inv_id, outcome)


def _dry_run_outcome(workspace: Workspace, pr_number: int) -> DeployVerifyOutcome:
    _audit({
        "task_id": workspace.task_id,
        "action": "dry_run_deploy",
        "pr_number": pr_number,
    })
    return DeployVerifyOutcome(
        kind="confirmed",
        pr_number=pr_number,
        reason=f"PR #{pr_number}: deploy verification skipped (dry run)",
    )


def _audit_verify_start(pr_number: int, gate_instructions: str | None) -> None:
    record = GateRecord.from_json(gate_instructions)
    path = record.path if record is not None else "ceiling"
    _audit({
        "action": "verify_start",
        "pr_number": pr_number,
        "path": path,
        "waited_s": round(waited_s(record, now=time.time()), 1),
    })


def _default_repo_slug() -> str:
    cfg = _engine_config_mod.default_engine_config()
    return f"{cfg.repo.owner}/{cfg.repo.repo}"


def _pipeline_available_for(workspace: Workspace) -> bool:
    return _repo_slug(workspace) == _default_repo_slug()


def _no_pipeline_outcome(workspace: Workspace, pr_number: int) -> DeployVerifyOutcome:
    repo = _repo_slug(workspace)
    _audit({
        "task_id": workspace.task_id,
        "action": "deploy_verify_skipped_no_pipeline",
        "pr_number": pr_number,
        "repo": repo,
    })
    return DeployVerifyOutcome(
        kind="confirmed",
        pr_number=pr_number,
        reason=(
            f"PR #{pr_number}: {repo} has no deploy pipeline for this engine — "
            "deploy verification skipped (deploy_verify_skipped_no_pipeline)"
        ),
    )


def _evaluate_once(
    workspace: Workspace, pr_number: int, gate_instructions: str | None = None
) -> DeployVerifyOutcome:
    if not _pipeline_available_for(workspace):
        return _no_pipeline_outcome(workspace, pr_number)
    if dry_run_enabled():
        return _dry_run_outcome(workspace, pr_number)
    _audit_verify_start(pr_number, gate_instructions)
    record = GateRecord.from_json(gate_instructions)
    info = _pr_merge_info(pr_number, forge_target=_pr_forge.forge_target_for_ws(workspace))
    if info.merged_at is None:
        return _merge_race_step(workspace, pr_number, record, info)
    return _merged_step(
        workspace, pr_number, record, info.merged_at, _health_url(), info.merge_sha
    )


def _format_utc_deadline(deadline: float) -> str:
    return datetime.fromtimestamp(deadline, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_slug(workspace: Workspace) -> str:
    forge_target = _pr_forge.forge_target_for_ws(workspace)
    return f"{forge_target.repo.owner}/{forge_target.repo.repo}"


def _merged_wait_reason(pr_number: int, deadline: float) -> str:
    return (
        f"PR #{pr_number}: confirming the merge is live via local observation — will "
        f"conclude it never went live and escalate at {_format_utc_deadline(deadline)} UTC"
    )


def _merge_race_wait_reason(pr_number: int, race_deadline: float) -> str:
    return (
        f"PR #{pr_number}: mergedAt not yet available (merge/forge race) — will "
        f"escalate at {_format_utc_deadline(race_deadline)} UTC if it never resolves"
    )


def _observe(merge_sha: str, url: str) -> DeployObservation:
    return deploy_observe.observe(merge_sha, health_url=url)


def _resolve_deadline(record: GateRecord | None) -> float:
    if record is not None and record.deadline is not None:
        return record.deadline
    return time.time() + _budget_s()


def _resolve_merge_sha(record: GateRecord | None, hint: str | None = None) -> str | None:
    if record is not None and record.merge_sha is not None:
        return record.merge_sha
    return hint


def _merged_step(
    workspace: Workspace,
    pr_number: int,
    record: GateRecord | None,
    merged_at: datetime,
    url: str,
    merge_sha_hint: str | None = None,
) -> DeployVerifyOutcome:
    deadline = _resolve_deadline(record)
    merge_sha = _resolve_merge_sha(record, merge_sha_hint)
    elapsed = (datetime.now(UTC) - merged_at).total_seconds()
    budget = deadline - merged_at.timestamp()
    observation = _observe(merge_sha, url) if merge_sha is not None else None
    if observation is not None and is_live(observation):
        return _confirmed_outcome(workspace, pr_number, elapsed)
    if time.time() >= deadline:
        return _never_live_outcome(workspace, pr_number, elapsed, budget, observation)
    return DeployVerifyOutcome(
        kind="wait",
        pr_number=pr_number,
        reason=_merged_wait_reason(pr_number, deadline),
        deadline=deadline,
        merge_sha=merge_sha,
        merged_at=merged_at,
        repo=_repo_slug(workspace),
    )


def _is_merge_race(info: _PrMergeInfo) -> bool:
    return info.state == "MERGED" or not info.gh_ok


def _merge_race_step(
    workspace: Workspace,
    pr_number: int,
    record: GateRecord | None,
    info: _PrMergeInfo,
) -> DeployVerifyOutcome:
    if not _is_merge_race(info):
        return _escalate_no_merged_at(workspace, pr_number, info, 0.0)
    race_budget = _merge_race_budget_s()
    race_deadline = (
        record.race_deadline
        if record is not None and record.race_deadline is not None
        else time.time() + race_budget
    )
    merge_sha = _resolve_merge_sha(record, info.merge_sha)
    now = time.time()
    if now >= race_deadline:
        race_elapsed = now - race_deadline + race_budget
        return _escalate_no_merged_at(workspace, pr_number, info, race_elapsed)
    return DeployVerifyOutcome(
        kind="wait",
        pr_number=pr_number,
        reason=_merge_race_wait_reason(pr_number, race_deadline),
        race_deadline=race_deadline,
        merge_sha=merge_sha,
    )


def _no_merged_at_variant(info: _PrMergeInfo) -> str:
    if info.state == "CLOSED":
        return "closed"
    if info.state == "OPEN":
        return "open"
    return "race_budget_exhausted"


def _no_merged_at_class(info: _PrMergeInfo, race_elapsed: float) -> tuple[str, str]:
    if info.state == "CLOSED":
        return (
            "PR closed without merging",
            "confirm the close was intentional; re-dispatch pr_merge if it should still land",
        )
    if info.state == "OPEN":
        return (
            "PR still open (mergedAt missing)",
            "verify the merge actually completed on GitHub; re-dispatch pr_merge if not",
        )
    return (
        f"mergedAt still unavailable after {round(race_elapsed)}s merge/forge-race budget "
        f"(state={_unavailable_state_desc(info)})",
        "check gh auth status and GitHub API availability, then re-dispatch deploy_verify",
    )


def _unavailable_state_desc(info: _PrMergeInfo) -> str:
    if info.state is not None:
        return info.state
    return "gh call failed" if not info.gh_ok else "state missing from gh response"


def _escalate_no_merged_at(
    workspace: Workspace,
    pr_number: int,
    info: _PrMergeInfo,
    race_elapsed: float,
) -> DeployVerifyOutcome:
    label, recipe = _no_merged_at_class(info, race_elapsed)
    _audit({
        "task_id": workspace.task_id,
        "action": "escalate_no_merged_at",
        "pr_number": pr_number,
        "state": info.state,
        "gh_ok": info.gh_ok,
        "variant": _no_merged_at_variant(info),
    })
    return DeployVerifyOutcome(
        kind="escalate",
        pr_number=pr_number,
        reason=f"PR #{pr_number}: {label} — {recipe}",
    )


def _diagnostic_detail(workspace: Workspace) -> str:
    workflow = _pr_forge.deploy_workflow_for_ws(workspace)
    if workflow is None:
        return "no deploy workflow configured"
    try:
        runs = deploy_runs_mod.fetch_deploy_runs(
            _pr_forge.forge_target_for_ws(workspace), workflow
        )
    except Exception:  # noqa: BLE001 - diagnostics are best-effort; never block the escalation
        return "deploy-run diagnostics unavailable"
    label = Path(workflow).stem
    if not runs:
        return f"no {label} runs observed"
    latest = max(runs, key=lambda r: r.created_at)
    return (
        f"latest {label} run status={latest.status} "
        f"conclusion={latest.conclusion or 'pending'}"
    )


def _never_live_outcome(
    workspace: Workspace,
    pr_number: int,
    elapsed: float,
    budget: float,
    observation: DeployObservation | None,
) -> DeployVerifyOutcome:
    arm_note = f", arm={observation.arm}" if observation is not None else ""
    detail = _diagnostic_detail(workspace)
    reason = (
        f"PR #{pr_number}: merge never went live — local observation did not confirm "
        f"within budget ({round(elapsed)}s elapsed, budget {round(budget)}s{arm_note}) — "
        f"deploy-run diagnostic: {detail}"
    )
    _audit({
        "task_id": workspace.task_id,
        "action": "deploy_escalate",
        "pr_number": pr_number,
        "elapsed_s": round(elapsed, 1),
        "variant": "never_live",
    })
    return DeployVerifyOutcome(kind="escalate", pr_number=pr_number, reason=reason)


def _mark_delivered(workspace: Workspace, *, live_at: datetime | None = None) -> None:
    active = fleet_timing_mod.active_fleet_time_for_workspace(workspace.path)
    settled = active.live_seconds(datetime.now(UTC))
    mark_delivered(workspace, settled_active_seconds=settled, live_at=live_at)


def _confirmed_outcome(
    workspace: Workspace, pr_number: int, elapsed: float
) -> DeployVerifyOutcome:
    _mark_delivered(workspace, live_at=datetime.now(UTC))
    _audit({
        "task_id": workspace.task_id,
        "action": "deploy_confirmed",
        "pr_number": pr_number,
        "elapsed_s": round(elapsed, 1),
    })
    return DeployVerifyOutcome(
        kind="confirmed",
        pr_number=pr_number,
        reason=(
            f"PR #{pr_number}: confirmed live via local observation (elapsed {round(elapsed)}s)"
        ),
    )


def _pr_merge_info(pr_number: int, *, forge_target: ForgeTarget) -> _PrMergeInfo:
    context = f"read_pr PR #{pr_number} (state,mergedAt,mergeCommit)"
    try:
        read = active_forge().read_pr(
            pr_number,
            ("state", "mergedAt", "mergeCommit"),
            forge_target=forge_target,
            timeout=_GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _forge_errors.ForgeTransientError(f"{context} raised {exc}") from exc
    if not read.ok:
        _forge_errors.raise_for_infra(
            context, returncode=read.returncode, stderr=read.stderr,
        )
        return _PrMergeInfo(gh_ok=False, state=None, merged_at=None)
    return _merge_info_from_fields(read.fields)


def _extract_merge_sha(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    oid = cast(dict[str, Any], raw).get("oid")
    return oid if isinstance(oid, str) else None


def _merge_info_from_fields(fields: JsonObject) -> _PrMergeInfo:
    if not fields:
        return _PrMergeInfo(gh_ok=False, state=None, merged_at=None)
    state_raw = fields.get("state")
    state = state_raw if isinstance(state_raw, str) else None
    merged_at_raw = fields.get("mergedAt")
    merged_at = (
        _parse_iso_datetime(merged_at_raw) if isinstance(merged_at_raw, str) else None
    )
    merge_sha = _extract_merge_sha(fields.get("mergeCommit"))
    return _PrMergeInfo(gh_ok=True, state=state, merged_at=merged_at, merge_sha=merge_sha)


def _build_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: DeployVerifyOutcome,
) -> Handover:
    if outcome.kind == "confirmed":
        return build_terminal_handover(
            workspace,
            from_team=_TEAM_NAME,
            summary=outcome.reason,
            prior=prior,
            invocation_id=inv_id,
            pr_number=outcome.pr_number,
        )
    if outcome.kind == "wait":
        return _wait_handover(workspace, prior, inv_id, outcome)
    return _escalation_handover(workspace, prior, inv_id, outcome)


def _wait_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: DeployVerifyOutcome,
) -> Handover:
    handover = build_terminal_handover(
        workspace,
        from_team=_TEAM_NAME,
        summary=outcome.reason,
        prior=prior,
        invocation_id=inv_id,
        pr_number=outcome.pr_number,
    )
    deadline = outcome.deadline if outcome.deadline is not None else outcome.race_deadline
    return handover.model_copy(
        update={
            "deploy_wait": DeployWaitSignal(
                merge_sha=outcome.merge_sha,
                deadline=cast(float, deadline),
                race_deadline=outcome.race_deadline,
                reason=outcome.reason,
                merged_at=(
                    outcome.merged_at.timestamp() if outcome.merged_at is not None else None
                ),
                repo=outcome.repo,
            )
        }
    )


def _escalation_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: DeployVerifyOutcome,
) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="escalation",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=[],
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=outcome.reason,
        pr_number=outcome.pr_number,
    )


__all__ = [
    "TEAM",
    "DeployVerifyOutcome",
    "DeployVerifyTeam",
    "_parse_iso_datetime",
    "dry_run_enabled",
    "restart_gate_terms",
    "restart_window_s",
]

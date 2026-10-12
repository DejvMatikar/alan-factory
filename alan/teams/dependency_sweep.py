from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from alan import dispatch_provenance as provenance_mod
from alan import store
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dep_sweep import (
    PeerCapBlock,
    Proposal,
    ProposalBatch,
    ProposalClass,
    classify_proposal,
    group_proposals,
    merge_target,
    parse_peer_cap,
)
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import (
    ForgeTarget,
    Posture,
    canonical_scope_key,
    live_floored_posture,
    live_hidden,
)
from alan.forge import JsonObject, active_forge
from alan.handover import Handover, save_handover
from alan.queue import JobStatus, Queue
from alan.roots import logs_root
from alan.teams import _pr_branch, dep_advisory
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import (
    EmitFn,
    build_terminal_handover,
    emit_node,
    make_handover_id,
    reject_resume_inv_id,
    workflow_lifecycle,
)
from alan.teams._pr_forge import (
    GH_TIMEOUT_S,
    forge_target_for_ws,
    governing_config_for_ws,
)
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Workspace

logger = logging.getLogger(__name__)

_TEAM_NAME = "dependency_sweep"
_NODE_ROLE = "sweep_runner"
_MODEL = "none"
_CADENCE_META_KEY = "dep_sweep_last_scheduled_at"
_DEPENDABOT_AUTHOR = "app/dependabot"
_PROPOSAL_FIELDS = ("number", "mergeable", "title")
_PROPOSAL_LIMIT = 100
_AUDIT_FILE = "dep_sweep_audit.jsonl"
_UV_LOCK_NAME = "uv.lock"
_STATUS_FILE = "dep_sweep_status.json"
_OUTCOME_SUCCESS = "success"
_OUTCOME_FAILURE = "failure"


@dataclass(frozen=True)
class SweepContext:
    forge: ForgeTarget
    run_vuln_scan: bool
    posture: Posture = Posture.autonomous


class DependencySweepStatus(BaseModel, frozen=True):
    slug: str
    enabled: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_outcome: str | None = None
    last_duration_seconds: float | None = None


def _status_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _STATUS_FILE


def _save_dependency_sweep_status(forge: ForgeTarget, status: DependencySweepStatus) -> None:
    path = _status_path(forge)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_mkstemp(path, status.model_dump_json(), prefix=".dep_sweep_status_")
    except OSError:
        logger.warning("dep_sweep: failed to persist status for %s", forge.repo.slug)


def load_dependency_sweep_status(
    forge: ForgeTarget,
) -> DependencySweepStatus | None:
    try:
        raw = _status_path(forge).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return DependencySweepStatus.model_validate_json(raw)
    except ValidationError:
        return None


_PR_MERGE_SPEC = PendingTaskSpec(id="pr_merge", role="pr_merge", model="none")
_IMPL_FIX_SPEC = PendingTaskSpec(
    id="implementer",
    role="implementer",
    model=_ENGINE_DEFAULT.roles["implementer"].model,
    effort=_ENGINE_DEFAULT.roles["implementer"].effort,
)
ROLE_SPEC = PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)


def _fetch_open_proposals(*, ctx: SweepContext) -> tuple[JsonObject, ...]:
    return active_forge().open_prs_by_author(
        _DEPENDABOT_AUTHOR, _PROPOSAL_FIELDS, forge_target=ctx.forge, limit=_PROPOSAL_LIMIT,
    )


def _collect_paths(files: list[object]) -> list[str]:
    paths: list[str] = []
    for f in files:
        if isinstance(f, dict):
            path: Any = cast(dict[str, Any], f).get("path")
            if isinstance(path, str):
                paths.append(path)
    return paths


def _fetch_pr_files(pr_number: int, *, ctx: SweepContext) -> list[str]:
    try:
        read = active_forge().read_pr(
            pr_number, ("files",), forge_target=ctx.forge, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    files = read.fields.get("files")
    if not read.ok or not isinstance(files, list):
        return []
    return _collect_paths(cast("list[object]", files))


_NPM_LOCKFILE_NAMES = frozenset({"package-lock.json", "yarn.lock"})


def _detect_upstream_block(
    pr_number: int, timeout_s: float, *, ctx: SweepContext
) -> PeerCapBlock | None:
    for job_id in _pr_branch.fetch_failing_job_ids(
        pr_number, timeout_s, forge_target=ctx.forge
    ):
        block = parse_peer_cap(
            _pr_branch.fetch_failed_log(job_id, timeout_s, forge_target=ctx.forge)
        )
        if block is not None:
            return block
    return None


_LOCKFILE_NAMES = frozenset({"uv.lock", "package-lock.json", "yarn.lock", "poetry.lock"})


def _touches_npm_lockfile(files: list[str]) -> bool:
    return any(Path(p).name in _NPM_LOCKFILE_NAMES for p in files)


def _lockfiles_in(paths: list[str]) -> frozenset[str]:
    return frozenset(p for p in paths if Path(p).name in _LOCKFILE_NAMES)


def _to_proposals(
    raw_prs: tuple[JsonObject, ...], timeout_s: float, *, ctx: SweepContext
) -> list[Proposal]:
    result: list[Proposal] = []
    for raw in raw_prs:
        number = raw.get("number")
        if not isinstance(number, int):
            continue
        mergeable = raw.get("mergeable") or "UNKNOWN"
        if not isinstance(mergeable, str):
            mergeable = "UNKNOWN"
        files = _fetch_pr_files(number, ctx=ctx)
        block = (
            _detect_upstream_block(number, timeout_s, ctx=ctx)
            if _touches_npm_lockfile(files)
            else None
        )
        result.append(Proposal(number=number, state="open", mergeable=mergeable,
                               lockfiles=_lockfiles_in(files), upstream_block=block))
    return result


def _find_workspace_for_pr(
    pr_number: int, workspaces_root: Path | None, *, engine_key: str | None = None
) -> Workspace | None:
    title = f"dep/{pr_number}"
    scope = canonical_scope_key(engine_key)
    try:
        all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    except Exception:  # noqa: BLE001
        return None
    return next(
        (
            ws
            for ws in all_ws
            if ws.meta.title == title and canonical_scope_key(ws.meta.engine_key) == scope
        ),
        None,
    )


def _is_in_flight(
    pr_number: int, queue: Queue, workspaces_root: Path | None, *, engine_key: str | None = None
) -> bool:
    ws = _find_workspace_for_pr(pr_number, workspaces_root, engine_key=engine_key)
    if ws is None:
        return False
    pending = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    running = queue.list_jobs(task_id=ws.task_id, status=JobStatus.RUNNING)
    return bool(pending or running)


_TERMINAL_DEP_STATUSES = frozenset(
    {Status.AWAITING_INPUT, Status.ESCALATED, Status.FAILED, Status.PAUSED}
)


def _needs_human(
    pr_number: int, workspaces_root: Path | None, *, engine_key: str | None = None
) -> bool:
    ws = _find_workspace_for_pr(pr_number, workspaces_root, engine_key=engine_key)
    return ws is not None and ws.meta.status in _TERMINAL_DEP_STATUSES


def _ensure_pr_workspace(
    pr_number: int, workspaces_root: Path | None, *, engine_key: str | None = None
) -> Workspace:
    existing = _find_workspace_for_pr(pr_number, workspaces_root, engine_key=engine_key)
    if existing is not None:
        return existing
    return workspace_mod.create(
        f"dep/{pr_number}",
        f"Dependabot dependency update PR #{pr_number}",
        team=_TEAM_NAME,
        root=workspaces_root,
        engine_key=engine_key,
        provenance=provenance_mod.DispatchProvenance(
            surface=provenance_mod.DispatchSurface.DEP_SWEEP_PR
        ),
    )


def _make_bootstrap_handover(workspace: Workspace, pr_number: int) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=new_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id="dep_sweep_bootstrap",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"Dependabot dependency update PR #{pr_number}",
        pr_number=pr_number,
        dependency_bot_authorized=True,
    )


def _team_specs(team_name: str) -> list[PendingTaskSpec]:
    if team_name == "pr_merge":
        return [_PR_MERGE_SPEC]
    return [_IMPL_FIX_SPEC]


def _dispatch_proposal(
    pr_number: int,
    proposal_class: ProposalClass,
    queue: Queue,
    workspaces_root: Path | None,
    *,
    engine_key: str | None = None,
) -> None:
    target = merge_target(proposal_class)
    if target is None and proposal_class == ProposalClass.MERGE_READY:
        return
    team_name = target if target is not None else "implementation_fix"
    ws = _ensure_pr_workspace(pr_number, workspaces_root, engine_key=engine_key)
    bootstrap = _make_bootstrap_handover(ws, pr_number)
    save_handover(ws, bootstrap)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team=team_name, tasks=_team_specs(team_name), source_task_id=ws.task_id,
    )
    queue.enqueue(ws.task_id, team_name,
                  parent_handover_id=bootstrap.handover_id,
                  triggered_by_invocation_id=inv_id)


def _escalation_message(pr_number: int, block: PeerCapBlock) -> str:
    return (
        f"PR #{pr_number} bumps `{block.dependency}` but it is blocked by an upstream "
        f"peer cap: `{block.capping_package}` requires `{block.dependency} "
        f"{block.peer_range}`. `npm ci` fails ERESOLVE — no lockfile rebase can fix this.\n\n"
        "Recommended (matches the eslint precedent, PR #402): close the PR and add to "
        ".github/dependabot.yml under the /alan/web npm updates:\n"
        f'    - dependency-name: "{block.dependency}"\n'
        '      update-types: ["version-update:semver-major"]'
    )


def _escalate_upstream_blocked(
    pr_number: int, proposal: Proposal, workspaces_root: Path | None,
    *, engine_key: str | None = None,
) -> None:
    block = cast(PeerCapBlock, proposal.upstream_block)
    ws = _ensure_pr_workspace(pr_number, workspaces_root, engine_key=engine_key)
    handover = build_terminal_handover(
        ws,
        from_team=_TEAM_NAME,
        summary=_escalation_message(pr_number, block),
        prior=None,
        invocation_id="dep_sweep_escalation",
        pr_number=pr_number,
    )
    save_handover(ws, handover)
    workspace_mod.transition(ws, Status.ESCALATED)


def _act_on_proposal(
    number: int,
    proposal: Proposal,
    queue: Queue,
    workspaces_root: Path | None,
    audit: list[dict[str, Any]],
    *,
    engine_key: str | None = None,
) -> bool:
    pc = classify_proposal(proposal)
    if pc == ProposalClass.UPSTREAM_BLOCKED:
        _escalate_upstream_blocked(number, proposal, workspaces_root, engine_key=engine_key)
        audit.append({"action": "escalate_upstream_blocked", "pr_number": number})
        return False
    _dispatch_proposal(number, pc, queue, workspaces_root, engine_key=engine_key)
    audit.append({"action": "dispatch", "pr_number": number, "proposal_class": pc.value})
    return True


def _batch_waits_for_advisory_fix(
    batch: ProposalBatch, workspaces_root: Path | None, *, ctx: SweepContext
) -> bool:
    if not any(Path(p).name == _UV_LOCK_NAME for p in batch.lockfiles):
        return False
    return dep_advisory.fix_in_flight(ctx.forge, workspaces_root)


def _batch_blocked(
    batch: ProposalBatch,
    queue: Queue,
    workspaces_root: Path | None,
    *,
    ctx: SweepContext,
    engine_key: str | None,
) -> bool:
    if _batch_waits_for_advisory_fix(batch, workspaces_root, ctx=ctx):
        return True
    return any(
        _is_in_flight(n, queue, workspaces_root, engine_key=engine_key)
        for n in batch.proposal_numbers
    )


def _dispatch_batch(
    batch: ProposalBatch,
    proposals_by_num: dict[int, Proposal],
    queue: Queue,
    workspaces_root: Path | None,
    audit: list[dict[str, Any]],
    *,
    ctx: SweepContext,
    engine_key: str | None = None,
) -> None:
    if _batch_blocked(batch, queue, workspaces_root, ctx=ctx, engine_key=engine_key):
        return
    for number in batch.proposal_numbers:
        proposal = proposals_by_num.get(number)
        if proposal is None:
            continue
        if _needs_human(number, workspaces_root, engine_key=engine_key):
            continue
        if _act_on_proposal(number, proposal, queue, workspaces_root, audit, engine_key=engine_key):
            return


def _run_vuln_scan(
    queue: Queue,
    workspaces_root: Path | None,
    audit: list[dict[str, Any]],
    *,
    ctx: SweepContext,
    engine_key: str | None,
    trigger: str,
) -> str:
    if not ctx.run_vuln_scan:
        return ""
    return dep_advisory.run_advisory_check(
        forge=ctx.forge,
        posture=ctx.posture,
        queue=queue,
        workspaces_root=workspaces_root,
        engine_key=engine_key,
        trigger=trigger,
        audit=audit,
    )


def _run_advisory_only(
    queue: Queue,
    workspaces_root: Path | None,
    audit: list[dict[str, Any]],
    *,
    ctx: SweepContext,
    engine_key: str | None,
) -> str:
    if not ctx.run_vuln_scan:
        audit.append({"action": "vuln_scan_disabled", "trigger": dep_advisory.TRIGGER_MAIN_CI_RED})
        return "dep_sweep advisory check skipped: python vulnerability scan is not enabled"
    return _run_vuln_scan(
        queue, workspaces_root, audit, ctx=ctx, engine_key=engine_key,
        trigger=dep_advisory.TRIGGER_MAIN_CI_RED,
    )


def _run_sweep(
    queue: Queue,
    workspaces_root: Path | None,
    audit: list[dict[str, Any]],
    *,
    ctx: SweepContext,
    engine_key: str | None = None,
) -> str:
    _run_vuln_scan(
        queue, workspaces_root, audit, ctx=ctx, engine_key=engine_key,
        trigger=dep_advisory.TRIGGER_WEEKLY,
    )
    raw_prs = _fetch_open_proposals(ctx=ctx)
    proposals = _to_proposals(raw_prs, GH_TIMEOUT_S, ctx=ctx)
    batches = group_proposals(proposals)
    proposals_by_num = {p.number: p for p in proposals}
    for batch in batches:
        _dispatch_batch(
            batch, proposals_by_num, queue, workspaces_root, audit,
            ctx=ctx, engine_key=engine_key,
        )
    dispatched = sum(1 for e in audit if e.get("action") == "dispatch")
    vuln_actions = sum(1 for e in audit if str(e.get("action", "")).startswith("vuln_"))
    return (
        f"dep_sweep complete: {dispatched} proposal(s) dispatched, "
        f"{vuln_actions} vuln audit result(s)"
    )


def _write_audit(
    workspace: Workspace, inv_id: str, audit: list[dict[str, Any]], *, trigger: str
) -> None:
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE,
        {
            "action": "sweep_complete",
            "task_id": workspace.task_id,
            "inv_id": inv_id,
            "trigger": trigger,
            "entry_count": len(audit),
            "entries": [{"trigger": trigger, **entry} for entry in audit],
        },
        log_label="dep_sweep",
    )


class DependencySweepTeam:
    name = _TEAM_NAME
    supports_resume = False

    def __init__(
        self,
        queue: Queue | None = None,
        workspaces_root: Path | None = None,
    ) -> None:
        self._queue = queue
        self._workspaces_root = workspaces_root

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [ROLE_SPEC]

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
        del instructions, task_id, on_proc_started
        reject_resume_inv_id(self.name, resume_inv_id)
        audit: list[dict[str, Any]] = []
        started = time.monotonic()
        summary = ""
        outcome = _OUTCOME_FAILURE
        advisory = dep_advisory.is_advisory_workspace(workspace)
        trigger = dep_advisory.TRIGGER_MAIN_CI_RED if advisory else dep_advisory.TRIGGER_WEEKLY
        try:
            with workflow_lifecycle(
                workspace, team_name=self.name, inv_id=inv_id, emit=emit
            ) as graph:
                node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
                workflow_mod.save_workflow(workspace, graph.doc)
                emit_node(emit, "node_started", inv_id, node)
                summary = self._sweep(workspace, audit)
                done = graph.complete_node(_NODE_ROLE)
                workflow_mod.save_workflow(workspace, graph.doc)
                emit_node(emit, "node_completed", inv_id, done)
            outcome = _OUTCOME_SUCCESS
        finally:
            duration_seconds = time.monotonic() - started
            _write_audit(workspace, inv_id, audit, trigger=trigger)
            if not advisory:
                self._schedule_next_sweep(
                    workspace, duration_seconds=duration_seconds, outcome=outcome
                )
        return build_terminal_handover(
            workspace, from_team=self.name, summary=summary, prior=prior, invocation_id=inv_id,
        )

    def _sweep(self, workspace: Workspace, audit: list[dict[str, Any]]) -> str:
        if self._queue is None:
            return "dependency_sweep: not wired to queue"
        cfg = governing_config_for_ws(workspace)
        forge = forge_target_for_ws(workspace)
        ctx = SweepContext(
            forge=forge,
            run_vuln_scan=cfg.dependency_sweep.enable_python_vuln_scan,
            posture=live_floored_posture(cfg.posture, workspace.meta.engine_key),
        )
        if dep_advisory.is_advisory_workspace(workspace):
            return _run_advisory_only(
                self._queue, self._workspaces_root, audit,
                ctx=ctx, engine_key=workspace.meta.engine_key,
            )
        return _run_sweep(
            self._queue, self._workspaces_root, audit,
            ctx=ctx, engine_key=workspace.meta.engine_key,
        )

    def _schedule_next_sweep(
        self,
        workspace: Workspace,
        *,
        duration_seconds: float | None = None,
        outcome: str = _OUTCOME_SUCCESS,
    ) -> None:
        try:
            self._do_schedule_next_sweep(
                workspace, duration_seconds=duration_seconds, outcome=outcome
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "dep_sweep: failed to schedule next sweep for %s", workspace.task_id
            )

    def _do_schedule_next_sweep(
        self,
        workspace: Workspace,
        *,
        duration_seconds: float | None = None,
        outcome: str = _OUTCOME_SUCCESS,
    ) -> None:
        if self._queue is None:
            return
        pending = self._queue.list_jobs(task_id=workspace.task_id, status=JobStatus.PENDING)
        if pending:
            return
        cfg = governing_config_for_ws(workspace)
        if live_hidden(workspace.meta.engine_key) or not cfg.dependency_sweep.enabled:
            return
        forge = forge_target_for_ws(workspace)
        now = time.time()
        next_at = now + cfg.dependency_sweep.cadence_seconds
        inv_id = workflow_mod.seed_pending_workflow(
            workspace, team=_TEAM_NAME, tasks=[ROLE_SPEC], source_task_id=workspace.task_id,
        )
        self._queue.enqueue(
            workspace.task_id, _TEAM_NAME,
            triggered_by_invocation_id=inv_id,
            eligible_at=next_at,
        )
        self._queue.set_meta(f"{_CADENCE_META_KEY}:{forge.repo.slug}", str(now))
        _save_dependency_sweep_status(
            forge,
            DependencySweepStatus(
                slug=forge.repo.slug,
                enabled=cfg.dependency_sweep.enabled,
                last_run_at=datetime.fromtimestamp(now, tz=UTC).isoformat(),
                next_run_at=datetime.fromtimestamp(next_at, tz=UTC).isoformat(),
                last_outcome=outcome,
                last_duration_seconds=duration_seconds,
            ),
        )


TEAM = DependencySweepTeam()

__all__ = [
    "TEAM",
    "DependencySweepStatus",
    "DependencySweepTeam",
    "ROLE_SPEC",
    "SweepContext",
    "load_dependency_sweep_status",
]

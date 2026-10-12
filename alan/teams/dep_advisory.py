from __future__ import annotations

import logging
import os
import re
import shlex
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from alan import dispatch_provenance as provenance_mod
from alan import pip_audit_gate, store
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dep_sweep import PackageFix, Vulnerability, plan_fixes
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import ForgeTarget, Posture, posture_rank
from alan.handover import Handover, save_handover
from alan.queue import Queue
from alan.teams._base import build_terminal_handover, make_handover_id
from alan.teams._dep_advisory_audit import AuditResult, audit_locked_dependencies
from alan.teams._dep_advisory_brief import (
    FIX_LOG_DOC,
    FORBIDDEN_ACTIONS,
    LOCK_FILE,
    PYPROJECT_FILE,
    build_fix_brief,
    build_no_fix_message,
    build_posture_message,
    fix_title,
    no_fix_title,
    safe_token,
    sanitize_plan,
)
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Workspace, as_utc

logger = logging.getLogger(__name__)

DEP_ADVISORY_TITLE = "Dependency Advisory Check"
TRIGGER_MAIN_CI_RED = "main_ci_red"
TRIGGER_WEEKLY = "weekly"
COVERED_WINDOW_S = 6 * 3600.0
DEFER_WINDOW_S = 24 * 3600.0

_TEAM_NAME = "dependency_sweep"
_FIX_TEAM = "implementation"
_SWEEP_NODE_ROLE = "sweep_runner"
_LEDGER_FILE = "dep_advisory_ledger.json"
_DRY_RUN_ENV = "ALAN_DEP_SWEEP_DRY_RUN"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_REQUIREMENTS_DIR = "/tmp"  # noqa: S108  # nosec B108
_UNSAFE_PATH_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")
_PENDING_RUN_KEY = "dep_advisory_pending_run"
_CHECKED_RUN_KEY = "dep_advisory_checked_run"
_AUDIT_FAILURE_ACTIONS = {"failed": "vuln_audit_failed", "no_lock": "vuln_audit_no_lock"}
_RETRY_ACTIONS = frozenset(
    {
        "vuln_fix_deferred",
        "vuln_workspace_failed",
        "vuln_ledger_write_failed",
        "vuln_workspace_dry_run",
    }
)
# Held across load -> decide -> create -> save so two concurrent checks cannot both open a
# workspace for one advisory; the audit subprocess runs before the lock is taken, never under it.
_LEDGER_LOCK = threading.Lock()

ADVISORY_SPEC = PendingTaskSpec(id=_SWEEP_NODE_ROLE, role=_SWEEP_NODE_ROLE, model="none")
_IMPL_SPEC = PendingTaskSpec(
    id="implementer",
    role="implementer",
    model=_ENGINE_DEFAULT.roles["implementer"].model,
    effort=_ENGINE_DEFAULT.roles["implementer"].effort,
)

LedgerKind = Literal["fix", "no_fix", "posture"]
_T = TypeVar("_T")


class LedgerEntry(BaseModel, frozen=True):
    advisory_id: str
    task_id: str
    kind: LedgerKind
    recorded_at: float


class Ledger(BaseModel):
    entries: dict[str, LedgerEntry] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Check:
    forge: ForgeTarget
    posture: Posture
    queue: Queue
    workspaces_root: Path | None
    engine_key: str | None
    trigger: str
    audit: list[dict[str, Any]]
    result: AuditResult
    now: datetime

    def record(self, action: str, **fields: object) -> None:
        self.audit.append({"action": action, "trigger": self.trigger, **fields})


@dataclass(frozen=True)
class _Request:
    kind: LedgerKind
    advisory_ids: tuple[str, ...]
    packages: tuple[str, ...]
    title: str
    description: str


@dataclass(frozen=True)
class _Opened:
    workspace: Workspace
    request: _Request


def pending_run_key(slug: str) -> str:
    return f"{_PENDING_RUN_KEY}:{slug}"


def checked_run_key(slug: str) -> str:
    return f"{_CHECKED_RUN_KEY}:{slug}"


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in _TRUTHY


def is_advisory_workspace(ws: Workspace) -> bool:
    return ws.meta.team == _TEAM_NAME and ws.meta.title == DEP_ADVISORY_TITLE


def _ledger_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _LEDGER_FILE


def load_ledger(forge: ForgeTarget) -> Ledger:
    try:
        raw = _ledger_path(forge).read_text(encoding="utf-8")
    except OSError:
        return Ledger()
    try:
        return Ledger.model_validate_json(raw)
    except ValidationError:
        logger.warning("dep_advisory: ledger for %s is corrupt; starting empty", forge.repo.slug)
        return Ledger()


def save_ledger(forge: ForgeTarget, ledger: Ledger) -> None:
    path = _ledger_path(forge)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(path, ledger.model_dump_json(), prefix=".dep_advisory_ledger_")


def _workspace_or_none(task_id: str, workspaces_root: Path | None) -> Workspace | None:
    try:
        return workspace_mod.read(task_id, root=workspaces_root)
    except workspace_mod.WorkspaceError:
        return None


def _terminal_age_s(ws: Workspace, now: datetime) -> float:
    stamp = ws.meta.terminal_at or ws.meta.updated_at
    return (now - as_utc(stamp)).total_seconds()


def _workspace_covers(ws: Workspace, now: datetime) -> bool:
    if ws.meta.status == Status.ARCHIVED:
        return False
    if ws.meta.status == Status.COMPLETED:
        return _terminal_age_s(ws, now) < COVERED_WINDOW_S
    return True


def _still_unfixable(advisory_id: str, current_vulns: Sequence[Vulnerability]) -> bool:
    matching = [vuln for vuln in current_vulns if vuln.id == advisory_id]
    return not plan_fixes(matching, ()).fixes


def is_covered(
    entry: LedgerEntry,
    workspaces_root: Path | None,
    now: datetime,
    current_vulns: Sequence[Vulnerability],
) -> bool:
    ws = _workspace_or_none(entry.task_id, workspaces_root)
    if ws is None or not _workspace_covers(ws, now):
        return False
    if entry.kind != "no_fix":
        return True
    return _still_unfixable(entry.advisory_id, current_vulns)


def _is_active_fix(entry: LedgerEntry, workspaces_root: Path | None, now: datetime) -> bool:
    if entry.kind != "fix" or now.timestamp() - entry.recorded_at >= DEFER_WINDOW_S:
        return False
    ws = _workspace_or_none(entry.task_id, workspaces_root)
    return ws is not None and ws.meta.status == Status.ACTIVE


def _active_fix_exists(ledger: Ledger, workspaces_root: Path | None, now: datetime) -> bool:
    return any(_is_active_fix(e, workspaces_root, now) for e in ledger.entries.values())


def fix_in_flight(
    forge: ForgeTarget, workspaces_root: Path | None, *, now: datetime | None = None
) -> bool:
    moment = now if now is not None else datetime.now(UTC)
    return _active_fix_exists(load_ledger(forge), workspaces_root, moment)


def requirements_path(forge: ForgeTarget) -> str:
    slug = _UNSAFE_PATH_CHARS_RE.sub("_", forge.repo.slug)
    return f"{_REQUIREMENTS_DIR}/alan-advisory-{slug}.txt"


def verify_command(suppressed: Sequence[str], requirements: str) -> str:
    safe = [vuln_id for vuln_id in suppressed if safe_token(vuln_id) is not None]
    export = [*pip_audit_gate.EXPORT_ARGV, "--output-file", requirements]
    audit_tail = pip_audit_gate.audit_argv(requirements, safe, json_output=False)[3:]
    audit = ["uv", "run", "--no-active", "pip-audit", *audit_tail]
    return f"{shlex.join(export)}\n{shlex.join(audit)}"


def _bootstrap_handover(ws: Workspace, brief: str, summary: str) -> Handover:
    new_id = make_handover_id(ws.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=new_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id="dep_advisory_bootstrap",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        plan_synthesis=brief,
        forbidden_actions=list(FORBIDDEN_ACTIONS),
        files_to_edit=[LOCK_FILE, PYPROJECT_FILE, FIX_LOG_DOC],
        dependency_bot_authorized=True,
    )


def _dispatch_fix(ws: Workspace, brief: str, summary: str, queue: Queue) -> None:
    bootstrap = _bootstrap_handover(ws, brief, summary)
    save_handover(ws, bootstrap)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team=_FIX_TEAM, tasks=[_IMPL_SPEC], source_task_id=ws.task_id
    )
    queue.enqueue(
        ws.task_id,
        _FIX_TEAM,
        parent_handover_id=bootstrap.handover_id,
        triggered_by_invocation_id=inv_id,
    )


def _escalate(ws: Workspace, summary: str) -> None:
    handover = build_terminal_handover(
        ws,
        from_team=_TEAM_NAME,
        summary=summary,
        prior=None,
        invocation_id="dep_advisory_escalation",
    )
    save_handover(ws, handover)
    workspace_mod.transition(ws, Status.ESCALATED)


def discard_workspace(queue: Queue, workspaces_root: Path | None, ws: Workspace) -> None:
    try:
        queue.delete_jobs(ws.task_id)
        workspace_mod.delete(ws.task_id, root=workspaces_root)
    except Exception:  # noqa: BLE001 - rollback is best-effort
        logger.exception("dep_advisory: rollback of %s failed", ws.task_id)


def _discard(check: _Check, ws: Workspace) -> None:
    discard_workspace(check.queue, check.workspaces_root, ws)


def _fail(check: _Check, request: _Request, ws: Workspace | None) -> None:
    logger.exception(
        "dep_advisory: could not open the %s workspace for %s", request.kind, request.advisory_ids
    )
    if ws is not None:
        _discard(check, ws)
    check.record(
        "vuln_workspace_failed", kind=request.kind, advisory_ids=list(request.advisory_ids)
    )


def _attempt(
    check: _Check, request: _Request, finish: Callable[[Workspace], None]
) -> _Opened | None:
    if dry_run_enabled():
        check.record(
            "vuln_workspace_dry_run",
            kind=request.kind,
            advisory_ids=list(request.advisory_ids),
            title=request.title,
        )
        return None
    try:
        ws = workspace_mod.create(
            request.title,
            request.description,
            team=_TEAM_NAME,
            root=check.workspaces_root,
            engine_key=check.engine_key,
            provenance=provenance_mod.DispatchProvenance(
                surface=provenance_mod.DispatchSurface.DEP_ADVISORY_FIX
            ),
        )
    except Exception:  # noqa: BLE001 - one failed creation must not abort the check
        _fail(check, request, None)
        return None
    try:
        finish(ws)
    except Exception:  # noqa: BLE001 - roll the half-built workspace back
        _fail(check, request, ws)
        return None
    return _Opened(ws, request)


def _fix_request(fixes: Sequence[PackageFix], kind: LedgerKind) -> _Request:
    ids = tuple(sorted({i for fix in fixes for i in fix.advisory_ids}))
    packages = tuple(fix.package for fix in fixes)
    targets = ", ".join(f"{fix.package} to {fix.target_version}" for fix in fixes)
    return _Request(
        kind=kind,
        advisory_ids=ids,
        packages=packages,
        title=fix_title(fixes),
        description=f"Raise {targets} to clear {', '.join(ids)}.",
    )


def _open_fix_workspace(check: _Check, fixes: Sequence[PackageFix]) -> _Opened | None:
    request = _fix_request(fixes, "fix")
    audit_command = verify_command(check.result.suppressed, requirements_path(check.forge))
    brief = build_fix_brief(fixes, check.result.pyproject_text, audit_command=audit_command)
    return _attempt(
        check,
        request,
        lambda ws: _dispatch_fix(ws, brief, request.description, check.queue),
    )


def _open_posture_workspace(check: _Check, fixes: Sequence[PackageFix]) -> _Opened | None:
    request = _fix_request(fixes, "posture")
    audit_command = verify_command(check.result.suppressed, requirements_path(check.forge))
    brief = build_fix_brief(fixes, check.result.pyproject_text, audit_command=audit_command)
    message = build_posture_message(fixes, brief)
    return _attempt(check, request, lambda ws: _escalate(ws, message))


def _open_no_fix_workspace(check: _Check, vulns: Sequence[Vulnerability]) -> _Opened | None:
    ids = tuple(sorted({vuln.id for vuln in vulns}))
    request = _Request(
        kind="no_fix",
        advisory_ids=ids,
        packages=tuple(vuln.package for vuln in vulns),
        title=no_fix_title(vulns),
        description=f"No fixed release exists for {', '.join(ids)}.",
    )
    message = build_no_fix_message(vulns)
    return _attempt(check, request, lambda ws: _escalate(ws, message))


def _advisory_covered(check: _Check, ledger: Ledger, advisory_id: str) -> bool:
    entry = ledger.entries.get(advisory_id)
    return entry is not None and is_covered(
        entry, check.workspaces_root, check.now, check.result.vulns
    )


def _uncovered(
    check: _Check,
    ledger: Ledger,
    items: Sequence[_T],
    ids_of: Callable[[_T], tuple[str, ...]],
) -> list[_T]:
    fresh: list[_T] = []
    for item in items:
        ids = ids_of(item)
        if all(_advisory_covered(check, ledger, advisory_id) for advisory_id in ids):
            check.record("vuln_already_covered", advisory_ids=list(ids))
        else:
            fresh.append(item)
    return fresh


def _below_propose(posture: Posture) -> bool:
    return posture_rank(posture) < posture_rank(Posture.propose)


def _open_fixes(check: _Check, ledger: Ledger, fixes: Sequence[PackageFix]) -> list[_Opened]:
    if not fixes:
        return []
    if _below_propose(check.posture):
        opened = _open_posture_workspace(check, fixes)
    elif _active_fix_exists(ledger, check.workspaces_root, check.now):
        ids = sorted({i for fix in fixes for i in fix.advisory_ids})
        check.record("vuln_fix_deferred", advisory_ids=ids)
        return []
    else:
        opened = _open_fix_workspace(check, fixes)
    return [] if opened is None else [opened]


def _open_no_fix(check: _Check, vulns: Sequence[Vulnerability]) -> list[_Opened]:
    if not vulns:
        return []
    opened = _open_no_fix_workspace(check, vulns)
    return [] if opened is None else [opened]


def _record_opened(check: _Check, opened: _Opened) -> None:
    request = opened.request
    check.record(
        f"vuln_{request.kind}_workspace_opened",
        task_id=opened.workspace.task_id,
        title=request.title,
        advisory_ids=list(request.advisory_ids),
        packages=list(request.packages),
    )


def _commit_ledger(check: _Check, ledger: Ledger, opened: list[_Opened]) -> None:
    if not opened:
        return
    stamp = check.now.timestamp()
    for item in opened:
        for advisory_id in item.request.advisory_ids:
            ledger.entries[advisory_id] = LedgerEntry(
                advisory_id=advisory_id,
                task_id=item.workspace.task_id,
                kind=item.request.kind,
                recorded_at=stamp,
            )
    try:
        save_ledger(check.forge, ledger)
    except OSError:
        logger.exception("dep_advisory: ledger write failed for %s", check.forge.repo.slug)
        for item in opened:
            _discard(check, item.workspace)
        check.record("vuln_ledger_write_failed", workspaces=len(opened))
        return
    for item in opened:
        _record_opened(check, item)


def _act_locked(check: _Check, fixes: Sequence[PackageFix], stuck: Sequence[Vulnerability]) -> None:
    ledger = load_ledger(check.forge)
    fresh_fixes = _uncovered(check, ledger, fixes, lambda fix: fix.advisory_ids)
    fresh_stuck = _uncovered(check, ledger, stuck, lambda vuln: (vuln.id,))
    opened = [*_open_fixes(check, ledger, fresh_fixes), *_open_no_fix(check, fresh_stuck)]
    _commit_ledger(check, ledger, opened)


def _plan_size(fixes: Sequence[PackageFix], stuck: Sequence[Vulnerability]) -> int:
    return sum(len(fix.advisory_ids) for fix in fixes) + len(stuck)


def _handle_findings(check: _Check) -> None:
    raw = plan_fixes(check.result.vulns, check.result.suppressed)
    plan = sanitize_plan(raw)
    raw_size = _plan_size(raw.fixes, raw.unfixable)
    size = _plan_size(plan.fixes, plan.unfixable)
    if raw_size == 0:
        check.record("vuln_none_found", suppressed=list(check.result.suppressed))
        return
    if raw_size != size:
        check.record("vuln_dropped_invalid", count=raw_size - size)
    if size == 0:
        return
    with _LEDGER_LOCK:
        _act_locked(check, plan.fixes, plan.unfixable)


def _retry_pending(check: _Check) -> bool:
    return any(entry.get("action") in _RETRY_ACTIONS for entry in check.audit)


def _record_checked_run(check: _Check) -> None:
    if check.trigger != TRIGGER_MAIN_CI_RED or _retry_pending(check):
        return
    slug = check.forge.repo.slug
    pending = check.queue.get_meta(pending_run_key(slug))
    if pending is not None:
        check.queue.set_meta(checked_run_key(slug), pending)


def _summary(audit: Sequence[dict[str, Any]]) -> str:
    actions = [str(entry.get("action", "")) for entry in audit]
    opened = sum(1 for action in actions if action.endswith("_workspace_opened"))
    covered = actions.count("vuln_already_covered")
    return f"advisory check complete: {opened} workspace(s) opened, {covered} already covered"


def run_advisory_check(
    *,
    forge: ForgeTarget,
    posture: Posture,
    queue: Queue,
    workspaces_root: Path | None,
    engine_key: str | None,
    trigger: str,
    audit: list[dict[str, Any]],
) -> str:
    result = audit_locked_dependencies(forge)
    check = _Check(
        forge, posture, queue, workspaces_root, engine_key, trigger, audit, result,
        datetime.now(UTC),
    )
    if result.outcome == "ok":
        _handle_findings(check)
    else:
        check.record(_AUDIT_FAILURE_ACTIONS[result.outcome], detail=result.detail)
    if result.outcome != "failed":
        _record_checked_run(check)
    return _summary(audit)

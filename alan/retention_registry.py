from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from alan import handover as handover_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.engine_config import canonical_scope_key
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams.dep_advisory import DEP_ADVISORY_TITLE
from alan.teams.triage_pregate import ClassifiedSurface
from alan.workspace import Workspace

logger = logging.getLogger("alan.retention_registry")

_INVOCATION_SLOT_CAP = 999
_NEAR_CAP_RATIO = 0.9
_NEAR_CAP_KIND = "retention_near_cap"
_INV_DIR_RE = re.compile(r"^inv_\d{3}$")

_SLOTS_PRUNED_ACTION = "invocation_slots_pruned"
_HANDOVERS_PRUNED_ACTION = "handover_slots_pruned"
_REVISIT_KEYS_PRUNED_ACTION = "revisit_keys_pruned"


class RetentionRegistryError(Exception):
    pass


@dataclass(frozen=True)
class RetentionEntry:
    team: str
    title: str
    description: str
    audit_file: str


REGISTRY: tuple[RetentionEntry, ...] = (
    RetentionEntry(
        team="triage",
        title="Failure Triage Sweep",
        description="Recurring read-only sweep that surfaces failure signals.",
        audit_file="triage_audit.jsonl",
    ),
    RetentionEntry(
        team="dependency_sweep",
        title="Dependency Sweep",
        description=(
            "Autonomous recurring sweep that drives Dependabot PRs and audits vulnerabilities."
        ),
        audit_file="dep_sweep_audit.jsonl",
    ),
    RetentionEntry(
        team="dependency_sweep",
        title=DEP_ADVISORY_TITLE,
        description="Event-triggered Python advisory check on red main CI.",
        audit_file="dep_sweep_audit.jsonl",
    ),
)

_BY_KEY: dict[tuple[str, str], RetentionEntry] = {(e.team, e.title): e for e in REGISTRY}


def lookup(team: str | None, title: str) -> RetentionEntry | None:
    if team is None:
        return None
    return _BY_KEY.get((team, title))


_DEP_SWEEP_TEAM = "dependency_sweep"


def _audit_pruned(entry: RetentionEntry, ws: Workspace, action: str, removed: int) -> None:
    if not removed:
        return
    payload: dict[str, object] = {"action": action, "task_id": ws.task_id, "removed": removed}
    if entry.team == _DEP_SWEEP_TEAM:
        payload["slug"] = canonical_scope_key(ws.meta.engine_key).slug
    append_jsonl_audit(
        logs_root() / entry.audit_file,
        payload,
        log_label="retention_registry",
    )


def _prune_registered_workspace(entry: RetentionEntry, ws: Workspace) -> None:
    _audit_pruned(entry, ws, _SLOTS_PRUNED_ACTION, workflow_mod.prune_invocation_dirs(ws))
    _audit_pruned(entry, ws, _HANDOVERS_PRUNED_ACTION, handover_mod.prune_handover_files(ws))
    _audit_pruned(
        entry,
        ws,
        _REVISIT_KEYS_PRUNED_ACTION,
        workspace_mod.prune_revisit_counts_for_team(ws, entry.team),
    )


def seed_recurring_workspace(ws: Workspace, *, hard_fail: bool = True) -> None:
    entry = lookup(ws.meta.team, ws.meta.title)
    if entry is None:
        if hard_fail:
            raise RetentionRegistryError(
                f"no retention registry entry for team={ws.meta.team!r} "
                f"title={ws.meta.title!r}"
            )
        return
    try:
        _prune_registered_workspace(entry, ws)
    except Exception:  # noqa: BLE001 - a prune failure must never block the seed
        logger.exception("retention_registry: prune failed for %s", ws.task_id)


def _near_cap_surface(entry: RetentionEntry, ws: Workspace, used: int) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"{_NEAR_CAP_KIND}:{ws.task_id}",
        kind=_NEAR_CAP_KIND,
        is_blocked=True,
        blocker_reason=_NEAR_CAP_KIND,
        obs={
            "task_id": ws.task_id,
            "team": entry.team,
            "title": entry.title,
            "used": used,
            "cap": _INVOCATION_SLOT_CAP,
        },
    )


def _used_invocation_slots(ws: Workspace) -> int:
    # `create_invocation_dir` claims a slot by creating `inv_NNN/` before the
    # invocation's workflow.yaml is ever written, so slot exhaustion tracks
    # directory count — `list_invocations()`'s workflow.yaml-having subset
    # would undercount a workspace interrupted between the two writes.
    base = workflow_mod.invocations_dir(ws.path)
    if not base.is_dir():
        return 0
    return sum(1 for entry in base.iterdir() if entry.is_dir() and _INV_DIR_RE.match(entry.name))


def _registry_capacity_findings(  # pyright: ignore[reportUnusedFunction]
    workspaces_root: Path | None,
) -> list[ClassifiedSurface]:
    threshold = _INVOCATION_SLOT_CAP * _NEAR_CAP_RATIO
    findings: list[ClassifiedSurface] = []
    for ws in workspace_mod.list_workspaces(root=workspaces_root):
        entry = lookup(ws.meta.team, ws.meta.title)
        if entry is None:
            continue
        used = _used_invocation_slots(ws)
        if used >= threshold:
            findings.append(_near_cap_surface(entry, ws, used))
    return findings


__all__ = [
    "REGISTRY",
    "RetentionEntry",
    "RetentionRegistryError",
    "lookup",
    "seed_recurring_workspace",
]

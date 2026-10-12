from __future__ import annotations

from typing import Literal

from alan.teams import _pr_audit_log
from alan.workspace import StickyInstruction, Workspace

NON_CONSUMING_TEAMS: frozenset[str] = frozenset(
    {"pr_merge", "triage", "dependency_sweep", "deploy_verify"}
)

InstructionOrigin = Literal["dispatch", "inherited", "generated"]

MAX_COMPOSED_CHARS = 40000

_TRUNCATION_MARKER = "\n\n(truncated — full text in the workspace UI)"


def resolve(ws: Workspace, task_id: str) -> StickyInstruction | None:
    return ws.meta.dispatch_instructions.get(task_id)


def compose(sticky: str, generated: str | None) -> str:
    if not generated:
        return sticky
    if len(generated) >= MAX_COMPOSED_CHARS:
        _pr_audit_log.append_audit({
            "action": "sticky_instructions_dropped",
            "sticky_len": len(sticky),
            "generated_len": len(generated),
        })
        return generated[:MAX_COMPOSED_CHARS]
    combined = f"{sticky}\n\n{generated}"
    if len(combined) <= MAX_COMPOSED_CHARS:
        return combined
    budget = MAX_COMPOSED_CHARS - len(generated) - len(_TRUNCATION_MARKER) - 2
    if budget <= 0:
        _pr_audit_log.append_audit({
            "action": "sticky_instructions_dropped",
            "sticky_len": len(sticky),
            "generated_len": len(generated),
        })
        return generated
    truncated_sticky = sticky[:budget] + _TRUNCATION_MARKER
    _pr_audit_log.append_audit({
        "action": "sticky_instructions_truncated",
        "sticky_len": len(sticky),
        "generated_len": len(generated),
    })
    return f"{truncated_sticky}\n\n{generated}"


def should_persist(team: str) -> bool:
    return team not in NON_CONSUMING_TEAMS


def for_enqueue(
    ws: Workspace, task_id: str, team: str, generated: str | None
) -> tuple[str | None, InstructionOrigin | None]:
    if team in NON_CONSUMING_TEAMS:
        return generated, None
    sticky = resolve(ws, task_id)
    if sticky is None:
        if generated:
            return generated, "generated"
        return None, None
    return compose(sticky.text, generated), "inherited"


__all__ = [
    "MAX_COMPOSED_CHARS",
    "NON_CONSUMING_TEAMS",
    "InstructionOrigin",
    "compose",
    "for_enqueue",
    "resolve",
    "should_persist",
]

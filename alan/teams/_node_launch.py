from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from alan import workflow as workflow_mod
from alan.launch_spec import TURN_BUDGET_PREFIX, LaunchSpec
from alan.node_launch_record import record_from_spec
from alan.workspace import Workspace

if TYPE_CHECKING:
    from alan.teams._base import WorkerOverrides


@dataclass(frozen=True)
class TurnBudget:
    applied: int
    derived: int
    source: str


@dataclass(frozen=True)
class PreparedNodeContext:
    opts: WorkerOverrides
    effective_kwargs: dict[str, Any]
    effective_max_turns: int
    augmented_prompt: str


def persist_node_config(
    workspace: Workspace,
    inv_id: str,
    node_id: str,
    *,
    system_prompt: str,
    user_text: str,
    allowed_tools: list[str],
    max_turns: int,
    permission_mode: str,
    model: str,
    turns_source: str,
    derived_max_turns: int,
) -> None:
    workflow_mod.save_node_config(
        workspace,
        inv_id,
        node_id,
        system_prompt=system_prompt,
        user_text=user_text,
        allowed_tools=allowed_tools,
        max_turns=max_turns,
        permission_mode=permission_mode,
        model=model,
        turns_source=turns_source,
        derived_max_turns=derived_max_turns,
    )


def prepare_node_context(
    workspace: Workspace,
    inv_id: str,
    node_id: str,
    *,
    opts: WorkerOverrides,
    effective_kwargs: dict[str, Any],
    budget: TurnBudget,
    system_prompt: str,
    user_text: str,
    model: str,
) -> PreparedNodeContext:
    augmented_prompt = f"{system_prompt}{TURN_BUDGET_PREFIX}{budget.applied}"
    persist_node_config(
        workspace,
        inv_id,
        node_id,
        system_prompt=augmented_prompt,
        user_text=user_text,
        allowed_tools=list(effective_kwargs["allowed_tools"]),
        max_turns=budget.applied,
        permission_mode=effective_kwargs.get("permission_mode", "auto"),
        model=model,
        turns_source=budget.source,
        derived_max_turns=budget.derived,
    )
    return PreparedNodeContext(opts, effective_kwargs, budget.applied, augmented_prompt)


def launch_recorder(
    workspace: Workspace, inv_id: str, node_id: str
) -> Callable[[LaunchSpec], None]:
    def _record(spec: LaunchSpec) -> None:
        workflow_mod.save_node_launch(workspace, inv_id, node_id, record_from_spec(spec))

    return _record

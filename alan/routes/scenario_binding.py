from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

import alan.workspace as workspace_mod
from alan import scenario_binding
from alan.handover import Handover
from alan.roots import logs_root
from alan.routes import _deps
from alan.routes._deps import WorkspacesRootDep, latest_handover, read_workspace_or_404
from alan.scenario_binding import ScenarioMode
from alan.teams import _pr_forge
from alan.teams._audit import append_jsonl_audit
from alan.teams._scenario_runner import (
    _is_prose_rejection,  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import ScenarioBinding, TestScenario

scenario_binding_router = APIRouter(prefix="/api/workspaces")

_WORKSPACE_NOT_FOUND_DESC = "workspace not found"
_ARCHIVED_DETAIL = "workspace is archived"
_TEST_REF_BOUND_DETAIL = (
    "scenario_id is referenced by a live Requirement.test_ref and cannot be waived"
)

_VIEW_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
}
_BINDING_RESPONSES: dict[int | str, dict[str, str]] = {
    **_deps.GUARDED,
    404: {
        "description": f"{_WORKSPACE_NOT_FOUND_DESC}, or "
        f"{scenario_binding.UNKNOWN_SCENARIO_DETAIL}"
    },
    409: {"description": f"{_ARCHIVED_DETAIL}, or {_TEST_REF_BOUND_DETAIL}"},
    422: {"description": scenario_binding.PROSE_WHEN_DETAIL},
}
_DELETE_RESPONSES: dict[int | str, dict[str, str]] = {
    **_deps.GUARDED,
    404: {
        "description": f"{_WORKSPACE_NOT_FOUND_DESC}, or "
        f"{scenario_binding.UNKNOWN_SCENARIO_DETAIL}"
    },
    409: {"description": _ARCHIVED_DETAIL},
}


class ScenarioViewResponse(BaseModel):
    scenarios: list[TestScenario]
    waived_scenario_ids: list[str]
    mode: ScenarioMode


class ScenarioBindingBody(BaseModel):
    when: str | None = Field(default=None, min_length=1)
    test_ref: str | None = Field(default=None, min_length=1)
    waived: bool | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _validate_at_least_one_set(self) -> "ScenarioBindingBody":
        if self.when is None and self.test_ref is None and self.waived is None:
            raise ValueError("when, test_ref, or waived is required")
        return self


def _logs_dir() -> Path:
    return logs_root()


def _scenario_view(
    ws: workspace_mod.Workspace, prior: Handover | None
) -> ScenarioViewResponse:
    base = scenario_binding.all_base_scenarios(ws, prior)
    return ScenarioViewResponse(
        scenarios=scenario_binding.bound(ws.meta, base),
        waived_scenario_ids=sorted(scenario_binding.waived_ids(ws.meta, base)),
        mode=scenario_binding.all_scenario_mode(ws, prior),
    )


def _test_ref_bound(ws: workspace_mod.Workspace, scenario_id: str) -> bool:
    return any(r.test_ref == scenario_id for r in (ws.meta.requirements or []))


def _append_binding_audit(
    workspace_id: str,
    scenario_id: str,
    *,
    action: str,
    when: str | None = None,
    test_ref: str | None = None,
    waived: bool | None = None,
    reason: str | None = None,
) -> None:
    append_jsonl_audit(
        _logs_dir() / scenario_binding.AUDIT_FILE,
        {
            "kind": "binding",
            "action": action,
            "workspace_id": workspace_id,
            "scenario_id": scenario_id,
            "when": when,
            "test_ref": test_ref,
            "waived": waived,
            "reason": reason,
        },
        log_label="scenario_binding_override",
    )


async def _load_bindable(
    workspace_id: str,
    scenario_id: str,
    workspaces_root: Path,
    *,
    allow_orphaned: bool = False,
) -> tuple[workspace_mod.Workspace, Handover | None]:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    if ws.meta.status == workspace_mod.Status.ARCHIVED:
        raise HTTPException(status_code=409, detail=_ARCHIVED_DETAIL)
    prior = await run_in_threadpool(latest_handover, ws)
    known_ids = {s.id for s in scenario_binding.all_base_scenarios(ws, prior)}
    orphaned_but_bound = allow_orphaned and scenario_id in ws.meta.scenario_bindings
    if scenario_id not in known_ids and not orphaned_but_bound:
        raise HTTPException(status_code=404, detail=scenario_binding.UNKNOWN_SCENARIO_DETAIL)
    return ws, prior


@scenario_binding_router.get("/{workspace_id}/scenarios", responses=_VIEW_RESPONSES)
async def get_workspace_scenarios(
    workspace_id: str, workspaces_root: WorkspacesRootDep
) -> ScenarioViewResponse:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    prior = await run_in_threadpool(latest_handover, ws)
    return _scenario_view(ws, prior)


@scenario_binding_router.put(
    "/{workspace_id}/scenarios/{scenario_id}/binding",
    responses=_BINDING_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def put_scenario_binding(
    workspace_id: str,
    scenario_id: str,
    body: ScenarioBindingBody,
    workspaces_root: WorkspacesRootDep,
) -> ScenarioViewResponse:
    ws, _prior = await _load_bindable(workspace_id, scenario_id, workspaces_root)
    if body.when is not None and _is_prose_rejection(
        body.when, _pr_forge.governing_config_for_ws(ws)
    ):
        raise HTTPException(status_code=422, detail=scenario_binding.PROSE_WHEN_DETAIL)
    if body.waived and _test_ref_bound(ws, scenario_id):
        raise HTTPException(status_code=409, detail=_TEST_REF_BOUND_DETAIL)
    await run_in_threadpool(
        _append_binding_audit,
        workspace_id,
        scenario_id,
        action="put",
        when=body.when,
        test_ref=body.test_ref,
        waived=body.waived,
        reason=body.reason,
    )
    binding = ScenarioBinding(
        when=body.when, test_ref=body.test_ref, waived=bool(body.waived), reason=body.reason
    )
    ws = await run_in_threadpool(workspace_mod.update_scenario_binding, ws, scenario_id, binding)
    ws = await run_in_threadpool(
        workspace_mod.record_intervention, ws, workspace_mod.InterventionAction.SCENARIO_OVERRIDE
    )
    prior = await run_in_threadpool(latest_handover, ws)
    return _scenario_view(ws, prior)


@scenario_binding_router.delete(
    "/{workspace_id}/scenarios/{scenario_id}/binding",
    responses=_DELETE_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def delete_scenario_binding(
    workspace_id: str,
    scenario_id: str,
    workspaces_root: WorkspacesRootDep,
) -> ScenarioViewResponse:
    ws, _prior = await _load_bindable(
        workspace_id, scenario_id, workspaces_root, allow_orphaned=True
    )
    await run_in_threadpool(_append_binding_audit, workspace_id, scenario_id, action="delete")
    ws = await run_in_threadpool(workspace_mod.update_scenario_binding, ws, scenario_id, None)
    ws = await run_in_threadpool(
        workspace_mod.record_intervention, ws, workspace_mod.InterventionAction.SCENARIO_OVERRIDE
    )
    prior = await run_in_threadpool(latest_handover, ws)
    return _scenario_view(ws, prior)

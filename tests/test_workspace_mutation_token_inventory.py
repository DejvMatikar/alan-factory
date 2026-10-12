from __future__ import annotations

import pytest
from fastapi import APIRouter

from alan import server
from alan.routes.dispatch_instructions import dispatch_instructions_router
from alan.routes.workspaces import tasks_router, workspaces_router
from tests.control_token_inventory import declares_control_token

ROUTERS: tuple[APIRouter, ...] = (workspaces_router, tasks_router, dispatch_instructions_router)

SCENARIO_OVERRIDE = ("PUT", "/api/workspaces/{workspace_id}/tasks/{task_id}/scenarios")
STICKY_INSTRUCTION_WRITE = ("PUT", "/api/workspaces/{task_id}/sticky-instructions")

UNGUARDED_WORKSPACE_MUTATIONS = [
    ("POST", "/api/workspaces/{task_id}/stop"),
    ("POST", "/api/workspaces/{task_id}/resume"),
    ("POST", "/api/workspaces/{task_id}/archive"),
    ("POST", "/api/workspaces/{task_id}/unarchive"),
    ("DELETE", "/api/workspaces/{task_id}"),
    ("DELETE", "/api/workspaces/{task_id}/sticky-instructions"),
]


def test_scenario_override_route_requires_control_token() -> None:
    assert declares_control_token(ROUTERS, *SCENARIO_OVERRIDE)


def test_sticky_instruction_write_route_requires_control_token() -> None:
    assert declares_control_token(ROUTERS, *STICKY_INSTRUCTION_WRITE)


@pytest.mark.parametrize(
    ("method", "path"),
    UNGUARDED_WORKSPACE_MUTATIONS,
    ids=[f"{method} {path}" for method, path in UNGUARDED_WORKSPACE_MUTATIONS],
)
def test_workspace_mutation_route_does_not_declare_control_token(method: str, path: str) -> None:
    assert not declares_control_token(ROUTERS, method, path)


def test_application_declares_no_blanket_dependencies() -> None:
    assert server.app.router.dependencies == []

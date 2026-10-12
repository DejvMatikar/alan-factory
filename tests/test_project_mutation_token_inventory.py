from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from alan import server  # noqa: F401
from alan.routes._deps import require_control_token
from alan.routes.projects import projects_router
from alan.routes.workspaces import tasks_router, workspaces_router
from tests.control_token_inventory import declares_control_token

ROUTERS: tuple[APIRouter, ...] = (projects_router,)

WORKSPACE_SCENARIO_OVERRIDE = ("PUT", "/api/workspaces/{workspace_id}/tasks/{task_id}/scenarios")

PROJECT_MUTATIONS = [
    ("POST", "/api/projects"),
    ("PATCH", "/api/projects/{project_id}"),
    ("POST", "/api/projects/{project_id}/complete"),
    ("DELETE", "/api/projects/{project_id}"),
    ("POST", "/api/projects/{project_id}/amendments"),
    ("POST", "/api/projects/{project_id}/amendments/{fingerprint}/approve"),
    ("POST", "/api/projects/{project_id}/roadmap/preview"),
    ("POST", "/api/projects/{project_id}/roadmap/import"),
    ("POST", "/api/projects/{project_id}/roadmap/reorder"),
    ("POST", "/api/projects/{project_id}/roadmap/advance"),
    ("POST", "/api/projects/{project_id}/roadmap/items/{item_id}"),
]


def _non_get_routes(router: APIRouter) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
        if method != "GET"
    }


@pytest.mark.parametrize(
    ("method", "path"),
    PROJECT_MUTATIONS,
    ids=[f"{method} {path}" for method, path in PROJECT_MUTATIONS],
)
def test_project_mutation_route_does_not_declare_control_token(method: str, path: str) -> None:
    assert not declares_control_token(ROUTERS, method, path)


def test_walker_detects_a_declared_control_token_on_a_synthetic_route() -> None:
    guarded = APIRouter()

    @guarded.post("/guarded", dependencies=[Depends(require_control_token)])
    async def guarded_route() -> None:
        return None

    assert declares_control_token((guarded,), "POST", "/guarded")


def test_walker_detects_a_declared_control_token_on_the_workspace_scenario_override() -> None:
    assert declares_control_token((workspaces_router, tasks_router), *WORKSPACE_SCENARIO_OVERRIDE)


def test_project_read_route_does_not_declare_control_token() -> None:
    assert not declares_control_token(ROUTERS, "GET", "/api/projects/{project_id}")


def test_every_non_get_project_route_is_classified() -> None:
    unclassified = _non_get_routes(projects_router) - set(PROJECT_MUTATIONS)
    stale = set(PROJECT_MUTATIONS) - _non_get_routes(projects_router)
    assert not unclassified, f"classify this route in PROJECT_MUTATIONS: {sorted(unclassified)}"
    assert not stale, f"PROJECT_MUTATIONS lists a route that no longer exists: {sorted(stale)}"

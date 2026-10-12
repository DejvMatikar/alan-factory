from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from alan import server  # noqa: F401
from alan.routes._deps import require_control_token
from alan.routes.build_chat import build_chat_router
from alan.routes.explore_chat import explore_chat_router
from alan.routes.workspaces import tasks_router, workspaces_router
from tests.control_token_inventory import declares_control_token

ROUTERS: tuple[APIRouter, ...] = (build_chat_router, explore_chat_router)

WORKSPACE_SCENARIO_OVERRIDE = ("PUT", "/api/workspaces/{workspace_id}/tasks/{task_id}/scenarios")

_CHAT_SESSION = "/sessions/{session_id}"

CHAT_MUTATIONS = [
    (method, f"/api/{family}-chat{suffix}")
    for family in ("build", "explore")
    for method, suffix in (
        ("POST", "/sessions"),
        ("DELETE", _CHAT_SESSION),
        ("POST", f"{_CHAT_SESSION}/messages"),
        ("POST", f"{_CHAT_SESSION}/interrupt"),
        ("POST", f"{_CHAT_SESSION}/summarize"),
        ("POST", f"{_CHAT_SESSION}/propose"),
        ("PUT", f"{_CHAT_SESSION}/brief"),
    )
]


def _non_get_routes(*routers: APIRouter) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
        if method != "GET"
    }


@pytest.mark.parametrize(
    ("method", "path"),
    CHAT_MUTATIONS,
    ids=[f"{method} {path}" for method, path in CHAT_MUTATIONS],
)
def test_chat_mutation_route_does_not_declare_control_token(method: str, path: str) -> None:
    assert not declares_control_token(ROUTERS, method, path)


def test_walker_detects_a_declared_control_token_on_a_synthetic_route() -> None:
    guarded = APIRouter()

    @guarded.post("/guarded", dependencies=[Depends(require_control_token)])
    async def guarded_route() -> None:
        return None

    assert declares_control_token((guarded,), "POST", "/guarded")


def test_walker_detects_a_declared_control_token_on_the_workspace_scenario_override() -> None:
    assert declares_control_token((workspaces_router, tasks_router), *WORKSPACE_SCENARIO_OVERRIDE)


def test_chat_session_read_route_does_not_declare_control_token() -> None:
    assert not declares_control_token(ROUTERS, "GET", "/api/build-chat/sessions/{session_id}")


def test_every_non_get_chat_route_is_classified() -> None:
    actual = _non_get_routes(*ROUTERS)
    unclassified = actual - set(CHAT_MUTATIONS)
    stale = set(CHAT_MUTATIONS) - actual
    assert not unclassified, f"classify this route in CHAT_MUTATIONS: {sorted(unclassified)}"
    assert not stale, f"CHAT_MUTATIONS lists a route that no longer exists: {sorted(stale)}"

from __future__ import annotations

import pytest
from fastapi import APIRouter

from alan import server  # noqa: F401
from alan.routes._deps import GUARDED
from alan.routes.engine_presets import engine_presets_router
from alan.routes.engine_sources import engine_sources_router
from alan.routes.engines import engines_router
from alan.routes.engines_accent import engines_accent_router
from alan.routes.engines_lane import engines_lane_router
from alan.routes.engines_recurring_status import engines_recurring_status_router
from alan.routes.engines_visibility import engines_visibility_router
from alan.routes.workspaces import tasks_router, workspaces_router
from tests.control_token_inventory import (
    declares_control_token,
    get_routes,
    non_get_routes,
)

ENGINE_ROUTERS: tuple[APIRouter, ...] = (
    engines_router,
    engines_accent_router,
    engines_lane_router,
    engines_recurring_status_router,
    engines_visibility_router,
    engine_presets_router,
    engine_sources_router,
)

WORKSPACE_ARCHIVE = ("POST", "/api/workspaces/{task_id}/archive")

_ENGINE = "/api/engines/{owner}/{repo}"

ENGINE_MUTATIONS = [
    ("POST", "/api/engines"),
    ("PATCH", _ENGINE),
    ("DELETE", _ENGINE),
    ("POST", f"{_ENGINE}/posture"),
    ("PUT", f"{_ENGINE}/accent"),
    ("PUT", f"{_ENGINE}/lane/limit"),
    ("POST", f"{_ENGINE}/hide"),
    ("POST", f"{_ENGINE}/unhide"),
    ("POST", f"{_ENGINE}/dependency-sweep"),
    ("POST", f"{_ENGINE}/prompt-overrides"),
    ("POST", f"{_ENGINE}/prompt-overrides/rollback"),
    ("POST", f"{_ENGINE}/preflight"),
    ("POST", f"{_ENGINE}/config-proposal"),
    ("POST", f"{_ENGINE}/config-draft-validate"),
    ("POST", f"{_ENGINE}/scaffold"),
    ("POST", f"{_ENGINE}/protect"),
]

ENGINE_READS = [
    ("GET", "/api/engines"),
    ("GET", "/api/engines/sources"),
    ("GET", _ENGINE),
    ("GET", f"{_ENGINE}/status"),
    ("GET", f"{_ENGINE}/recurring-status"),
    ("GET", f"{_ENGINE}/pool"),
    ("GET", f"{_ENGINE}/preflight"),
    ("GET", f"{_ENGINE}/dependency-sweep"),
    ("GET", f"{_ENGINE}/prompt-overrides/history"),
    ("GET", f"{_ENGINE}/onboarding"),
    ("GET", f"{_ENGINE}/pinned-work"),
    ("GET", f"{_ENGINE}/lane"),
    ("GET", "/api/engine-presets"),
    ("GET", "/api/engine-presets/{name}"),
    ("GET", "/api/engine-presets/{name}/config"),
]

REQUIREMENT_IS_THE_DEPENDENCY = (
    "GUARDED is a responses documentation dict and adds no enforcement; "
    "the requirement is require_control_token declared as a route dependency"
)


RESPONSE_DOCUMENTATION_KEYS = frozenset({"description", "model", "content", "headers", "links"})


def test_guarded_holds_only_response_documentation() -> None:
    assert all(
        {"description"} <= set(entry) <= RESPONSE_DOCUMENTATION_KEYS for entry in GUARDED.values()
    ), REQUIREMENT_IS_THE_DEPENDENCY


def test_a_route_documenting_guarded_responses_without_the_dependency_is_not_enforced() -> None:
    documented_only = APIRouter()

    @documented_only.post("/documented", responses={**GUARDED})
    async def documented_route() -> None:
        return None

    assert not declares_control_token((documented_only,), "POST", "/documented"), (
        REQUIREMENT_IS_THE_DEPENDENCY
    )


@pytest.mark.parametrize(
    ("method", "path"),
    ENGINE_MUTATIONS,
    ids=[f"{method} {path}" for method, path in ENGINE_MUTATIONS],
)
def test_engine_mutation_route_requires_control_token(method: str, path: str) -> None:
    assert declares_control_token(ENGINE_ROUTERS, method, path), (
        f"{method} {path} must declare require_control_token as a dependency. "
        f"{REQUIREMENT_IS_THE_DEPENDENCY}."
    )


@pytest.mark.parametrize(
    ("method", "path"),
    ENGINE_READS,
    ids=[f"{method} {path}" for method, path in ENGINE_READS],
)
def test_engine_read_route_does_not_declare_control_token(method: str, path: str) -> None:
    assert not declares_control_token(ENGINE_ROUTERS, method, path)


def test_walker_detects_the_absence_of_a_control_token_on_a_synthetic_route() -> None:
    open_router = APIRouter()

    @open_router.post("/open")
    async def open_route() -> None:
        return None

    assert not declares_control_token((open_router,), "POST", "/open")


def test_walker_detects_the_absence_of_a_control_token_on_the_workspace_archive_route() -> None:
    assert not declares_control_token((workspaces_router, tasks_router), *WORKSPACE_ARCHIVE)


def test_every_non_get_engine_route_is_classified() -> None:
    actual = non_get_routes(ENGINE_ROUTERS)
    unclassified = actual - set(ENGINE_MUTATIONS)
    stale = set(ENGINE_MUTATIONS) - actual
    assert not unclassified, f"classify this route in ENGINE_MUTATIONS: {sorted(unclassified)}"
    assert not stale, f"ENGINE_MUTATIONS lists a route that no longer exists: {sorted(stale)}"


def test_every_get_engine_route_is_classified() -> None:
    actual = get_routes(ENGINE_ROUTERS)
    unclassified = actual - set(ENGINE_READS)
    stale = set(ENGINE_READS) - actual
    assert not unclassified, f"classify this route in ENGINE_READS: {sorted(unclassified)}"
    assert not stale, f"ENGINE_READS lists a route that no longer exists: {sorted(stale)}"

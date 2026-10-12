from __future__ import annotations

import pytest
from fastapi import APIRouter

from alan import server  # noqa: F401
from alan.routes.operator_terminal import operator_terminal_router
from tests.control_token_inventory import declares_control_token, non_get_routes

ROUTERS: tuple[APIRouter, ...] = (operator_terminal_router,)

OPERATOR_MUTATIONS = [
    ("POST", "/api/operator/session"),
    ("DELETE", "/api/operator/session"),
    ("POST", "/api/operator/attach-ticket"),
]


@pytest.mark.parametrize(
    ("method", "path"),
    OPERATOR_MUTATIONS,
    ids=[f"{method} {path}" for method, path in OPERATOR_MUTATIONS],
)
def test_operator_mutation_route_declares_the_control_token(method: str, path: str) -> None:
    assert declares_control_token(ROUTERS, method, path)


def test_operator_status_route_needs_no_control_token() -> None:
    assert not declares_control_token(ROUTERS, "GET", "/api/operator/status")


def test_every_non_get_operator_route_is_classified() -> None:
    unclassified = non_get_routes(ROUTERS) - set(OPERATOR_MUTATIONS)
    stale = set(OPERATOR_MUTATIONS) - non_get_routes(ROUTERS)
    assert not unclassified, f"classify this route in OPERATOR_MUTATIONS: {sorted(unclassified)}"
    assert not stale, f"OPERATOR_MUTATIONS lists a route that no longer exists: {sorted(stale)}"

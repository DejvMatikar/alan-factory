from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from alan.routes._deps import require_control_token


def find_route(routers: Iterable[APIRouter], method: str, path: str) -> APIRoute:
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path == path and method in (route.methods or set()):
                return route
    raise AssertionError(f"no {method} route registered at {path}")


def routes_with_methods(
    routers: Iterable[APIRouter], *, include_get: bool
) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
        if (method == "GET") == include_get
    }


def non_get_routes(routers: Iterable[APIRouter]) -> set[tuple[str, str]]:
    return routes_with_methods(routers, include_get=False)


def get_routes(routers: Iterable[APIRouter]) -> set[tuple[str, str]]:
    return routes_with_methods(routers, include_get=True)


def declared_calls(dependant: Dependant) -> set[Callable[..., Any]]:
    calls: set[Callable[..., Any]] = set()
    if dependant.call is not None:
        calls.add(dependant.call)
    for sub in dependant.dependencies:
        calls |= declared_calls(sub)
    return calls


def declares_control_token(routers: Iterable[APIRouter], method: str, path: str) -> bool:
    return require_control_token in declared_calls(find_route(routers, method, path).dependant)

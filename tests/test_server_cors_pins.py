from __future__ import annotations

from typing import Any, cast

from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from alan import server


def _cors_kwargs() -> dict[str, Any]:
    for middleware in server.app.user_middleware:
        if cast(Any, middleware.cls) is CORSMiddleware:
            return cast(dict[str, Any], middleware.kwargs)
    raise AssertionError("CORSMiddleware not registered on alan.server.app")


def test_cors_allow_origins_pinned_to_dev_pair() -> None:
    assert _cors_kwargs()["allow_origins"] == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_allow_headers_pinned_to_content_type_only() -> None:
    assert _cors_kwargs()["allow_headers"] == ["Content-Type"]


def test_cors_preflight_rejects_control_token_header() -> None:
    with TestClient(server.app) as client:
        res = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Alan-Control-Token",
            },
        )
    assert "X-Alan-Control-Token" not in res.headers.get("access-control-allow-headers", "")

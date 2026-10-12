from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import engine_config as engine_config_mod
from alan.engine_config import EngineConfig, RepoIdentity
from alan.routes import engines as engines_route_mod

_UNKNOWN_ENGINE = "/api/engines/ghost/nowhere"
_OVERRIDE = {"team": "implementation", "role": "implementer", "text": "x"}

_LOOKUP_THEN_404_ROUTES: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("get", "", None, False),
    ("post", "/dependency-sweep", {"enabled": True}, True),
    ("get", "/dependency-sweep", None, False),
    ("post", "/prompt-overrides", _OVERRIDE, True),
    ("get", "/prompt-overrides/history?team=implementation&role=implementer", None, False),
    ("post", "/prompt-overrides/rollback", {**_OVERRIDE, "version": 1}, True),
    ("post", "/preflight", None, True),
    ("get", "/preflight", None, False),
    ("post", "/scaffold", None, True),
    ("post", "/protect", None, True),
    ("get", "/onboarding", None, False),
    ("get", "/pinned-work", None, False),
    ("delete", "", None, True),
    ("get", "/pool", None, False),
    ("get", "/agents", None, False),
    ("get", "/agents/summary", None, False),
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path / "workspaces"
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: object()
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


@pytest.mark.parametrize(
    ("method", "suffix", "body", "guarded"),
    _LOOKUP_THEN_404_ROUTES,
    ids=[f"{route[0]}{route[1].split('?')[0]}" for route in _LOOKUP_THEN_404_ROUTES],
)
def test_unknown_engine_returns_the_shared_engine_not_found_404(
    client: TestClient, method: str, suffix: str, body: dict[str, Any] | None, guarded: bool
) -> None:
    res = client.request(
        method,
        f"{_UNKNOWN_ENGINE}{suffix}",
        json=body,
        headers=_control_headers() if guarded else None,
    )
    assert res.status_code == 404
    assert res.json() == {"detail": "engine not found"}


async def test_require_engine_returns_the_registered_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    config = EngineConfig(repo=identity)
    monkeypatch.setattr(
        engine_config_mod, "lookup_by_repo", lambda _requested: config
    )
    assert await engines_route_mod._require_engine(identity) is config  # noqa: SLF001


async def test_require_engine_raises_404_for_an_unregistered_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_config_mod, "lookup_by_repo", lambda _requested: None)
    with pytest.raises(HTTPException) as exc_info:
        await engines_route_mod._require_engine(  # noqa: SLF001
            RepoIdentity(owner="ghost", repo="nowhere")
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "engine not found"


def test_lookup_then_404_route_count_matches_the_collapsed_engine_routes() -> None:
    assert len(_LOOKUP_THEN_404_ROUTES) == 16

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import agent_catalogue, agent_key, server
from alan import engine_config as engine_config_mod
from alan.agent_catalogue_helpers import ResolverGroup
from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig
from tests._agent_catalogue_support import make_resolved

_IDENTITY = RepoIdentity(owner="acme", repo="widget")
_BASE = "/api/engines/acme/widget"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path / "workspaces"
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: None
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def registered_engine() -> EngineConfig:
    cfg = EngineConfig(
        repo=_IDENTITY,
        roles={
            "judge": RoleConfig(model="opus"),
            "remediator": RoleConfig(model="opus", effort="xhigh"),
        },
    )
    engine_config_mod.register(cfg, validate_env=False)
    return cfg


def _fixed_groups(monkeypatch: pytest.MonkeyPatch, *, with_failure: bool = False) -> None:
    resolved = make_resolved("collision")

    def broken(_cfg: EngineConfig) -> list[object]:
        raise RuntimeError("resolver exploded")

    groups = [
        ResolverGroup(
            id="collision",
            name="collision",
            key=agent_key.COLLISION,
            resolve=lambda _cfg: [resolved],
        )
    ]
    if with_failure:
        groups.append(
            ResolverGroup(
                id="broken",
                name="broken",
                key=agent_key.MANAGER_SUMMARY,
                resolve=broken,  # type: ignore[arg-type]
            )
        )
    monkeypatch.setattr(agent_catalogue, "resolver_groups", lambda: tuple(groups))


def test_unknown_engines_return_404_on_both_routes(client: TestClient) -> None:
    for suffix in ("/agents", "/agents/summary"):
        res = client.get(f"/api/engines/ghost/nowhere{suffix}")
        assert res.status_code == 404
        assert res.json() == {"detail": "engine not found"}


@pytest.mark.usefixtures("registered_engine")
def test_the_catalogue_is_resolved_for_the_viewed_repository(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_groups(monkeypatch)
    res = client.get(f"{_BASE}/agents")
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "acme/widget"
    assert [entry["id"] for entry in body["entries"]] == ["background_helper:collision"]
    assert body["notes"]["appended_prompt"] == agent_catalogue.APPENDED_PROMPT_NOTE
    assert [flag["code"] for flag in body["flags"]] == ["unread_stored_role"] * 2


@pytest.mark.usefixtures("registered_engine")
def test_a_failing_resolver_is_isolated_into_an_error_entry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_groups(monkeypatch, with_failure=True)
    res = client.get(f"{_BASE}/agents")
    assert res.status_code == 200
    entries = {entry["id"]: entry for entry in res.json()["entries"]}
    assert entries["background_helper:manager_summary"]["error"] == (
        "RuntimeError: resolver exploded"
    )
    assert entries["background_helper:collision"]["error"] is None


@pytest.mark.usefixtures("registered_engine")
def test_the_summary_route_counts_the_catalogue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_groups(monkeypatch, with_failure=True)
    res = client.get(f"{_BASE}/agents/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "acme/widget"
    assert body["total"] == 2
    assert body["errors"] == 1
    assert body["by_model"] == [{"name": "sonnet", "count": 1}]


@pytest.mark.usefixtures("registered_engine")
def test_the_routes_are_read_only(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        for suffix in ("/agents", "/agents/summary"):
            res = client.request(method, f"{_BASE}{suffix}")
            assert res.status_code in {403, 405}


@pytest.mark.usefixtures("registered_engine")
def test_the_response_never_contains_environment_values(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SONAR_TOKEN", "route-secret-value")
    _fixed_groups(monkeypatch)
    payload = json.dumps(client.get(f"{_BASE}/agents").json())
    assert "route-secret-value" not in payload
    assert '"name": "SONAR_TOKEN"' in payload

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from alan import server
from alan.routes.engines import ConfigProposalRequest


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def test_list_engine_presets_returns_sorted_names(client: TestClient) -> None:
    res = client.get("/api/engine-presets")
    assert res.status_code == 200
    names = [p["name"] for p in res.json()["presets"]]
    assert names == ["dotnet", "ios", "node", "python"]


def test_list_engine_presets_does_not_require_control_token(client: TestClient) -> None:
    res = client.get("/api/engine-presets")
    assert res.status_code == 200


def test_get_engine_preset_dotnet_returns_config(client: TestClient) -> None:
    res = client.get("/api/engine-presets/dotnet")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "dotnet"
    assert body["config"]["preset"] == "dotnet"
    assert "Bash(dotnet:*)" in body["config"]["base_tools"]
    assert body["config"]["forge"] is None


def test_get_engine_preset_python_returns_config(client: TestClient) -> None:
    res = client.get("/api/engine-presets/python")
    assert res.status_code == 200
    assert res.json()["config"]["preset"] == "python"


def test_get_engine_preset_node_returns_config(client: TestClient) -> None:
    res = client.get("/api/engine-presets/node")
    assert res.status_code == 200
    assert res.json()["config"]["preset"] == "node"


def test_get_engine_preset_ios_returns_config(client: TestClient) -> None:
    res = client.get("/api/engine-presets/ios")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "ios"
    assert body["config"]["preset"] == "ios"
    assert "Bash(xcodebuild:*)" in body["config"]["base_tools"]
    assert body["config"]["forge"] is None


def test_get_engine_preset_unknown_returns_404(client: TestClient) -> None:
    res = client.get("/api/engine-presets/cobol")
    assert res.status_code == 404


def test_get_engine_preset_does_not_require_control_token(client: TestClient) -> None:
    res = client.get("/api/engine-presets/dotnet")
    assert res.status_code == 200


def test_get_engine_preset_config_returns_forge_baseline(client: TestClient) -> None:
    res = client.get(
        "/api/engine-presets/python/config", params={"owner": "acme", "repo": "widget"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["repo"] == {"owner": "acme", "repo": "widget"}
    assert body["forge"]["repo"] == {"owner": "acme", "repo": "widget"}
    assert body["forge"]["credential_env_name"] == "ACME_WIDGET_GH_TOKEN"
    assert body["posture"] == "observe"


def test_get_engine_preset_config_unknown_preset_404(client: TestClient) -> None:
    res = client.get(
        "/api/engine-presets/cobol/config", params={"owner": "acme", "repo": "widget"}
    )
    assert res.status_code == 404


def test_get_engine_preset_config_blank_owner_422(client: TestClient) -> None:
    res = client.get(
        "/api/engine-presets/python/config", params={"owner": "", "repo": "widget"}
    )
    assert res.status_code == 422


def test_get_engine_preset_config_blank_repo_422(client: TestClient) -> None:
    res = client.get(
        "/api/engine-presets/python/config", params={"owner": "acme", "repo": ""}
    )
    assert res.status_code == 422


def test_get_engine_preset_config_needs_no_control_token(client: TestClient) -> None:
    res = client.get(
        "/api/engine-presets/python/config", params={"owner": "acme", "repo": "widget"}
    )
    assert res.status_code == 200


def test_config_proposal_request_accepts_ios_base_preset() -> None:
    assert ConfigProposalRequest(base_preset="ios").base_preset == "ios"
    with pytest.raises(ValidationError):
        ConfigProposalRequest.model_validate({"base_preset": "cobol"})

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, secrets_store, server
from alan.github_repos import GithubReposResult, RepoCapability
from alan.routes import github as github_routes


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def client(secrets_root: Path) -> Iterator[TestClient]:
    _ = secrets_root
    with TestClient(server.app) as c:
        yield c


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _post_repos(client, *, token="ghp_faketoken", headers=None):  # noqa: S107
    return client.post(
        "/api/github/repos",
        json={"token": token},
        headers=headers if headers is not None else _control_headers(),
    )


def test_list_repos_without_control_token_returns_401(client: TestClient) -> None:
    res = client.post("/api/github/repos", json={"token": "t"})
    assert res.status_code == 401


def test_list_repos_wrong_control_token_returns_403(client: TestClient) -> None:
    res = _post_repos(client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"})
    assert res.status_code == 403


def test_list_repos_missing_token_returns_422(client: TestClient) -> None:
    res = client.post("/api/github/repos", json={}, headers=_control_headers())
    assert res.status_code == 422


def test_list_repos_empty_token_returns_422(client: TestClient) -> None:
    res = _post_repos(client, token="")
    assert res.status_code == 422


def test_list_repos_oversized_token_returns_422(client: TestClient) -> None:
    res = _post_repos(client, token="x" * 4001)
    assert res.status_code == 422


def test_list_repos_200_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_result = GithubReposResult(
        repos=(RepoCapability(slug="acme/widget", read=True, write=True, admin=True),),
    )
    monkeypatch.setattr(github_routes, "probe_repos", lambda token: fake_result)  # noqa: ARG005
    res = _post_repos(client)
    assert res.status_code == 200
    body = res.json()
    assert body["repos"][0]["slug"] == "acme/widget"
    assert "required_permissions" in body


def test_list_repos_passes_body_token_to_probe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def _fake(token: str) -> GithubReposResult:
        captured["token"] = token
        return GithubReposResult()

    monkeypatch.setattr(github_routes, "probe_repos", _fake)
    _post_repos(client, token="ghp_specific_value")  # noqa: S106
    assert captured["token"] == "ghp_specific_value"  # noqa: S105


def test_list_repos_does_not_mutate_secrets_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github_routes, "probe_repos", lambda token: GithubReposResult())  # noqa: ARG005
    before = secrets_store.list_secrets()
    _post_repos(client)
    after = secrets_store.list_secrets()
    assert before == after == []


def test_list_repos_response_round_trips_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_result = GithubReposResult(
        repos=(RepoCapability(slug="acme/widget", read=True, write=False, admin=False),),
    )
    monkeypatch.setattr(github_routes, "probe_repos", lambda token: fake_result)  # noqa: ARG005
    res = _post_repos(client)
    validated = GithubReposResult.model_validate(res.json())
    assert validated == fake_result

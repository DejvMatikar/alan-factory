from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, secrets_store, server
from alan.teams._base import TeamEvent

_SECRET_VALUE = "never-echoed-never-echoed"  # noqa: S105


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


def _create(client, *, engine="engine-a", purpose="transport", value=_SECRET_VALUE):
    return client.post(
        "/api/secrets",
        json={"engine": engine, "purpose": purpose, "value": value},
        headers=_control_headers(),
    )


def _delete(client, *, engine="engine-a", purpose="transport"):
    return client.request(
        "DELETE",
        "/api/secrets",
        json={"engine": engine, "purpose": purpose},
        headers=_control_headers(),
    )


def test_create_secret_returns_201_with_name_and_fingerprint(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "engine-a:transport"
    assert body["fingerprint"] == secrets_store.fingerprint(_SECRET_VALUE)
    assert body["last_verified"] is None
    assert "value" not in body


def test_create_secret_publishes_secret_written_event(client: TestClient) -> None:
    captured: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(captured.append)
    try:
        _create(client, engine="acme/widget", purpose="transport")
    finally:
        unsubscribe()
    matching = [e for e in captured if e.kind == "secret_written"]
    assert len(matching) == 1
    assert matching[0].payload == {"engine": "acme/widget", "purpose": "transport"}


def test_create_secret_overwrite_updates_fingerprint(client: TestClient) -> None:
    _create(client, value="first-value")
    res = _create(client, value="second-value")
    assert res.status_code == 201
    assert res.json()["fingerprint"] == secrets_store.fingerprint("second-value")


def test_create_secret_whitespace_only_engine_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={"engine": "   ", "purpose": "transport", "value": "v"},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_secret_missing_value_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={"engine": "engine-a", "purpose": "transport"},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_secret_empty_value_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={"engine": "engine-a", "purpose": "transport", "value": ""},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_secret_with_expires_at_returns_expiry_fields(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "expires_at": "2099-01-01",
        },
        headers=_control_headers(),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["expires_at"] == "2099-01-01"
    assert body["expiry_warned_at"] is None


def test_create_secret_without_expires_at_has_null_expiry_fields(client: TestClient) -> None:
    res = _create(client)
    body = res.json()
    assert body["expires_at"] is None
    assert body["expiry_warned_at"] is None


def test_create_secret_malformed_expires_at_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v",
            "expires_at": "not-a-date",
        },
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_secret_without_control_token_returns_401(client: TestClient) -> None:
    res = client.post(
        "/api/secrets", json={"engine": "engine-a", "purpose": "transport", "value": "v"}
    )
    assert res.status_code == 401


def test_create_secret_wrong_control_token_returns_403(client: TestClient) -> None:
    res = client.post(
        "/api/secrets",
        json={"engine": "engine-a", "purpose": "transport", "value": "v"},
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_create_secret_with_correct_control_token_succeeds(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 201


def test_delete_secret_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = client.request(
        "DELETE", "/api/secrets", json={"engine": "engine-a", "purpose": "transport"}
    )
    assert res.status_code == 401


def test_delete_secret_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = client.request(
        "DELETE",
        "/api/secrets",
        json={"engine": "engine-a", "purpose": "transport"},
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_delete_secret_with_correct_control_token_succeeds(client: TestClient) -> None:
    _create(client)
    res = _delete(client)
    assert res.status_code == 204


def test_list_secrets_does_not_require_control_token(client: TestClient) -> None:
    res = client.get("/api/secrets")
    assert res.status_code == 200


def test_list_secrets_empty_when_none_created(client: TestClient) -> None:
    res = client.get("/api/secrets")
    assert res.status_code == 200
    assert res.json() == {"secrets": []}


def test_list_secrets_returns_created_entries(client: TestClient) -> None:
    _create(client, engine="engine-a", purpose="transport", value="v1")
    _create(client, engine="engine-b", purpose="code_quality", value="v2")
    res = client.get("/api/secrets")
    assert res.status_code == 200
    names = {row["name"] for row in res.json()["secrets"]}
    assert names == {"engine-a:transport", "engine-b:code_quality"}


def test_list_secrets_returns_expiry_fields(client: TestClient) -> None:
    client.post(
        "/api/secrets",
        json={
            "engine": "engine-a",
            "purpose": "transport",
            "value": "v1",
            "expires_at": "2099-05-05",
        },
        headers=_control_headers(),
    )
    res = client.get("/api/secrets")
    row = res.json()["secrets"][0]
    assert row["expires_at"] == "2099-05-05"
    assert row["expiry_warned_at"] is None


def test_delete_secret_returns_204(client: TestClient) -> None:
    _create(client)
    res = _delete(client)
    assert res.status_code == 204
    assert client.get("/api/secrets").json() == {"secrets": []}


def test_delete_secret_missing_returns_404(client: TestClient) -> None:
    res = _delete(client)
    assert res.status_code == 404


def test_delete_secret_whitespace_only_purpose_returns_422(client: TestClient) -> None:
    res = client.request(
        "DELETE",
        "/api/secrets",
        json={"engine": "engine-a", "purpose": "  "},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_redaction_api_responses_never_contain_raw_value(client: TestClient) -> None:
    create_res = _create(client)
    list_res = client.get("/api/secrets")
    delete_res = _delete(client)
    bad_res = client.post(
        "/api/secrets",
        json={"engine": "  ", "purpose": "transport", "value": _SECRET_VALUE},
        headers=_control_headers(),
    )
    for res in (create_res, list_res, delete_res, bad_res):
        assert _SECRET_VALUE not in res.text


def test_redaction_logs_never_contain_raw_value(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        _create(client)
        client.get("/api/secrets")
        _delete(client)
        client.post(
            "/api/secrets",
            json={"engine": "  ", "purpose": "transport", "value": _SECRET_VALUE},
            headers=_control_headers(),
        )
    for record in caplog.records:
        assert _SECRET_VALUE not in record.getMessage()


def test_redaction_audit_jsonl_never_contains_raw_value(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    _create(client)
    client.get("/api/secrets")
    _delete(client)
    if not logs_root.is_dir():
        return
    for path in logs_root.rglob("*.jsonl"):
        assert _SECRET_VALUE not in path.read_text(encoding="utf-8")


def test_redaction_sse_payloads_never_contain_raw_value(client: TestClient) -> None:
    captured: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(captured.append)
    try:
        _create(client)
        client.get("/api/secrets")
        _delete(client)
    finally:
        unsubscribe()
    for event in captured:
        assert _SECRET_VALUE not in json.dumps(event.payload)

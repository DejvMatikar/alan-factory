from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, control_token_store, server

_STATUS_URL = "/api/control-token/status"
_EXTEND_URL = "/api/control-token/extend"
_ROTATE_URL = "/api/control-token/rotate"
_REVOKE_URL = "/api/control-token/revoke"
_RESPONSE_KEYS = {"state", "deadline"}


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def client(state_root: Path) -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _auth_headers(token: str) -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: token}


def _active_token() -> str:
    return os.environ[control_plane.CONTROL_TOKEN_ENV]


def test_status_succeeds_with_no_credential(client: TestClient) -> None:
    res = client.get(_STATUS_URL)
    assert res.status_code == 200
    assert set(res.json().keys()) == _RESPONSE_KEYS


def test_status_reports_active_for_a_freshly_bootstrapped_token(client: TestClient) -> None:
    body = client.get(_STATUS_URL).json()
    assert body["state"] == "active"
    assert body["deadline"] is not None


def test_status_never_leaks_the_token_value(client: TestClient) -> None:
    token = _active_token()
    res = client.get(_STATUS_URL)
    assert token not in res.text


def test_extend_requires_control_token(client: TestClient) -> None:
    res = client.post(_EXTEND_URL)
    assert res.status_code == 401


def test_extend_rejects_mismatched_token(client: TestClient) -> None:
    res = client.post(_EXTEND_URL, headers=_auth_headers("wrong-token"))
    assert res.status_code == 403


def test_extend_moves_the_deadline_and_keeps_the_same_token_valid(client: TestClient) -> None:
    token = _active_token()
    before = client.get(_STATUS_URL).json()["deadline"]

    res = client.post(_EXTEND_URL, headers=_auth_headers(token))
    assert res.status_code == 200
    assert set(res.json().keys()) == _RESPONSE_KEYS
    after = res.json()["deadline"]
    assert after is not None
    assert before is not None
    assert datetime.fromisoformat(after) > datetime.fromisoformat(before)

    follow_up = client.post(_EXTEND_URL, headers=_auth_headers(token))
    assert follow_up.status_code == 200


def test_extend_response_never_carries_the_token_value(client: TestClient) -> None:
    token = _active_token()
    res = client.post(_EXTEND_URL, headers=_auth_headers(token))
    assert token not in res.text


def test_rotate_requires_control_token(client: TestClient) -> None:
    res = client.post(_ROTATE_URL)
    assert res.status_code == 401


def test_rotate_invalidates_the_old_token_and_issues_a_working_new_one(
    client: TestClient,
) -> None:
    old_token = _active_token()

    res = client.post(_ROTATE_URL, headers=_auth_headers(old_token))
    assert res.status_code == 200
    assert set(res.json().keys()) == _RESPONSE_KEYS

    old_still_used = client.post(_EXTEND_URL, headers=_auth_headers(old_token))
    assert old_still_used.status_code == 403

    new_token = os.environ[control_plane.CONTROL_TOKEN_ENV]
    assert new_token != old_token
    new_works = client.post(_EXTEND_URL, headers=_auth_headers(new_token))
    assert new_works.status_code == 200


def test_rotate_response_never_carries_the_new_token_value(client: TestClient) -> None:
    old_token = _active_token()
    res = client.post(_ROTATE_URL, headers=_auth_headers(old_token))
    new_token = os.environ[control_plane.CONTROL_TOKEN_ENV]
    assert new_token not in res.text


def test_rotate_rejects_an_expired_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    expired_value, _meta = control_token_store.load_or_generate(
        root, now=datetime.now(UTC) - timedelta(days=31)
    )
    assert expired_value is not None
    control_token_store.reset_cached_meta()

    with TestClient(server.app) as c:
        res = c.post(_ROTATE_URL, headers=_auth_headers(expired_value))
        assert res.status_code == 401

        status_after = c.get(_STATUS_URL).json()
        assert status_after["state"] == "expired"


def test_revoke_requires_control_token(client: TestClient) -> None:
    res = client.post(_REVOKE_URL)
    assert res.status_code == 401


def test_revoke_kills_the_token_with_no_replacement(client: TestClient) -> None:
    token = _active_token()

    res = client.post(_REVOKE_URL, headers=_auth_headers(token))
    assert res.status_code == 200
    assert set(res.json().keys()) == _RESPONSE_KEYS
    assert res.json()["state"] == "revoked"
    assert res.json()["deadline"] is None

    assert control_plane.CONTROL_TOKEN_ENV not in os.environ

    locked_out = client.post(_EXTEND_URL, headers=_auth_headers(token))
    assert locked_out.status_code == 500


def test_revoke_status_survives_a_simulated_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    with TestClient(server.app) as c:
        token = os.environ[control_plane.CONTROL_TOKEN_ENV]
        c.post(_REVOKE_URL, headers=_auth_headers(token))

    with TestClient(server.app) as c2:
        status = c2.get(_STATUS_URL).json()
        assert status["state"] == "revoked"

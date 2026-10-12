from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from alan import control_plane, control_token_store
from alan.routes import _deps

_EXPECTED_TOKEN = "expected-control-token"  # noqa: S105


@pytest.fixture
def guarded_app() -> FastAPI:
    app = FastAPI()

    @app.post("/guarded", dependencies=[Depends(_deps.require_control_token)])
    async def guarded() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def client(guarded_app: FastAPI) -> TestClient:
    return TestClient(guarded_app)


@pytest.fixture(autouse=True)
def _reset_store_cache() -> Iterator[None]:
    control_token_store.reset_cached_meta()
    yield
    control_token_store.reset_cached_meta()


def test_require_control_token_401_when_header_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    res = client.post("/guarded")
    assert res.status_code == 401
    assert res.json() == {"detail": "control token required"}


def test_require_control_token_403_when_header_mismatched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403
    assert res.json() == {"detail": "control token mismatch"}


def test_require_control_token_200_when_header_matches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: _EXPECTED_TOKEN}
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_require_control_token_500_when_env_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: "whatever"}
    )
    assert res.status_code == 500
    assert res.json() == {"detail": "control token not configured"}


def test_require_control_token_500_precedes_401_when_header_also_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    res = client.post("/guarded")
    assert res.status_code == 500


def test_require_control_token_401_expired_when_cached_meta_matches_and_is_past_expiry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, _meta = control_token_store.load_or_generate(
        tmp_path, now=datetime.now(UTC) - timedelta(days=31)
    )
    assert value is not None
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, value)
    res = client.post("/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: value})
    assert res.status_code == 401
    assert res.json() == {"detail": "control token expired"}


def test_require_control_token_allows_when_cached_meta_not_yet_expired(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value, _meta = control_token_store.load_or_generate(tmp_path, now=datetime.now(UTC))
    assert value is not None
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, value)
    res = client.post("/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: value})
    assert res.status_code == 200


def test_require_control_token_ignores_expiry_when_cached_meta_is_for_a_different_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    mismatched_meta = control_token_store.ControlTokenMeta(
        state="active",
        token="a-completely-different-cached-token",
        issued_at=datetime.now(UTC) - timedelta(days=40),
        expires_at=datetime.now(UTC) - timedelta(days=10),
    )
    control_token_store._set_cached_meta(mismatched_meta)  # noqa: SLF001
    res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: _EXPECTED_TOKEN}
    )
    assert res.status_code == 200


def test_require_control_token_ignores_expiry_when_no_meta_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    assert control_token_store.current_meta().token is None
    res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: _EXPECTED_TOKEN}
    )
    assert res.status_code == 200


def test_missing_and_expired_control_token_produce_distinct_401_details(
    guarded_app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = TestClient(guarded_app)
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, _EXPECTED_TOKEN)
    missing_res = client.post("/guarded")
    assert missing_res.status_code == 401
    missing_detail = missing_res.json()["detail"]

    value, _meta = control_token_store.load_or_generate(
        tmp_path, now=datetime.now(UTC) - timedelta(days=31)
    )
    assert value is not None
    monkeypatch.setenv(control_plane.CONTROL_TOKEN_ENV, value)
    expired_res = client.post(
        "/guarded", headers={control_plane.CONTROL_TOKEN_HEADER: value}
    )
    assert expired_res.status_code == 401
    expired_detail = expired_res.json()["detail"]

    assert missing_detail != expired_detail


def test_guarded_expirable_differs_from_guarded_only_in_the_401_description() -> None:
    assert set(_deps.GUARDED) == {401, 403, 500}
    assert set(_deps.GUARDED_EXPIRABLE) == {401, 403, 500}
    assert _deps.GUARDED_EXPIRABLE[403] == _deps.GUARDED[403]
    assert _deps.GUARDED_EXPIRABLE[500] == _deps.GUARDED[500]
    assert _deps.GUARDED[401] == {"description": "control token missing"}
    assert _deps.GUARDED_EXPIRABLE[401] == {"description": "control token missing or expired"}

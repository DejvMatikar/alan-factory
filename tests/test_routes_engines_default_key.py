from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import engine_config, server
from alan.engine_config import EngineConfig, RepoIdentity, default_engine_config

_ACME = RepoIdentity(owner="acme", repo="widget")
_LISTED_DEFAULT = RepoIdentity(owner="acme", repo="listed-default")
_OTHER = RepoIdentity(owner="acme", repo="other")


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _register(identity: RepoIdentity, *, default: bool = False) -> None:
    engine_config.register(EngineConfig(repo=identity), default=default, validate_env=False)


def _register_default_not_first() -> None:
    _register(_ACME)
    _register(_LISTED_DEFAULT, default=True)
    _register(_OTHER)


def _register_no_default() -> None:
    _register(_ACME)
    _register(_OTHER)


def _register_nothing() -> None:
    return None


def _listing() -> dict[str, Any]:
    with TestClient(server.app) as client:
        response = client.get("/api/engines")
    assert response.status_code == 200
    listing: dict[str, Any] = response.json()
    return listing


def test_list_names_the_registered_default_engine() -> None:
    _register_default_not_first()
    listing = _listing()
    assert [e["repo"]["repo"] for e in listing["engines"]] == [
        "widget",
        "listed-default",
        "other",
    ]
    assert listing["default_engine_key"] == _LISTED_DEFAULT.slug


def test_list_names_the_fallback_default_when_no_default_is_registered() -> None:
    _register_no_default()
    assert engine_config.get_registered() is None
    assert _listing()["default_engine_key"] == default_engine_config().repo.slug


def test_list_names_the_default_on_an_empty_registry() -> None:
    listing = _listing()
    assert listing["engines"] == []
    assert listing["default_engine_key"] == default_engine_config().repo.slug


@pytest.mark.parametrize(
    "arrange",
    [_register_default_not_first, _register_no_default, _register_nothing],
)
def test_list_default_key_equals_canonical_scope_key_none(arrange: Callable[[], None]) -> None:
    arrange()
    assert _listing()["default_engine_key"] == engine_config.canonical_scope_key(None).slug


def test_list_engine_items_carry_no_default_flag() -> None:
    _register_default_not_first()
    items = _listing()["engines"]
    assert [set(item) for item in items] == [set(EngineConfig.model_fields)] * 3

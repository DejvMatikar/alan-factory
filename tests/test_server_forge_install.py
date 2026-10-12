from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alan import forge as forge_mod
from alan import server
from alan.forge_gh import GhForge


def test_lifespan_installs_gh_forge_as_the_active_forge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        assert isinstance(forge_mod._active_forge, GhForge)  # pyright: ignore[reportPrivateUsage]

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest
import uvicorn
import uvicorn.importer
from fastapi.testclient import TestClient

from alan import __main__ as main_mod
from alan import server

_REMOVED_OPT_IN = "ALAN_ALLOW_NON_LOOPBACK_BIND"


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> tuple[str, int]:
    captured: dict[str, Any] = {}
    original_level = logging.getLogger("alan").level

    def _fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["alan", *argv])
    try:
        main_mod.main()
        resolved = uvicorn.importer.import_from_string(captured["app"])
        assert resolved is server.served_app
        assert captured["reload"] is False
        return captured["host"], captured["port"]
    finally:
        logging.getLogger("alan").setLevel(original_level)


def test_main_serves_the_guarded_app_that_refuses_a_foreign_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    captured: dict[str, Any] = {}
    original_level = logging.getLogger("alan").level

    def _fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured["app"] = app

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["alan"])
    try:
        main_mod.main()
    finally:
        logging.getLogger("alan").setLevel(original_level)
    resolved = uvicorn.importer.import_from_string(captured["app"])
    with TestClient(resolved, base_url="http://rebind.example.test") as guarded_client:
        res = guarded_client.get("/api/health")
    assert res.status_code == 400
    assert res.json() == {"detail": "host not allowed"}


def test_main_defaults_when_nothing_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    monkeypatch.delenv("ALAN_PORT", raising=False)
    assert _run_main(monkeypatch, []) == ("127.0.0.1", 8585)


def test_main_honors_env_overrides_when_flags_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "127.0.0.2")
    monkeypatch.setenv("ALAN_PORT", "9100")
    assert _run_main(monkeypatch, []) == ("127.0.0.2", 9100)


def test_main_explicit_flags_beat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "127.0.0.2")
    monkeypatch.setenv("ALAN_PORT", "9100")
    result = _run_main(monkeypatch, ["--host", "::1", "--port", "1234"])
    assert result == ("::1", 1234)


def _refused_main(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], *, removed_opt_in: str | None = None
) -> pytest.ExceptionInfo[SystemExit]:
    runs: list[str] = []
    original_level = logging.getLogger("alan").level

    def _fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        runs.append(host)

    monkeypatch.delenv(_REMOVED_OPT_IN, raising=False)
    if removed_opt_in is not None:
        monkeypatch.setenv(_REMOVED_OPT_IN, removed_opt_in)
    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["alan", *argv])
    try:
        with pytest.raises(SystemExit) as refused:
            main_mod.main()
    finally:
        logging.getLogger("alan").setLevel(original_level)
    assert runs == []
    return refused


def _assert_refusal_names(
    refused: pytest.ExceptionInfo[SystemExit], value: str, source: str
) -> None:
    code = refused.value.code
    assert isinstance(code, str)
    assert repr(value) in code
    assert f"from {source}" in code
    assert _REMOVED_OPT_IN not in code


def test_main_refuses_a_non_loopback_env_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    _assert_refusal_names(_refused_main(monkeypatch, []), "10.20.30.40", "ALAN_HOST")


def test_main_refuses_a_non_loopback_flag_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    _assert_refusal_names(_refused_main(monkeypatch, ["--host", "10.0.0.1"]), "10.0.0.1", "--host")


def test_main_refuses_the_all_interfaces_flag_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    refused = _refused_main(monkeypatch, ["--host", "0.0.0.0"])  # noqa: S104
    _assert_refusal_names(refused, "0.0.0.0", "--host")  # noqa: S104


def test_main_refuses_the_ipv6_all_interfaces_flag_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    _assert_refusal_names(_refused_main(monkeypatch, ["--host", "::"]), "::", "--host")  # noqa: S104


def test_main_refuses_a_custom_alias_env_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "myalias.internal")
    refused = _refused_main(monkeypatch, [])
    _assert_refusal_names(refused, "myalias.internal", "ALAN_HOST")


def test_main_refuses_a_non_loopback_env_host_even_with_the_removed_opt_in_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    refused = _refused_main(monkeypatch, [], removed_opt_in="container")
    _assert_refusal_names(refused, "10.20.30.40", "ALAN_HOST")

from __future__ import annotations

import logging

import pytest

from alan import detached_mode
from alan.roots import SLOT_POOL_ROOT_ENV


@pytest.mark.parametrize(
    ("dial", "pool", "expected"),
    [
        ("auto", "", False),
        ("auto", "/configured/pool", True),
        ("", "", False),
        ("", "/configured/pool", True),
        ("on", "", True),
        ("on", "/configured/pool", True),
        ("off", "", False),
        ("off", "/configured/pool", False),
    ],
)
def test_resolve_detached_mode_matrix(dial: str, pool: str, expected: bool) -> None:
    env = {detached_mode.DIAL_ENV: dial, SLOT_POOL_ROOT_ENV: pool}
    assert detached_mode.resolve_detached_mode(env) is expected


def test_resolve_detached_mode_unrecognized_falls_back_to_auto_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = {detached_mode.DIAL_ENV: "sometimes", SLOT_POOL_ROOT_ENV: "/configured/pool"}
    with caplog.at_level(logging.WARNING, logger="alan.detached_mode"):
        assert detached_mode.resolve_detached_mode(env) is True
    assert any("not recognized" in r.message for r in caplog.records)


def test_resolve_detached_mode_defaults_to_process_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(detached_mode.DIAL_ENV, "on")
    assert detached_mode.resolve_detached_mode() is True


def test_resolve_detached_mode_uppercase_and_whitespace_normalized() -> None:
    env = {detached_mode.DIAL_ENV: "  ON  "}
    assert detached_mode.resolve_detached_mode(env) is True


def test_log_resolved_mode_logs_mode_and_reason(caplog: pytest.LogCaptureFixture) -> None:
    env = {detached_mode.DIAL_ENV: "on"}
    with caplog.at_level(logging.INFO, logger="alan.detached_mode"):
        result = detached_mode.log_resolved_mode(env)
    assert result is True
    assert any("detached worker mode resolved: on" in r.message for r in caplog.records)
    assert any("explicit" in r.message for r in caplog.records)


def test_log_resolved_mode_auto_reason_names_pool_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = {SLOT_POOL_ROOT_ENV: "/configured/pool"}
    with caplog.at_level(logging.INFO, logger="alan.detached_mode"):
        result = detached_mode.log_resolved_mode(env)
    assert result is True
    assert any("auto: working-area pool configured" in r.message for r in caplog.records)


def test_log_resolved_mode_defaults_to_process_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(detached_mode.DIAL_ENV, "off")
    assert detached_mode.log_resolved_mode() is False

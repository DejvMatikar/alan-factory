from __future__ import annotations

import socket

import pytest

from alan.bind import bind_refusal, resolve_host, resolve_port

LOOPBACK_HOSTS = [
    "127.0.0.1",
    "127.0.0.2",
    "127.255.255.254",
    "::1",
    "0:0:0:0:0:0:0:1",
    "localhost",
]

REFUSED_HOSTS = [
    "0.0.0.0",  # noqa: S104
    "::",  # noqa: S104
    "10.20.30.40",
    "192.168.1.1",
    "8.8.8.8",
    "myalias.internal",
    "LOCALHOST",
    "localhost.",
    "127.1",
    "127.000.000.001",
    "::1%lo0",
    "::ffff:127.0.0.1",
    "[::1]",
]

ENV_REFUSED_HOSTS = ["10.20.30.40", "::", "myalias.internal"]  # noqa: S104

_REMOVED_OPT_IN = "ALAN_ALLOW_NON_LOOPBACK_BIND"
FORMER_OPT_IN_VALUES = ["container", "1", "true", "yes", "CONTAINER", ""]


@pytest.fixture(autouse=True)
def _removed_opt_in_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_REMOVED_OPT_IN, raising=False)


def test_resolve_host_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    assert resolve_host() == "127.0.0.1"


def test_resolve_host_uses_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "127.0.0.2")
    assert resolve_host() == "127.0.0.2"


def test_resolve_host_treats_empty_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "")
    assert resolve_host() == "127.0.0.1"


def test_resolve_host_treats_whitespace_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "   ")
    assert resolve_host() == "127.0.0.1"


def test_resolve_host_explicit_flag_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "127.0.0.2")
    assert resolve_host("::1") == "::1"


def test_resolve_host_whitespace_explicit_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    assert resolve_host("   ") == "127.0.0.1"
    assert bind_refusal("   ") is None


def test_resolve_host_empty_explicit_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    assert resolve_host("") == "127.0.0.1"
    assert bind_refusal("") is None


def test_resolve_port_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_PORT", raising=False)
    assert resolve_port() == 8585


def test_resolve_port_uses_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "9000")
    assert resolve_port() == 9000


def test_resolve_port_treats_empty_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "")
    assert resolve_port() == 8585


def test_resolve_port_treats_whitespace_env_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "   ")
    assert resolve_port() == 8585


def test_resolve_port_falls_back_on_unparseable_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "not-a-port")
    assert resolve_port() == 8585


def test_resolve_port_falls_back_below_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "0")
    assert resolve_port() == 8585


def test_resolve_port_falls_back_above_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "65536")
    assert resolve_port() == 8585


def test_resolve_port_accepts_range_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "1")
    assert resolve_port() == 1
    monkeypatch.setenv("ALAN_PORT", "65535")
    assert resolve_port() == 65535


def test_resolve_port_explicit_flag_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PORT", "9000")
    assert resolve_port(1234) == 1234


def test_resolve_port_explicit_below_range_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_PORT", "9000")
    assert resolve_port(0) == 8585


def test_resolve_port_explicit_above_range_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_PORT", "9000")
    assert resolve_port(65536) == 8585


def test_resolve_port_explicit_accepts_range_boundaries() -> None:
    assert resolve_port(1) == 1
    assert resolve_port(65535) == 65535


def test_resolve_host_surrounding_whitespace_with_content_strips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_HOST", "  127.0.0.2  ")
    assert resolve_host() == "127.0.0.2"


def test_resolve_port_surrounding_whitespace_with_content_strips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_PORT", "  9000  ")
    assert resolve_port() == 9000


@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_bind_refusal_accepts_loopback_from_the_flag(host: str) -> None:
    assert bind_refusal(host) is None


@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_bind_refusal_accepts_loopback_from_the_env(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setenv("ALAN_HOST", host)
    assert bind_refusal() is None


def test_bind_refusal_accepts_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    assert bind_refusal() is None


@pytest.mark.parametrize("host", REFUSED_HOSTS)
def test_bind_refusal_refuses_a_non_loopback_flag(host: str) -> None:
    message = bind_refusal(host)
    assert message is not None
    assert repr(host) in message
    assert "from --host" in message
    assert _REMOVED_OPT_IN not in message
    assert "opt" not in message
    assert "lifts" not in message
    assert "\n" not in message


@pytest.mark.parametrize("host", ENV_REFUSED_HOSTS)
def test_bind_refusal_refuses_a_non_loopback_env(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setenv("ALAN_HOST", host)
    message = bind_refusal()
    assert message is not None
    assert repr(host) in message
    assert "from ALAN_HOST" in message
    assert "--host" not in message


def test_bind_refusal_judges_the_flag_before_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HOST", "127.0.0.1")
    message = bind_refusal("10.0.0.1")
    assert message is not None
    assert "from --host" in message


def test_bind_refusal_accepts_a_loopback_flag_over_a_bad_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    assert bind_refusal("127.0.0.1") is None


def test_bind_refusal_strips_the_flag_before_judging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HOST", raising=False)
    assert bind_refusal("  ::1  ") is None


def test_bind_refusal_makes_no_dns_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_dns(*args: object, **kwargs: object) -> None:
        raise AssertionError("DNS lookup attempted")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)
    monkeypatch.setattr(socket, "gethostbyname", _no_dns)
    monkeypatch.setattr(socket, "gethostbyname_ex", _no_dns)
    message = bind_refusal("myalias.internal")
    assert message is not None
    assert "'myalias.internal'" in message


@pytest.mark.parametrize("value", FORMER_OPT_IN_VALUES)
def test_bind_refusal_ignores_the_removed_opt_in_for_a_flag_host(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_REMOVED_OPT_IN, value)
    message = bind_refusal("10.20.30.40")
    assert message is not None
    assert "'10.20.30.40'" in message
    assert "from --host" in message


@pytest.mark.parametrize("value", FORMER_OPT_IN_VALUES)
def test_bind_refusal_ignores_the_removed_opt_in_for_an_env_host(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_REMOVED_OPT_IN, value)
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    message = bind_refusal()
    assert message is not None
    assert "'10.20.30.40'" in message
    assert "from ALAN_HOST" in message


def test_bind_refusal_accepts_loopback_with_the_removed_opt_in_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_REMOVED_OPT_IN, "container")
    assert bind_refusal("127.0.0.1") is None


def test_resolve_host_returns_a_non_loopback_value_untouched_with_the_removed_opt_in_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_REMOVED_OPT_IN, "container")
    monkeypatch.setenv("ALAN_HOST", "10.20.30.40")
    assert resolve_host() == "10.20.30.40"
    assert resolve_host("192.168.1.1") == "192.168.1.1"

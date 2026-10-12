from __future__ import annotations

import ipaddress
import os
from typing import Literal

HostSource = Literal["--host", "ALAN_HOST", "default"]

_LOOPBACK_NAME = "localhost"
_IPV6_LOOPBACK = ipaddress.IPv6Address("::1")
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8585
_MIN_PORT = 1
_MAX_PORT = 65535


def _resolve_host_with_source(explicit: str | None) -> tuple[str, HostSource]:
    if explicit is not None:
        stripped = explicit.strip()
        if stripped:
            return stripped, "--host"
        return _DEFAULT_HOST, "default"
    raw = os.environ.get("ALAN_HOST", "").strip()
    if raw:
        return raw, "ALAN_HOST"
    return _DEFAULT_HOST, "default"


def resolve_host(explicit: str | None = None) -> str:
    return _resolve_host_with_source(explicit)[0]


def _is_loopback(host: str) -> bool:
    if host == _LOOPBACK_NAME:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return address.is_loopback
    return address == _IPV6_LOOPBACK


def bind_refusal(explicit: str | None = None) -> str | None:
    host, source = _resolve_host_with_source(explicit)
    if _is_loopback(host):
        return None
    return (
        f"refusing to bind host {host!r} from {source}: alan serves unauthenticated "
        f"read and dispatch on its port, so it only binds an address in 127.0.0.0/8, "
        f"::1, or the lowercase name localhost"
    )


def resolve_port(explicit: int | None = None) -> int:
    if explicit is not None:
        return explicit if _MIN_PORT <= explicit <= _MAX_PORT else _DEFAULT_PORT
    raw = os.environ.get("ALAN_PORT", "").strip()
    if not raw:
        return _DEFAULT_PORT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PORT
    return value if _MIN_PORT <= value <= _MAX_PORT else _DEFAULT_PORT

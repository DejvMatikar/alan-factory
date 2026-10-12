from __future__ import annotations

import dataclasses
import ipaddress
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPMessage
from typing import IO, Literal, Protocol

Outcome = Literal[
    "complete",
    "truncated",
    "empty",
    "redirect",
    "unreachable",
    "refused",
    "client_rendered",
]

_DEFAULT_CAP_BYTES = 256 * 1024
_DEFAULT_TIMEOUT_S = 10.0
_TERMINATOR = b"--SOURCE_FETCH_END--"


@dataclass(frozen=True)
class FetchResult:
    outcome: Outcome
    url: str
    status: int | None
    location: str | None
    content_type: str | None
    content_encoding: str | None
    bytes_read: int
    cap: int
    total_bytes: int
    detail: str | None


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    read: Callable[[int], bytes]


class Transport(Protocol):
    def open(self, url: str, timeout: float) -> Response: ...  # pragma: no cover


class Resolver(Protocol):
    def resolve(self, host: str) -> list[str]: ...  # pragma: no cover


def _lower_headers(msg: Message) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in msg.items()}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del newurl
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _urllib_open(url: str, timeout: float) -> Response:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Accept-Encoding": "identity"}
    )
    try:
        raw = _OPENER.open(request, timeout=timeout)  # noqa: S310  # nosec B310
    except urllib.error.HTTPError as exc:
        return Response(status=exc.code, headers=_lower_headers(exc.headers), read=exc.read)
    return Response(status=raw.status, headers=_lower_headers(raw.headers), read=raw.read)


class _UrllibTransport:
    def open(self, url: str, timeout: float) -> Response:
        return _urllib_open(url, timeout)


_URLLIB_TRANSPORT: Transport = _UrllibTransport()


class _GetAddrInfoResolver:
    def resolve(self, host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None)
        return [str(info[4][0]) for info in infos]


_GETADDRINFO_RESOLVER: Resolver = _GetAddrInfoResolver()


@dataclass(frozen=True)
class _Target:
    host: str


def _split_target(url: str) -> tuple[_Target | None, str]:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return None, "invalid-host"
    if parsed.scheme not in ("http", "https"):
        return None, "scheme"
    if not host:
        return None, "missing-host"
    return _Target(host=host), ""


def _screen_address(addrs: Sequence[str]) -> bool:
    for raw in addrs:
        ip = ipaddress.ip_address(raw)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.is_private:
            return True
    return False


def _empty_result(outcome: Outcome, url: str, detail: str, cap: int) -> FetchResult:
    return FetchResult(
        outcome=outcome,
        url=url,
        status=None,
        location=None,
        content_type=None,
        content_encoding=None,
        bytes_read=0,
        cap=cap,
        total_bytes=0,
        detail=detail,
    )


def _resolve_and_screen(
    url: str, host: str, resolver: Resolver, cap: int
) -> FetchResult | None:
    try:
        addrs = resolver.resolve(host)
    except UnicodeError:
        return _empty_result("refused", url, "invalid-host", cap)
    except OSError:
        return _empty_result("unreachable", url, "dns", cap)
    if _screen_address(addrs):
        return _empty_result("refused", url, "private-address", cap)
    return None


def _redirect_result(url: str, resp: Response, cap: int) -> FetchResult:
    return FetchResult(
        outcome="redirect",
        url=url,
        status=resp.status,
        location=resp.headers.get("location"),
        content_type=None,
        content_encoding=None,
        bytes_read=0,
        cap=cap,
        total_bytes=0,
        detail=None,
    )


def _read_capped(resp: Response, cap: int) -> tuple[bytes, int]:
    chunk = resp.read(cap + 1)
    return chunk[:cap], len(chunk)


def _body_outcome(total: int, cap: int) -> Outcome:
    if total == 0:
        return "empty"
    if total > cap:
        return "truncated"
    return "complete"


def _classify(url: str, resp: Response, body: bytes, total: int, cap: int) -> FetchResult:
    return FetchResult(
        outcome=_body_outcome(total, cap),
        url=url,
        status=resp.status,
        location=None,
        content_type=resp.headers.get("content-type"),
        content_encoding=resp.headers.get("content-encoding"),
        bytes_read=len(body),
        cap=cap,
        total_bytes=total,
        detail=None,
    )


def fetch(
    url: str,
    *,
    transport: Transport = _URLLIB_TRANSPORT,
    resolver: Resolver = _GETADDRINFO_RESOLVER,
    cap: int = _DEFAULT_CAP_BYTES,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> tuple[FetchResult, bytes]:
    target, detail = _split_target(url)
    if target is None:
        return _empty_result("refused", url, detail, cap), b""
    screened = _resolve_and_screen(url, target.host, resolver, cap)
    if screened is not None:
        return screened, b""
    try:
        resp = transport.open(url, timeout)
        if 300 <= resp.status < 400:
            return _redirect_result(url, resp, cap), b""
        body, total = _read_capped(resp, cap)
    except OSError as exc:
        return _empty_result("unreachable", url, type(exc).__name__, cap), b""
    return _classify(url, resp, body, total, cap), body


def render(result: FetchResult, body: bytes) -> bytes:
    meta = json.dumps(dataclasses.asdict(result), sort_keys=True).encode("utf-8")
    return meta + b"\n" + body + b"\n" + _TERMINATOR + b"\n"


def _main(argv: list[str]) -> int:
    if len(argv) != 1:
        return 2
    result, body = fetch(argv[0])
    sys.stdout.buffer.write(render(result, body))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))


__all__ = [
    "FetchResult",
    "Outcome",
    "Resolver",
    "Response",
    "Transport",
    "fetch",
    "render",
]

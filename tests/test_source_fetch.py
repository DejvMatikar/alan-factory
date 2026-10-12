from __future__ import annotations

import dataclasses
import io
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import get_args

import pytest

from alan import source_fetch
from alan.source_fetch import (
    _OPENER,
    _TERMINATOR,
    FetchResult,
    Outcome,
    Response,
    _GetAddrInfoResolver,
    _lower_headers,
    _main,
    _NoRedirect,
    _urllib_open,
    _UrllibTransport,
    fetch,
    render,
)

_PUBLIC_IPV4 = "8.8.8.8"


def _headers_message(fields: dict[str, str]) -> HTTPMessage:
    msg = HTTPMessage()
    for name, value in fields.items():
        msg[name] = value
    return msg


class _UnexpectedTransport:
    def open(self, url: str, timeout: float) -> Response:
        raise AssertionError("transport.open must not be called")


class _FakeTransport:
    def __init__(
        self, response: Response | None = None, exc: OSError | None = None
    ) -> None:
        self._response = response
        self._exc = exc

    def open(self, url: str, timeout: float) -> Response:
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


class _FakeResolver:
    def __init__(self, addrs: list[str]) -> None:
        self._addrs = addrs

    def resolve(self, host: str) -> list[str]:
        return self._addrs


def test_fetch_returns_complete_when_body_under_cap() -> None:
    body = b"hello world"
    resp = Response(
        status=200, headers={"content-type": "text/plain"}, read=lambda n: body[:n]
    )
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
        cap=100,
    )
    assert result.outcome == "complete"
    assert result.bytes_read == len(body)
    assert result.total_bytes == len(body)
    assert result.content_type == "text/plain"
    assert returned_body == body


def test_fetch_returns_truncated_when_body_exceeds_cap() -> None:
    body = b"x" * 20
    resp = Response(status=200, headers={}, read=lambda n: body[:n])
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
        cap=10,
    )
    assert result.outcome == "truncated"
    assert result.bytes_read == 10
    assert result.total_bytes == 11
    assert returned_body == body[:10]


def test_fetch_returns_empty_for_zero_length_body() -> None:
    resp = Response(status=200, headers={}, read=lambda n: b"")
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
        cap=100,
    )
    assert result.outcome == "empty"
    assert result.bytes_read == 0
    assert result.total_bytes == 0
    assert returned_body == b""


def test_fetch_returns_redirect_with_location_and_does_not_follow() -> None:
    def _boom(n: int) -> bytes:
        raise AssertionError("body must not be read on redirect")

    resp = Response(
        status=302, headers={"location": "http://example.com/b"}, read=_boom
    )
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
    )
    assert result.outcome == "redirect"
    assert result.location == "http://example.com/b"
    assert result.status == 302
    assert result.bytes_read == 0
    assert returned_body == b""


def test_fetch_returns_unreachable_on_connection_error() -> None:
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(exc=ConnectionRefusedError("refused")),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
    )
    assert result.outcome == "unreachable"
    assert returned_body == b""


def test_fetch_returns_unreachable_when_body_read_raises_after_open() -> None:
    def _boom(n: int) -> bytes:
        raise ConnectionResetError("connection reset by peer")

    resp = Response(status=200, headers={}, read=_boom)
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
    )
    assert result.outcome == "unreachable"
    assert result.detail == "ConnectionResetError"
    assert result.bytes_read == 0
    assert returned_body == b""


def test_fetch_refuses_url_with_unbalanced_ipv6_brackets() -> None:
    result, returned_body = fetch("http://[::1")
    assert result.outcome == "refused"
    assert result.detail == "invalid-host"
    assert returned_body == b""


class _IdnaRaisingResolver:
    def resolve(self, host: str) -> list[str]:
        raise UnicodeError("encoding with 'idna' codec failed")


def test_fetch_refuses_when_hostname_fails_idna_encoding() -> None:
    result, returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_IdnaRaisingResolver(),
    )
    assert result.outcome == "refused"
    assert result.detail == "invalid-host"
    assert returned_body == b""


def test_fetch_refuses_non_http_scheme() -> None:
    result, returned_body = fetch("file:///etc/passwd")
    assert result.outcome == "refused"
    assert result.detail == "scheme"
    assert returned_body == b""


def test_fetch_refuses_url_with_missing_host() -> None:
    result, returned_body = fetch("http:///path")
    assert result.outcome == "refused"
    assert result.detail == "missing-host"
    assert returned_body == b""


class _RaisingResolver:
    def resolve(self, host: str) -> list[str]:
        raise OSError("dns failure")


def test_fetch_returns_unreachable_when_dns_resolution_raises() -> None:
    result, returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_RaisingResolver(),
    )
    assert result.outcome == "unreachable"
    assert result.detail == "dns"
    assert returned_body == b""


def test_fetch_refuses_resolved_loopback_ipv4() -> None:
    result, _returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_FakeResolver(["127.0.0.1"]),
    )
    assert result.outcome == "refused"
    assert result.detail == "private-address"


def test_fetch_refuses_resolved_rfc1918_ipv4() -> None:
    result, _returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_FakeResolver(["10.1.2.3"]),
    )
    assert result.outcome == "refused"
    assert result.detail == "private-address"


def test_fetch_refuses_resolved_ipv6_loopback_literal() -> None:
    result, _returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_FakeResolver(["::1"]),
    )
    assert result.outcome == "refused"
    assert result.detail == "private-address"


def test_fetch_refuses_ipv4_mapped_ipv6_loopback() -> None:
    result, _returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_FakeResolver(["::ffff:127.0.0.1"]),
    )
    assert result.outcome == "refused"
    assert result.detail == "private-address"


def test_fetch_refuses_when_any_of_multiple_resolved_addresses_is_private() -> None:
    result, _returned_body = fetch(
        "http://internal.example/",
        transport=_UnexpectedTransport(),
        resolver=_FakeResolver([_PUBLIC_IPV4, "127.0.0.1"]),
    )
    assert result.outcome == "refused"
    assert result.detail == "private-address"


def test_fetch_allows_resolved_public_ipv4() -> None:
    resp = Response(status=200, headers={}, read=lambda n: b"ok")
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
    )
    assert result.outcome == "complete"
    assert returned_body == b"ok"


def test_bytes_read_and_total_bytes_equal_at_exact_cap_boundary() -> None:
    body = b"abcde"
    resp = Response(status=200, headers={}, read=lambda n: body[:n])
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
        cap=5,
    )
    assert result.outcome == "complete"
    assert result.bytes_read == 5
    assert result.total_bytes == 5
    assert returned_body == body


def test_bytes_read_capped_and_total_bytes_exceeds_at_cap_plus_one() -> None:
    body = b"abcdef"
    resp = Response(status=200, headers={}, read=lambda n: body[:n])
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
        cap=5,
    )
    assert result.outcome == "truncated"
    assert result.bytes_read == 5
    assert result.total_bytes == 6
    assert returned_body == b"abcde"


def test_fetch_result_has_no_offset_or_range_fields() -> None:
    fields = {f.name for f in dataclasses.fields(FetchResult)}
    assert "offset" not in fields
    assert "range_used" not in fields
    assert "reread" not in fields


def test_client_rendered_is_a_valid_outcome_member() -> None:
    assert "client_rendered" in get_args(Outcome)


def test_fetch_reports_content_encoding_without_decompressing() -> None:
    raw = b"\x1f\x8b\x08not-really-gzip-but-untouched"
    resp = Response(
        status=200, headers={"content-encoding": "gzip"}, read=lambda n: raw[:n]
    )
    result, returned_body = fetch(
        "http://example.com/a",
        transport=_FakeTransport(response=resp),
        resolver=_FakeResolver([_PUBLIC_IPV4]),
    )
    assert result.content_encoding == "gzip"
    assert returned_body == raw


def test_render_emits_metadata_line_then_raw_body_then_terminator() -> None:
    result = FetchResult(
        outcome="complete",
        url="http://example.com",
        status=200,
        location=None,
        content_type="text/plain",
        content_encoding=None,
        bytes_read=5,
        cap=1024,
        total_bytes=5,
        detail=None,
    )
    body = b"hello"
    rendered = render(result, body)
    meta_line, rest = rendered.split(b"\n", 1)
    parsed = json.loads(meta_line)
    assert parsed["outcome"] == "complete"
    assert parsed["bytes_read"] == 5
    assert rest == body + b"\n" + _TERMINATOR + b"\n"


def test_render_body_containing_fake_terminator_does_not_corrupt_envelope_bytes_read() -> None:
    fake_body = _TERMINATOR + b" not the real terminator"
    result = FetchResult(
        outcome="complete",
        url="http://example.com",
        status=200,
        location=None,
        content_type=None,
        content_encoding=None,
        bytes_read=len(fake_body),
        cap=1024,
        total_bytes=len(fake_body),
        detail=None,
    )
    rendered = render(result, fake_body)
    meta_line, rest = rendered.split(b"\n", 1)
    parsed = json.loads(meta_line)
    assert parsed["bytes_read"] == len(fake_body)
    assert rest == fake_body + b"\n" + _TERMINATOR + b"\n"


def test_lower_headers_normalizes_case() -> None:
    msg = _headers_message({"Content-Type": "text/html", "X-Foo": "bar"})
    assert _lower_headers(msg) == {
        "content-type": "text/html",
        "x-foo": "bar",
    }


def test_no_redirect_handler_returns_none() -> None:
    handler = _NoRedirect()
    req = urllib.request.Request("http://example.com")
    result = handler.redirect_request(
        req,
        io.BytesIO(b""),
        302,
        "Found",
        _headers_message({"Location": "http://example.com/b"}),
        "http://example.com/b",
    )
    assert result is None


class _FakeRawResponse:
    status = 200
    headers = _headers_message({"Content-Type": "text/plain"})

    def read(self, n: int) -> bytes:
        return b"ok"[:n]


def test_urllib_open_wraps_a_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _OPENER,
        "open",
        lambda request, timeout: _FakeRawResponse(),
    )
    resp = _urllib_open("http://example.com", 5.0)
    assert resp.status == 200
    assert resp.headers["content-type"] == "text/plain"
    assert resp.read(2) == b"ok"
    transport_resp = _UrllibTransport().open("http://example.com", 5.0)
    assert transport_resp.status == 200


def test_urllib_open_wraps_an_http_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = urllib.error.HTTPError(
        "http://example.com",
        404,
        "not found",
        _headers_message({"Content-Type": "text/html"}),
        None,
    )

    def _raise(request: object, timeout: float) -> None:
        raise exc

    monkeypatch.setattr(_OPENER, "open", _raise)
    resp = _urllib_open("http://example.com", 5.0)
    assert resp.status == 404
    assert resp.headers["content-type"] == "text/html"


def test_get_addr_info_resolver_extracts_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo(host: str, port: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:db8::1", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    resolver = _GetAddrInfoResolver()
    assert resolver.resolve("example.com") == ["93.184.216.34", "2001:db8::1"]


def test_main_returns_zero_for_refused_and_unreachable_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _main(["ftp://example.com/"]) == 0

    def _fake_fetch(url: str, **_kwargs: object) -> tuple[FetchResult, bytes]:
        return (
            FetchResult(
                outcome="unreachable",
                url=url,
                status=None,
                location=None,
                content_type=None,
                content_encoding=None,
                bytes_read=0,
                cap=1024,
                total_bytes=0,
                detail="dns",
            ),
            b"",
        )

    monkeypatch.setattr(source_fetch, "fetch", _fake_fetch)
    assert _main(["http://example.invalid/"]) == 0


def test_main_returns_nonzero_on_missing_url_argument() -> None:
    assert _main([]) == 2


def test_main_returns_nonzero_on_multiple_url_arguments() -> None:
    assert _main(["http://a", "http://b"]) == 2


def test_source_fetch_subprocess_offline_refused_smoke() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "PYTHONPATH")
    }
    result = subprocess.run(  # noqa: S603
        ["/usr/bin/python3", "-m", "alan.source_fetch", "file:///x"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    meta_line = result.stdout.splitlines()[0]
    parsed = json.loads(meta_line)
    assert parsed["outcome"] == "refused"
    assert parsed["detail"] == "scheme"

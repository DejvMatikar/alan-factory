from __future__ import annotations

import pytest

from alan.self_update import _ops, _soak

TARGET = "a" * 40
URL = "http://health.invalid/health"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ops, "sleep", lambda seconds: None)


def scripted_status(monkeypatch: pytest.MonkeyPatch, values: list[tuple[bool, int | None]]) -> None:
    statuses = iter(values)
    monkeypatch.setattr(_ops, "service_status", lambda: next(statuses, values[-1]))


def scripted_health(monkeypatch: pytest.MonkeyPatch, values: list[bool]) -> None:
    checks = iter(values)
    monkeypatch.setattr(_ops, "health_ok", lambda url, target: next(checks, values[-1]))


def test_a_clean_soak_runs_the_full_duration_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ops, "service_status", lambda: (True, 5))
    monkeypatch.setattr(_ops, "health_ok", lambda url, target: True)
    assert _soak.run(TARGET, URL) is True


def test_a_rising_run_count_is_a_crash_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted_status(monkeypatch, [(True, 5), (True, 6), (True, 7)])
    scripted_health(monkeypatch, [True])
    assert _soak.run(TARGET, URL) is False


def test_the_service_not_running_on_two_consecutive_polls_is_a_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted_status(monkeypatch, [(True, 5), (False, None), (False, None)])
    scripted_health(monkeypatch, [True])
    assert _soak.run(TARGET, URL) is False


def test_a_single_not_running_poll_that_recovers_is_not_a_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted_status(monkeypatch, [(True, 5), (False, 5), (True, 5)])
    scripted_health(monkeypatch, [True])
    assert _soak.run(TARGET, URL) is True


def test_a_sustained_health_failure_of_120_seconds_fails_the_soak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ops, "service_status", lambda: (True, 5))
    scripted_health(monkeypatch, [False] * 8)
    assert _soak.run(TARGET, URL) is False


def test_a_health_failure_that_recovers_before_120_seconds_does_not_fail_the_soak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ops, "service_status", lambda: (True, 5))
    pattern = ([False] * 7 + [True]) * 6
    scripted_health(monkeypatch, pattern)
    assert _soak.run(TARGET, URL) is True


def test_the_soak_constants_match_the_specified_thresholds() -> None:
    assert _soak.DURATION_S == 600.0
    assert _soak.POLL_S == 15.0
    assert _soak.SUSTAINED_FAILURE_S == 120.0


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body[:size] if size >= 0 else self._body


def test_health_ok_is_false_when_the_status_is_not_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ops, "urlopen", lambda url, timeout: _Response(500, b"{}"))
    assert _ops.health_ok(URL, TARGET) is False


def test_health_ok_is_false_for_a_body_that_is_not_valid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ops, "urlopen", lambda url, timeout: _Response(200, b"not json"))
    assert _ops.health_ok(URL, TARGET) is False


def test_health_ok_is_false_for_a_json_body_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ops, "urlopen", lambda url, timeout: _Response(200, b"[1, 2]"))
    assert _ops.health_ok(URL, TARGET) is False


def test_health_ok_is_false_for_a_url_with_no_scheme_or_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def unreachable(url: str, timeout: float) -> _Response:
        calls.append(url)
        raise AssertionError("must not be reached")

    monkeypatch.setattr(_ops, "urlopen", unreachable)
    assert _ops.health_ok("not-a-url", TARGET) is False
    assert _ops.health_ok("ftp://health.invalid/health", TARGET) is False
    assert calls == []


def test_health_ok_is_true_only_when_ok_and_served_sha_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = f'{{"ok": true, "served_sha": "{TARGET}"}}'.encode()
    monkeypatch.setattr(_ops, "urlopen", lambda url, timeout: _Response(200, body))
    assert _ops.health_ok(URL, TARGET) is True

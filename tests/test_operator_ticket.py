from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from alan import operator_ticket
from alan.clock import Epoch, Monotonic


class _Clock:
    def __init__(self) -> None:
        self.now_value = 1000.0

    def monotonic(self) -> Monotonic:
        return Monotonic(self.now_value)

    def now(self) -> Epoch:
        return Epoch(self.now_value)


@pytest.fixture(autouse=True)
def _clean_tickets() -> Iterator[None]:
    operator_ticket.clear()
    yield
    operator_ticket.clear()


def _pending() -> dict[str, Monotonic]:
    pending: dict[str, Monotonic] = operator_ticket.__dict__["_expiries"]
    return pending


def test_a_ticket_is_valid_for_thirty_seconds() -> None:
    assert operator_ticket.TICKET_TTL_S == 30.0


def test_a_fresh_ticket_is_consumed_once() -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    assert operator_ticket.consume(ticket, clock) is True
    assert operator_ticket.consume(ticket, clock) is False


def test_tickets_are_unique_and_url_safe() -> None:
    clock = _Clock()
    tickets = {operator_ticket.issue(clock) for _ in range(20)}
    assert len(tickets) == 20
    assert all(t.replace("-", "").replace("_", "").isalnum() and len(t) >= 32 for t in tickets)


def test_an_unknown_or_empty_ticket_is_refused() -> None:
    assert operator_ticket.consume("never-issued", _Clock()) is False
    assert operator_ticket.consume("", _Clock()) is False


def test_a_ticket_inside_its_window_is_accepted() -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    clock.now_value += operator_ticket.TICKET_TTL_S - 0.001
    assert operator_ticket.consume(ticket, clock) is True


@pytest.mark.parametrize("extra", [0.0, 0.001, 600.0])
def test_a_ticket_at_or_past_its_window_is_refused(extra: float) -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    clock.now_value += operator_ticket.TICKET_TTL_S + extra
    assert operator_ticket.consume(ticket, clock) is False


def test_an_expired_ticket_is_still_spent_by_the_attempt() -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    clock.now_value += 60.0
    assert operator_ticket.consume(ticket, clock) is False
    clock.now_value -= 60.0
    assert operator_ticket.consume(ticket, clock) is False


def test_issuing_purges_tickets_that_have_expired() -> None:
    clock = _Clock()
    stale = operator_ticket.issue(clock)
    clock.now_value += 31.0
    fresh = operator_ticket.issue(clock)
    assert set(_pending()) == {fresh}
    assert stale not in _pending()


def test_clear_drops_every_pending_ticket() -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    operator_ticket.clear()
    assert operator_ticket.consume(ticket, clock) is False


def test_the_default_clock_issues_and_consumes() -> None:
    ticket = operator_ticket.issue()
    assert operator_ticket.consume(ticket) is True


def test_concurrent_consumers_of_one_ticket_produce_exactly_one_winner() -> None:
    clock = _Clock()
    ticket = operator_ticket.issue(clock)
    barrier = threading.Barrier(16)
    results: list[bool] = []
    results_lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        outcome = operator_ticket.consume(ticket, clock)
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 15

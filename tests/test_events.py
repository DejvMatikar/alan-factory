from __future__ import annotations

import logging

import pytest

from alan.events import PubSub
from alan.teams._base import TeamEvent


def test_publish_delivers_to_keyed_subscriber() -> None:
    bus = PubSub()
    received: list[TeamEvent] = []
    bus.subscribe("task-a", received.append)
    bus.publish("task-a", TeamEvent("kind", {"k": "v"}))
    assert [e.kind for e in received] == ["kind"]


def test_publish_ignores_other_task_ids() -> None:
    bus = PubSub()
    received: list[TeamEvent] = []
    bus.subscribe("task-a", received.append)
    bus.publish("task-b", TeamEvent("kind", {}))
    assert received == []


def test_subscribe_all_receives_keyed_and_global_publishes() -> None:
    bus = PubSub()
    seen: list[str] = []
    unsubscribe = bus.subscribe_all(lambda e: seen.append(e.kind))
    bus.publish("task-x", TeamEvent("keyed", {"task_id": "task-x"}))
    bus.publish_global(TeamEvent("global", {}))
    assert seen == ["keyed", "global"]
    unsubscribe()
    bus.publish_global(TeamEvent("after", {}))
    assert seen == ["keyed", "global"]


def test_publish_global_skips_keyed_listeners() -> None:
    bus = PubSub()
    keyed: list[str] = []
    bus.subscribe("task-x", lambda e: keyed.append(e.kind))
    bus.publish_global(TeamEvent("global-only", {}))
    assert keyed == []


def test_publish_fanout_contains_listener_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = PubSub()
    seen: list[str] = []

    def boom(_e: TeamEvent) -> None:
        raise RuntimeError("listener exploded")

    bus.subscribe("task-a", boom)
    bus.subscribe("task-a", lambda e: seen.append(e.kind))
    with caplog.at_level(logging.ERROR, logger="alan.events"):
        bus.publish("task-a", TeamEvent("survives", {}))
    assert seen == ["survives"]
    assert any("pubsub listener failed" in r.message for r in caplog.records)


def test_global_fanout_contains_listener_exception() -> None:
    bus = PubSub()
    seen: list[str] = []

    def boom(_e: TeamEvent) -> None:
        raise RuntimeError("listener exploded")

    bus.subscribe_all(boom)
    bus.subscribe_all(lambda e: seen.append(e.kind))
    bus.publish_global(TeamEvent("survives", {}))
    assert seen == ["survives"]


def test_subscribe_returns_unsubscribe_handle() -> None:
    bus = PubSub()
    received: list[TeamEvent] = []
    unsub = bus.subscribe("task-a", received.append)
    unsub()
    bus.publish("task-a", TeamEvent("kind", {}))
    assert received == []


def test_unsubscribe_unknown_callback_is_noop() -> None:
    bus = PubSub()
    received: list[TeamEvent] = []
    unsub = bus.subscribe("task-a", received.append)
    unsub()
    unsub()


def test_unsubscribe_unknown_task_is_noop() -> None:
    bus = PubSub()
    bus._unsubscribe("ghost", lambda _e: None)  # noqa: SLF001


def test_unsubscribe_last_callback_removes_task_key() -> None:
    bus = PubSub()
    unsub = bus.subscribe("ephemeral", lambda _e: None)
    assert "ephemeral" in bus._listeners  # noqa: SLF001
    unsub()
    assert "ephemeral" not in bus._listeners  # noqa: SLF001


def test_unsubscribe_one_of_many_keeps_others() -> None:
    bus = PubSub()
    received_a: list[TeamEvent] = []
    received_b: list[TeamEvent] = []
    unsub_a = bus.subscribe("shared", received_a.append)
    bus.subscribe("shared", received_b.append)
    unsub_a()
    bus.publish("shared", TeamEvent("kind", {}))
    assert received_a == []
    assert len(received_b) == 1
    assert "shared" in bus._listeners  # noqa: SLF001


def test_unsubscribe_all_unknown_callback_is_noop() -> None:
    bus = PubSub()
    bus._unsubscribe_all(lambda _e: None)  # noqa: SLF001
    bus.subscribe_all(lambda _e: None)
    bus._unsubscribe_all(lambda _e: None)  # noqa: SLF001


def test_multiple_subscribers_for_same_task() -> None:
    bus = PubSub()
    received_a: list[TeamEvent] = []
    received_b: list[TeamEvent] = []
    bus.subscribe("multi", received_a.append)
    bus.subscribe("multi", received_b.append)
    bus.publish("multi", TeamEvent("kind", {}))
    assert len(received_a) == 1
    assert len(received_b) == 1

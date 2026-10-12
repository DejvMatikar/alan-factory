from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from alan.teams._base import TeamEvent

logger = logging.getLogger("alan.events")


def _fanout(listeners: list[Callable[[TeamEvent], None]], event: TeamEvent) -> None:
    for cb in listeners:
        try:
            cb(event)
        except Exception:  # noqa: BLE001 - subscriber faults must not abort fanout
            logger.exception("pubsub listener failed for kind=%s", event.kind)


class PubSub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[str, list[Callable[[TeamEvent], None]]] = {}
        self._all_listeners: list[Callable[[TeamEvent], None]] = []

    def subscribe(
        self, task_id: str, callback: Callable[[TeamEvent], None]
    ) -> Callable[[], None]:
        with self._lock:
            self._listeners.setdefault(task_id, []).append(callback)
        return lambda: self._unsubscribe(task_id, callback)

    def subscribe_all(self, callback: Callable[[TeamEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._all_listeners.append(callback)
        return lambda: self._unsubscribe_all(callback)

    def publish(self, task_id: str, event: TeamEvent) -> None:
        with self._lock:
            listeners = [*self._listeners.get(task_id, ()), *self._all_listeners]
        _fanout(listeners, event)

    def publish_global(self, event: TeamEvent) -> None:
        with self._lock:
            listeners = list(self._all_listeners)
        _fanout(listeners, event)

    def _unsubscribe(
        self, task_id: str, callback: Callable[[TeamEvent], None]
    ) -> None:
        with self._lock:
            existing = self._listeners.get(task_id)
            if existing is None:
                return
            remaining = [cb for cb in existing if cb is not callback]
            if remaining:
                self._listeners[task_id] = remaining
            else:
                del self._listeners[task_id]

    def _unsubscribe_all(self, callback: Callable[[TeamEvent], None]) -> None:
        with self._lock:
            self._all_listeners = [
                cb for cb in self._all_listeners if cb is not callback
            ]

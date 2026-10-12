from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import Queue, QueueError


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def test_delete_meta_missing_key_is_a_noop(queue: Queue) -> None:
    queue.delete_meta("does-not-exist")


def test_delete_meta_removes_an_existing_key(queue: Queue) -> None:
    queue.set_meta("some-key", "some-value")
    queue.delete_meta("some-key")
    assert queue.get_meta("some-key") is None


def test_delete_meta_leaves_other_keys_untouched(queue: Queue) -> None:
    queue.set_meta("keep-me", "still-here")
    queue.set_meta("drop-me", "gone")
    queue.delete_meta("drop-me")
    assert queue.get_meta("keep-me") == "still-here"


def test_delete_meta_after_close_raises(queue: Queue) -> None:
    queue.close()
    with pytest.raises(QueueError):
        queue.delete_meta("some-key")

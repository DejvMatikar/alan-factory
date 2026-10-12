from __future__ import annotations

from pathlib import Path

from alan.clock import SYSTEM_CLOCK
from alan.queue import Queue
from tests.fake_clock import FakeClock


def test_queue_defaults_to_system_clock(tmp_path: Path) -> None:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        assert q._clock is SYSTEM_CLOCK  # noqa: SLF001
    finally:
        q.close()


def test_claim_binds_lease_epoch_from_injected_clock(tmp_path: Path) -> None:
    fake = FakeClock(epoch=1_000.0)
    q = Queue(db_path=tmp_path / "queue.db", clock=fake)
    try:
        job = q.enqueue("t", "research", eligible_at=1_500.0)
        assert q.claim() is None
        fake.advance(600.0)
        claimed = q.claim()
        assert claimed is not None
        assert claimed.id == job.id
    finally:
        q.close()


def test_paused_until_uses_injected_clock(tmp_path: Path) -> None:
    fake = FakeClock(epoch=1_000.0)
    q = Queue(db_path=tmp_path / "queue.db", clock=fake)
    try:
        q.pause_until(1_500.0)
        assert q.paused_until() == 1_500.0
        fake.advance(600.0)
        assert q.paused_until() is None
    finally:
        q.close()

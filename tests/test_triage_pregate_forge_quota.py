from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.queue import FORGE_DEGRADED_META_KEY, Queue
from alan.teams import _forge_quota
from alan.teams.triage_pregate import gather_forge_degraded


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


def _sample(remaining: int | None, limit: int | None) -> _forge_quota.QuotaSample:
    return _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=limit, cost=1, remaining=remaining, reset_at=None, outcome="ok",
    )


def test_gather_forge_degraded_past_reset_with_low_headroom_surfaces_unblocked(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_at = datetime.now(UTC).timestamp() - 5.0
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "old"}))
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(50, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == "forge_low_headroom"
    assert surface.obs == {"remaining": 50, "limit": 5000}
    assert surface.resolvable is True


def test_gather_forge_degraded_past_reset_with_healthy_headroom_returns_empty(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_at = datetime.now(UTC).timestamp() - 5.0
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "old"}))
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(4000, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == ""
    assert surface.resolvable is False


def test_gather_forge_degraded_past_reset_with_no_sample_returns_empty(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_at = datetime.now(UTC).timestamp() - 5.0
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "old"}))
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: None)
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == ""


def test_gather_forge_degraded_actively_paused_ignores_headroom(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_at = datetime.now(UTC).timestamp() + 600.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "rate limit"}),
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(10, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is True
    assert surface.blocker_reason == "forge_rate_limited"


def test_gather_forge_degraded_no_meta_with_low_headroom_surfaces_unblocked(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(1, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == "forge_low_headroom"
    assert surface.obs == {"remaining": 1, "limit": 5000}
    assert surface.resolvable is True


def test_gather_forge_degraded_no_meta_with_healthy_headroom_returns_empty(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(4000, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == ""
    assert surface.resolvable is False


def test_gather_forge_degraded_corrupt_meta_with_low_headroom_surfaces_unblocked(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, "not-json{{{")
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(1, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == "forge_low_headroom"
    assert surface.resolvable is True


@pytest.mark.parametrize("detail", [None, 123, ["x"], {"a": 1}])
def test_gather_forge_degraded_non_text_detail_with_future_reset_stays_blocked(
    queue: Queue, detail: object,
) -> None:
    reset_at = datetime.now(UTC).timestamp() + 600.0
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": detail}))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is True
    assert surface.blocker_reason == "forge_rate_limited"
    assert surface.obs == {"reset_at": reset_at, "detail": str(detail)}


def test_gather_forge_degraded_missing_detail_with_future_reset_reports_empty_detail(
    queue: Queue,
) -> None:
    reset_at = datetime.now(UTC).timestamp() + 600.0
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at}))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is True
    assert surface.obs == {"reset_at": reset_at, "detail": ""}


@pytest.mark.parametrize("stored", ["[1, 2]", "null", "42", '"text"', "{}", '{"reset_at": "soon"}'])
def test_gather_forge_degraded_unusable_meta_falls_back_to_the_headroom_surface(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, stored: str,
) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, stored)
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(1, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == "forge_low_headroom"
    assert surface.resolvable is True


def test_gather_forge_degraded_corrupt_meta_with_healthy_headroom_returns_empty(
    queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, "not-json{{{")
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: _sample(4000, 5000))
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked is False
    assert surface.blocker_reason == ""
    assert surface.resolvable is False

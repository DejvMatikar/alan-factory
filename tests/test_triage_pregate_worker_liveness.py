from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import worker_liveness
from alan.dispatcher import Dispatcher
from alan.health import compute_health_state
from alan.queue import FORGE_DEGRADED_META_KEY, WORKER_STATE_META_KEY, Job, JobStatus, Queue
from alan.teams.triage_pregate import (
    _STALE_JOB_THRESHOLD_S,
    ClassifiedSurface,
    gather_forge_degraded,
    gather_stale_jobs,
    gather_worker_pool,
)


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


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _liveness_payload(
    *, alive: int, configured: int, observed_at: float | None = None
) -> str:
    ts = observed_at if observed_at is not None else datetime.now(UTC).timestamp()
    return worker_liveness.encode(
        worker_liveness.DispatchLoopReading(
            live_loops=alive, configured_loops=configured, observed_at=ts
        )
    )


def test_gather_worker_pool_no_meta_returns_healthy(queue: Queue) -> None:
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked
    assert surface.kind == "worker_pool"


def test_gather_worker_pool_all_alive_returns_healthy(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=3, configured=3))
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_deficit_returns_blocked(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=2, configured=4))
    surface = gather_worker_pool(queue)
    assert surface.is_blocked
    assert surface.kind == "worker_pool"
    assert surface.obs["alive"] == 2
    assert surface.obs["configured"] == 4


def test_gather_worker_pool_all_dead_returns_blocked(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=0, configured=2))
    surface = gather_worker_pool(queue)
    assert surface.is_blocked


def test_gather_worker_pool_corrupt_meta_returns_healthy(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, "not-json{{{")
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_non_dict_meta_returns_healthy(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, json.dumps([1, 2, 3]))
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_infinite_observed_at_returns_healthy(queue: Queue) -> None:
    queue.set_meta(
        WORKER_STATE_META_KEY,
        json.dumps({"live_loops": 0, "configured_loops": 2, "observed_at": float("inf")}),
    )
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked
    assert surface.obs == {}


def test_gather_worker_pool_v1_blob_returns_healthy(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, json.dumps({"alive": 0, "configured": 2}))
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_blocker_reason_includes_deficit_count(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=1, configured=4))
    surface = gather_worker_pool(queue)
    assert "3" in surface.blocker_reason


def test_gather_worker_pool_stale_reading_returns_healthy(queue: Queue) -> None:
    stale_ts = datetime.now(UTC).timestamp() - worker_liveness.HEARTBEAT_TTL_S - 1.0
    queue.set_meta(
        WORKER_STATE_META_KEY,
        _liveness_payload(alive=0, configured=2, observed_at=stale_ts),
    )
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_future_dated_reading_returns_healthy(queue: Queue) -> None:
    future_ts = datetime.now(UTC).timestamp() + 3600.0
    queue.set_meta(
        WORKER_STATE_META_KEY,
        _liveness_payload(alive=0, configured=2, observed_at=future_ts),
    )
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_deficit_silenced_by_running_job(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=0, configured=2))
    queue.enqueue("ws-running", "research")
    queue.claim()
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_deficit_silenced_by_detached_survivor(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=0, configured=2))
    monkeypatch.setattr(worker_liveness, "probe_active_workers", lambda: 1)
    surface = gather_worker_pool(queue)
    assert not surface.is_blocked


def test_gather_worker_pool_observed_at_present_in_obs(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=2, configured=2))
    surface = gather_worker_pool(queue)
    assert surface.obs["observed_at"] is not None


def test_gather_worker_pool_obs_empty_when_meta_absent(queue: Queue) -> None:
    surface = gather_worker_pool(queue)
    assert surface.obs == {}


def test_worker_pool_deficit_produces_degraded_verdict(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=0, configured=2))
    surface = gather_worker_pool(queue)
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    assert len(state.findings) == 1
    finding = state.findings[0]
    assert finding.kind == "worker_pool"
    assert finding.severity == "critical"


def test_worker_pool_healthy_produces_healthy_verdict(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=2, configured=2))
    surface = gather_worker_pool(queue)
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"


def test_worker_pool_description_shows_alive_and_configured(queue: Queue) -> None:
    queue.set_meta(WORKER_STATE_META_KEY, _liveness_payload(alive=1, configured=4))
    surface = gather_worker_pool(queue)
    state = compute_health_state([surface], datetime.now(UTC))
    desc = state.findings[0].description
    assert "1" in desc
    assert "4" in desc


def test_gather_stale_jobs_empty_queue_returns_empty(queue: Queue) -> None:
    assert gather_stale_jobs(queue) == []


def test_gather_stale_jobs_fresh_running_job_not_surfaced(queue: Queue) -> None:
    queue.enqueue("ws-fresh", "research")
    queue.claim()
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S)
    assert surfaces == []


def test_gather_stale_jobs_old_job_surfaced(queue: Queue) -> None:
    queue.enqueue("ws-stale", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    assert len(surfaces) == 1
    assert surfaces[0].kind == "stale_job"
    assert surfaces[0].is_blocked


def test_gather_stale_jobs_workspace_id_in_obs(queue: Queue) -> None:
    queue.enqueue("ws-drill", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    assert len(surfaces) == 1
    assert surfaces[0].obs["workspace_id"] == "ws-drill"


def test_gather_stale_jobs_threshold_zero_flags_running(queue: Queue) -> None:
    queue.enqueue("ws-a", "research")
    queue.enqueue("ws-b", "research")
    queue.claim()
    queue.claim()
    future = datetime.now(UTC) + timedelta(seconds=1)
    surfaces = gather_stale_jobs(queue, threshold_s=0.0, _now=future)
    assert len(surfaces) == 2


def test_gather_stale_jobs_surface_id_unique_per_job(queue: Queue) -> None:
    queue.enqueue("ws-x", "research")
    queue.enqueue("ws-y", "research")
    queue.claim()
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    ids = {s.surface_id for s in surfaces}
    assert len(ids) == len(surfaces)


def test_gather_stale_jobs_elapsed_seconds_in_obs(queue: Queue) -> None:
    queue.enqueue("ws-elapsed", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=4)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    assert len(surfaces) == 1
    elapsed = surfaces[0].obs["elapsed_seconds"]
    assert elapsed >= int(timedelta(hours=4).total_seconds()) - 5


def _make_job(started_at: datetime | None) -> Job:
    return Job(
        id=1,
        task_id="t1",
        workspace_id="ws-mock",
        team="research",
        status=JobStatus.RUNNING,
        enqueued_at=datetime.now(UTC),
        started_at=started_at,
    )


def test_gather_stale_jobs_skips_job_with_none_started_at(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "list_running", lambda: [_make_job(None)])
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    assert surfaces == []


def test_gather_stale_jobs_handles_naive_started_at(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
    monkeypatch.setattr(queue, "list_running", lambda: [_make_job(naive)])
    future = datetime.now(UTC) + timedelta(hours=1)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    assert len(surfaces) == 1
    assert surfaces[0].kind == "stale_job"


def test_gather_stale_jobs_handles_naive_now(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = datetime.now(UTC) - timedelta(hours=3)
    monkeypatch.setattr(queue, "list_running", lambda: [_make_job(started)])
    naive_now = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=naive_now)
    assert len(surfaces) == 1
    assert surfaces[0].kind == "stale_job"


def test_stale_job_produces_degraded_verdict(queue: Queue) -> None:
    queue.enqueue("ws-stale-h", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.verdict == "degraded"
    assert state.findings[0].kind == "stale_job"


def test_stale_job_finding_has_workspace_drill_down(queue: Queue) -> None:
    queue.enqueue("ws-link", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.findings[0].workspace_id == "ws-link"


def test_stale_job_finding_severity_is_high(queue: Queue) -> None:
    queue.enqueue("ws-sev", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert state.findings[0].severity == "high"


def test_stale_job_description_includes_workspace_id(queue: Queue) -> None:
    queue.enqueue("ws-desc", "research")
    queue.claim()
    future = datetime.now(UTC) + timedelta(hours=3)
    surfaces = gather_stale_jobs(queue, threshold_s=_STALE_JOB_THRESHOLD_S, _now=future)
    state = compute_health_state(surfaces, datetime.now(UTC))
    assert "ws-desc" in state.findings[0].description


def test_dispatcher_publishes_worker_state_after_start(
    queue: Queue, tmp_path: Path
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=2, poll_interval=0.01)
    d.start()
    try:
        reading = worker_liveness.parse(queue.get_meta(WORKER_STATE_META_KEY))
        assert reading is not None
        assert reading.live_loops == 2
        assert reading.configured_loops == 2
    finally:
        d.stop(timeout=3.0)


def test_dispatcher_deletes_worker_state_on_clean_stop(
    queue: Queue, tmp_path: Path
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=2, poll_interval=0.01)
    d.start()
    d.stop(timeout=3.0)
    assert queue.get_meta(WORKER_STATE_META_KEY) is None


def test_dispatcher_publishes_deficit_reading_on_unexpected_worker_exit(
    queue: Queue, tmp_path: Path
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=2, poll_interval=0.01)
    d.start()
    try:
        d._on_worker_exit()  # noqa: SLF001
        reading = worker_liveness.parse(queue.get_meta(WORKER_STATE_META_KEY))
        assert reading is not None
        assert reading.live_loops == 1
        assert reading.configured_loops == 2
    finally:
        d.stop(timeout=3.0)


def test_dispatcher_worker_state_configured_count_correct(
    queue: Queue, tmp_path: Path
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=3, poll_interval=0.01)
    d.start()
    try:
        reading = worker_liveness.parse(queue.get_meta(WORKER_STATE_META_KEY))
        assert reading is not None
        assert reading.configured_loops == 3
        assert reading.live_loops == 3
    finally:
        d.stop(timeout=3.0)


def test_publish_worker_state_tolerates_queue_error(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_on_set(key: str, value: str) -> None:  # noqa: ARG001
        raise RuntimeError("db full")

    monkeypatch.setattr(queue, "set_meta", raise_on_set)
    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=1, poll_interval=0.01)
    d._publish_worker_state()  # noqa: SLF001


def test_dispatcher_publish_not_called_when_start_raises(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    def record_set(key: str, value: str) -> None:
        if key == WORKER_STATE_META_KEY:
            published.append(value)

    monkeypatch.setattr(queue, "set_meta", record_set)

    def fail_start(self: threading.Thread) -> None:
        raise RuntimeError("OS denied")

    monkeypatch.setattr(threading.Thread, "start", fail_start)

    d = Dispatcher(queue, {}, workspaces_root=tmp_path / "ws", concurrency=1)
    with pytest.raises(RuntimeError, match="OS denied"):
        d.start()

    assert published == []


def test_worker_state_meta_key_constant_value() -> None:
    assert WORKER_STATE_META_KEY == "dispatcher_worker_state_v2"


def test_stale_job_threshold_constant_is_two_hours() -> None:
    assert _STALE_JOB_THRESHOLD_S == pytest.approx(7200.0)


def test_gather_forge_degraded_no_meta_returns_healthy(queue: Queue) -> None:
    surface = gather_forge_degraded(queue)
    assert not surface.is_blocked
    assert surface.kind == "forge_degraded"


def test_gather_forge_degraded_future_reset_returns_blocked(queue: Queue) -> None:
    reset_at = datetime.now(UTC).timestamp() + 600.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY,
        json.dumps({"reset_at": reset_at, "detail": "API rate limit already exceeded"}),
    )
    surface = gather_forge_degraded(queue)
    assert surface.is_blocked
    assert surface.blocker_reason == "forge_rate_limited"
    assert surface.obs["reset_at"] == pytest.approx(reset_at)
    assert surface.obs["detail"] == "API rate limit already exceeded"


def test_gather_forge_degraded_past_reset_returns_healthy(queue: Queue) -> None:
    reset_at = datetime.now(UTC).timestamp() - 5.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "old"})
    )
    surface = gather_forge_degraded(queue)
    assert not surface.is_blocked


def test_gather_forge_degraded_corrupt_meta_returns_healthy(queue: Queue) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, "not-json{{{")
    surface = gather_forge_degraded(queue)
    assert not surface.is_blocked


def test_gather_forge_degraded_missing_reset_key_returns_healthy(queue: Queue) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, json.dumps({"detail": "no reset"}))
    surface = gather_forge_degraded(queue)
    assert not surface.is_blocked


def test_gather_forge_degraded_non_numeric_reset_returns_healthy(queue: Queue) -> None:
    queue.set_meta(
        FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": "soonish", "detail": ""})
    )
    surface = gather_forge_degraded(queue)
    assert not surface.is_blocked


def test_gather_forge_degraded_explicit_now_boundary(queue: Queue) -> None:
    now = datetime.now(UTC)
    queue.set_meta(
        FORGE_DEGRADED_META_KEY,
        json.dumps({"reset_at": now.timestamp(), "detail": ""}),
    )
    surface = gather_forge_degraded(queue, _now=now)
    assert not surface.is_blocked


def test_forge_degraded_produces_high_finding_with_resume_time(queue: Queue) -> None:
    reset_at = datetime.now(UTC).timestamp() + 600.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY,
        json.dumps({"reset_at": reset_at, "detail": "rate limit"}),
    )
    surface = gather_forge_degraded(queue)
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "degraded"
    finding = state.findings[0]
    assert finding.kind == "forge_degraded"
    assert finding.severity == "high"
    assert finding.action == "deep_link"
    assert "GitHub API degraded" in finding.description
    expected_hhmm = datetime.fromtimestamp(reset_at, tz=UTC).strftime("%H:%M UTC")
    assert expected_hhmm in finding.description


def test_forge_degraded_description_without_reset_at_omits_time() -> None:
    surface = ClassifiedSurface(
        "forge_degraded", "forge_degraded", True, "forge_rate_limited", {}
    )
    state = compute_health_state([surface], datetime.now(UTC))
    desc = state.findings[0].description
    assert desc == "GitHub API degraded (rate limited) — dispatch paused"


def test_forge_degraded_healthy_surface_produces_healthy_verdict(queue: Queue) -> None:
    surface = gather_forge_degraded(queue)
    state = compute_health_state([surface], datetime.now(UTC))
    assert state.verdict == "healthy"


def test_forge_degraded_description_survives_out_of_range_reset_at() -> None:
    surface = ClassifiedSurface(
        "forge_degraded", "forge_degraded", True, "forge_rate_limited",
        {"reset_at": 1e300},
    )
    state = compute_health_state([surface], datetime.now(UTC))
    desc = state.findings[0].description
    assert desc == "GitHub API degraded (rate limited) — dispatch paused"

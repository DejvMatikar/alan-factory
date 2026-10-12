from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import deploy_gate_sweep, deploy_observe, deploy_signal
from alan.deploy_verify import DeployObservation, GateRecord
from alan.queue import Queue


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    yield q
    q.close()


def _enqueue_ceiling_job(
    queue: Queue,
    *,
    merge_sha: str | None = "a" * 40,
    eligible_at: float | None = None,
) -> int:
    when = eligible_at if eligible_at is not None else time.time() + 600.0
    record = GateRecord(merge_sha=merge_sha, path="ceiling", deferred_at=time.time())
    job = queue.enqueue("t", "deploy_verify", eligible_at=when, instructions=record.to_json())
    return job.id


def test_promotes_ceiling_job_when_signal_is_served(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    job_id = _enqueue_ceiling_job(queue)
    promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 1
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at <= time.time() + 1.0
    record = GateRecord.from_json(updated.instructions)
    assert record is not None
    assert record.path == "signal"


def test_does_not_promote_when_signal_not_served(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: False)
    job_id = _enqueue_ceiling_job(queue)
    promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 0
    updated = queue.get_job(job_id)
    assert updated is not None
    record = GateRecord.from_json(updated.instructions)
    assert record is not None
    assert record.path == "ceiling"


def test_skips_non_deploy_verify_team(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    record = GateRecord(merge_sha="a" * 40, path="ceiling", deferred_at=time.time())
    queue.enqueue("t", "pr_merge", eligible_at=time.time() + 600.0, instructions=record.to_json())
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_skips_job_with_no_eligible_at(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    record = GateRecord(merge_sha="a" * 40, path="ceiling", deferred_at=time.time())
    queue.enqueue("t", "deploy_verify", instructions=record.to_json())
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_skips_unparseable_instructions(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    queue.enqueue("t", "deploy_verify", eligible_at=time.time() + 600.0, instructions="not json")
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_skips_non_ceiling_path(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    record = GateRecord(merge_sha="a" * 40, path="restart_free", deferred_at=None)
    queue.enqueue(
        "t", "deploy_verify", eligible_at=time.time() + 600.0, instructions=record.to_json()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_skips_when_merge_sha_missing(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    _enqueue_ceiling_job(queue, merge_sha=None)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_promote_job_returns_false_when_eligible_at_already_past(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    job_id = _enqueue_ceiling_job(queue, eligible_at=time.time() - 5.0)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0
    updated = queue.get_job(job_id)
    assert updated is not None
    record = GateRecord.from_json(updated.instructions)
    assert record is not None
    assert record.path == "ceiling"


def test_swallows_exception_and_returns_zero(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _enqueue_ceiling_job(queue)

    def _boom(sha: str) -> bool:
        raise RuntimeError("gh unreachable")

    monkeypatch.setattr(deploy_signal, "merge_is_served", _boom)
    with caplog.at_level("WARNING"):
        promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 0
    assert f"job={job_id}" in caplog.text
    assert "RuntimeError" in caplog.text


def test_ignores_other_pending_jobs_alongside_a_ceiling_job(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    queue.enqueue("other", "implementation")
    job_id = _enqueue_ceiling_job(queue)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 1
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None


def test_awaiting_restart_job_is_promoted_like_ceiling(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    record = GateRecord(
        merge_sha="a" * 40,
        path="awaiting_restart",
        deferred_at=time.time(),
        deadline=time.time() + 5400.0,
        race_deadline=None,
    )
    job = queue.enqueue(
        "t", "deploy_verify", eligible_at=time.time() + 5400.0, instructions=record.to_json()
    )
    promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 1
    updated = queue.get_job(job.id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at <= time.time() + 1.0
    updated_record = GateRecord.from_json(updated.instructions)
    assert updated_record is not None
    assert updated_record.path == "signal"
    assert updated_record.deadline == record.deadline
    assert updated_record.race_deadline == record.race_deadline


def test_promotes_all_jobs_covered_by_one_deploy(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    covered = {"a" * 40, "b" * 40}
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: sha in covered)
    job_ids = [_enqueue_ceiling_job(queue, merge_sha=sha) for sha in covered]
    assert deploy_gate_sweep.promote_covered_jobs(queue) == len(covered)
    for job_id in job_ids:
        updated = queue.get_job(job_id)
        assert updated is not None
        assert updated.eligible_at is not None
        assert updated.eligible_at <= time.time() + 1.0
        record = GateRecord.from_json(updated.instructions)
        assert record is not None
        assert record.path == "signal"


def _enqueue_awaiting_live_job(
    queue: Queue,
    *,
    merge_sha: str | None = "a" * 40,
    eligible_at: float | None = None,
    repo: str | None = None,
    merged_at: float | None = None,
) -> int:
    when = eligible_at if eligible_at is not None else time.time() + 5400.0
    record = GateRecord(
        merge_sha=merge_sha,
        path="awaiting_live",
        deferred_at=time.time(),
        deadline=time.time() + 5400.0,
        race_deadline=None,
        merged_at=merged_at,
        repo=repo,
    )
    job = queue.enqueue("t", "deploy_verify", eligible_at=when, instructions=record.to_json())
    return job.id


def _live_observation() -> DeployObservation:
    return DeployObservation(
        arm="restart_free",
        checkout_contains_merge=True,
        slot_contains_merge=True,
        served_contains_merge=False,
        health_ok=True,
    )


def _not_live_observation() -> DeployObservation:
    return DeployObservation(
        arm="restart_free",
        checkout_contains_merge=False,
        slot_contains_merge=False,
        served_contains_merge=False,
        health_ok=True,
    )


def test_promotes_awaiting_live_job_when_locally_observed_live(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _enqueue_awaiting_live_job(queue)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _live_observation()
    )
    promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 1
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at <= time.time() + 1.0


def test_does_not_promote_awaiting_live_job_when_not_locally_observed_live(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _enqueue_awaiting_live_job(queue)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _not_live_observation()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at > time.time()


def test_awaiting_live_job_with_no_merged_at_still_promotes(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _enqueue_awaiting_live_job(queue, merged_at=None)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _live_observation()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 1
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at <= time.time() + 1.0


def test_skips_awaiting_live_job_with_no_merge_sha(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enqueue_awaiting_live_job(queue, merge_sha=None)

    def _fail_if_called(sha: str, *, health_url: str | None) -> DeployObservation:
        raise AssertionError("observe must not be called with no merge_sha")

    monkeypatch.setattr(deploy_observe, "observe", _fail_if_called)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_ceiling_job_and_awaiting_live_job_both_promote_in_one_sweep(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_signal, "merge_is_served", lambda sha: True)
    ceiling_job_id = _enqueue_ceiling_job(queue, merge_sha="d" * 40)
    live_job_id = _enqueue_awaiting_live_job(queue, merge_sha="e" * 40)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _live_observation()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 2
    for job_id in (ceiling_job_id, live_job_id):
        updated = queue.get_job(job_id)
        assert updated is not None
        assert updated.eligible_at is not None
        assert updated.eligible_at <= time.time() + 1.0


def test_awaiting_live_job_not_promoted_when_eligible_at_already_past(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _enqueue_awaiting_live_job(queue, eligible_at=time.time() - 5.0)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _live_observation()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0
    updated = queue.get_job(job_id)
    assert updated is not None
    record = GateRecord.from_json(updated.instructions)
    assert record is not None
    assert record.path == "awaiting_live"


def test_swallows_observe_exception_and_returns_zero(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _enqueue_awaiting_live_job(queue)

    def _boom(sha: str, *, health_url: str | None) -> DeployObservation:
        raise RuntimeError("git unreachable")

    monkeypatch.setattr(deploy_observe, "observe", _boom)
    with caplog.at_level("WARNING"):
        promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 0
    assert f"job={job_id}" in caplog.text
    assert "RuntimeError" in caplog.text


def test_one_jobs_observe_failure_does_not_block_another_jobs_promotion(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    good_sha = "a" * 40
    bad_sha = "b" * 40
    good_job_id = _enqueue_awaiting_live_job(queue, merge_sha=good_sha)
    bad_job_id = _enqueue_awaiting_live_job(queue, merge_sha=bad_sha)

    def _observe(sha: str, *, health_url: str | None) -> DeployObservation:
        if sha == bad_sha:
            raise RuntimeError("git unreachable")
        return _live_observation()

    monkeypatch.setattr(deploy_observe, "observe", _observe)
    with caplog.at_level("WARNING"):
        promoted = deploy_gate_sweep.promote_covered_jobs(queue)
    assert promoted == 1

    good_updated = queue.get_job(good_job_id)
    assert good_updated is not None
    assert good_updated.eligible_at is not None
    assert good_updated.eligible_at <= time.time() + 1.0

    bad_updated = queue.get_job(bad_job_id)
    assert bad_updated is not None
    assert bad_updated.eligible_at is not None
    assert bad_updated.eligible_at > time.time()


def test_awaiting_live_job_for_non_default_repo_is_not_promoted(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enqueue_awaiting_live_job(queue, repo="DejvMatikar/example-repo")

    def _fail_if_called(sha: str, *, health_url: str | None) -> DeployObservation:
        raise AssertionError("observe must not be called for a non-default repo")

    monkeypatch.setattr(deploy_observe, "observe", _fail_if_called)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 0


def test_awaiting_live_job_with_no_repo_falls_back_to_default_and_is_promoted(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _enqueue_awaiting_live_job(queue, repo=None)
    monkeypatch.setattr(
        deploy_observe, "observe", lambda sha, *, health_url: _live_observation()
    )
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 1
    updated = queue.get_job(job_id)
    assert updated is not None
    assert updated.eligible_at is not None
    assert updated.eligible_at <= time.time() + 1.0


def test_awaiting_live_job_for_default_repo_promotes_while_other_repos_job_is_skipped(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    alan_sha = "a" * 40
    other_sha = "b" * 40
    alan_job_id = _enqueue_awaiting_live_job(
        queue, merge_sha=alan_sha, repo="DejvMatikar/alan"
    )
    other_job_id = _enqueue_awaiting_live_job(
        queue, merge_sha=other_sha, repo="DejvMatikar/example-repo"
    )

    def _observe(sha: str, *, health_url: str | None) -> DeployObservation:
        assert sha == alan_sha
        return _live_observation()

    monkeypatch.setattr(deploy_observe, "observe", _observe)
    assert deploy_gate_sweep.promote_covered_jobs(queue) == 1

    alan_updated = queue.get_job(alan_job_id)
    assert alan_updated is not None
    assert alan_updated.eligible_at is not None
    assert alan_updated.eligible_at <= time.time() + 1.0

    other_updated = queue.get_job(other_job_id)
    assert other_updated is not None
    assert other_updated.eligible_at is not None
    assert other_updated.eligible_at > time.time()

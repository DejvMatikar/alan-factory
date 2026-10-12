from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from alan.queue import Job, JobStatus, Queue, QueueError
from tests.fake_clock import FakeClock

_WORKSPACES = ("ws-a", "ws-b")
_CAP = 2

_EXPECTED_MAX_EXAMPLES = 30
_EXPECTED_STATEFUL_STEP_COUNT = 15

_STATE_MACHINE_SETTINGS = settings(
    settings.get_profile("alan"), max_examples=150, stateful_step_count=30
)


class QueueStateMachine(RuleBasedStateMachine):
    jobs: Bundle[int] = Bundle("jobs")

    def __init__(self) -> None:
        super().__init__()
        self.queue = Queue(db_path=Path(":memory:"), intra_workspace_cap=_CAP, clock=FakeClock())
        self._task_counter = 0
        self._model_status: dict[int, JobStatus] = {}
        self._model_workspace: dict[int, str] = {}
        self._deleted: set[int] = set()
        self._currently_running: set[int] = set()
        self._exclusivity_violations: list[str] = []

    def teardown(self) -> None:
        self.queue.close()

    @rule(target=jobs, workspace_id=st.sampled_from(_WORKSPACES))
    def enqueue(self, workspace_id: str) -> int:
        self._task_counter += 1
        task_id = f"{workspace_id}-task-{self._task_counter}"
        job: Job = self.queue.enqueue(task_id, "implementation", workspace_id=workspace_id)
        self._model_status[job.id] = JobStatus.PENDING
        self._model_workspace[job.id] = workspace_id
        return job.id

    @rule()
    def claim(self) -> None:
        job = self.queue.claim()
        if job is None:
            return
        prior_status = self._model_status.get(job.id)
        if prior_status is not JobStatus.PENDING:
            self._exclusivity_violations.append(
                f"claim() returned job {job.id} whose model status was "
                f"{prior_status!r}, not pending"
            )
        self._model_status[job.id] = JobStatus.RUNNING
        self._currently_running.add(job.id)

    @rule(job_id=jobs)
    def complete(self, job_id: int) -> None:
        if job_id in self._deleted or self._model_status.get(job_id) is not JobStatus.RUNNING:
            with pytest.raises(QueueError):
                self.queue.complete(job_id)
            return
        result = self.queue.complete(job_id)
        assert result.status == JobStatus.COMPLETED
        self._model_status[job_id] = JobStatus.COMPLETED
        self._currently_running.discard(job_id)

    @rule(job_id=jobs, error=st.text(max_size=20))
    def fail(self, job_id: int, error: str) -> None:
        if job_id in self._deleted or self._model_status.get(job_id) is not JobStatus.RUNNING:
            with pytest.raises(QueueError):
                self.queue.fail(job_id, error)
            return
        result = self.queue.fail(job_id, error)
        assert result.status == JobStatus.FAILED
        self._model_status[job_id] = JobStatus.FAILED
        self._currently_running.discard(job_id)

    @rule(job_id=jobs)
    def requeue_pending(self, job_id: int) -> None:
        if job_id in self._deleted or self._model_status.get(job_id) is not JobStatus.RUNNING:
            with pytest.raises(QueueError):
                self.queue.requeue_pending(job_id)
            return
        result = self.queue.requeue_pending(job_id)
        assert result.status == JobStatus.PENDING
        self._model_status[job_id] = JobStatus.PENDING
        self._currently_running.discard(job_id)

    @rule(job_id=jobs, resume_inv_id=st.sampled_from(["inv-a", "inv-b"]))
    def requeue_for_resume(self, job_id: int, resume_inv_id: str) -> None:
        if job_id in self._deleted or self._model_status.get(job_id) is not JobStatus.RUNNING:
            with pytest.raises(QueueError):
                self.queue.requeue_for_resume(job_id, resume_inv_id)
            return
        result = self.queue.requeue_for_resume(job_id, resume_inv_id)
        assert result.status == JobStatus.PENDING
        self._model_status[job_id] = JobStatus.PENDING
        self._currently_running.discard(job_id)

    @rule(workspace_id=st.sampled_from(_WORKSPACES))
    def delete_jobs_by_workspace(self, workspace_id: str) -> None:
        self.queue.delete_jobs_by_workspace(workspace_id)
        for job_id, ws in self._model_workspace.items():
            if ws == workspace_id:
                self._deleted.add(job_id)
                self._currently_running.discard(job_id)

    @invariant()
    def no_lost_jobs(self) -> None:
        for job_id, expected_status in self._model_status.items():
            if job_id in self._deleted:
                continue
            job = self.queue.get_job(job_id)
            assert job is not None, f"job {job_id} vanished from the queue"
            assert job.status == expected_status, (
                f"job {job_id}: queue status {job.status!r} != model status "
                f"{expected_status!r}"
            )

    @invariant()
    def capacity_bounds(self) -> None:
        cap = self.queue._intra_workspace_cap  # pyright: ignore[reportPrivateUsage]
        for workspace_id in _WORKSPACES:
            running = self.queue.list_jobs_for_workspace(workspace_id, status=JobStatus.RUNNING)
            assert len(running) <= cap, (
                f"workspace {workspace_id} had {len(running)} running jobs, "
                f"exceeding cap {cap}"
            )

    @invariant()
    def claim_exclusivity(self) -> None:
        assert not self._exclusivity_violations, self._exclusivity_violations


def test_queue_invariants_under_generated_operation_sequences() -> None:
    run_state_machine_as_test(QueueStateMachine, settings=_STATE_MACHINE_SETTINGS)


def test_in_memory_queues_are_isolated_per_instance() -> None:
    q1 = Queue(db_path=Path(":memory:"))
    q2 = Queue(db_path=Path(":memory:"))
    try:
        job = q1.enqueue("task-iso", "implementation")
        assert q2.get_job(job.id) is None
        assert q2.list_jobs() == []
    finally:
        q1.close()
        q2.close()


def test_ci_profile_is_derandomized_and_time_bounded() -> None:
    profile = settings.default
    assert profile is not None
    assert profile.derandomize is True
    assert profile.database is None
    assert profile.deadline is None
    assert profile.max_examples == _EXPECTED_MAX_EXAMPLES
    assert profile.stateful_step_count == _EXPECTED_STATEFUL_STEP_COUNT

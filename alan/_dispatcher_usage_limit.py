from __future__ import annotations

import json
import logging
import os
import random
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import alan._dispatcher_base as _base
from alan import store, workflow_interrupt
from alan import workflow as workflow_mod
from alan.claude_cli import SpendCapReached, UsageLimitError
from alan.queue import (
    FORGE_DEGRADED_META_KEY,
    ForgeDegradedMarker,
    Job,
    JobStatus,
    QueueError,
    parse_forge_degraded,
)
from alan.teams import _forge_errors, _forge_quota
from alan.teams._base import Team
from alan.workspace import Workspace

logger = logging.getLogger("alan.dispatcher")

_SERVER_ERROR_BASE_DELAY_ENV = "ALAN_SERVER_ERROR_BASE_DELAY_S"
_SERVER_ERROR_DELAY_CAP_ENV = "ALAN_SERVER_ERROR_DELAY_CAP_S"
_SERVER_ERROR_MAX_RETRIES = 3
_DEFAULT_SERVER_ERROR_BASE_DELAY = 120.0
_DEFAULT_SERVER_ERROR_DELAY_CAP = 600.0
_USAGE_LIMIT_PAUSE_ERROR = "usage limit reached"
_FORGE_RATE_LIMIT_PAUSE_ERROR = "github rate limited"
_STRANDED_ERROR = "interrupted job could not be requeued"


@dataclass(frozen=True)
class _Recovery:
    resume_inv_id: str | None = None
    eligible_at: float | None = None
    server_error_retry_count: int = 0


def _positive_float_env(env: str, default: float) -> float:
    return store.positive_float_env(os.environ.get(env), default)


def _known_headroom(
    sample: _forge_quota.QuotaSample | None,
) -> tuple[int | None, int | None]:
    if sample is None or sample.remaining is None or sample.limit is None:
        return None, None
    return sample.remaining, sample.limit


def _window_already_covers(raw: str | None, reset_at: float) -> bool:
    existing = parse_forge_degraded(raw)
    if existing is None:
        if raw is not None:
            logger.warning("overwriting malformed %s", FORGE_DEGRADED_META_KEY)
        return False
    return existing.reset_at >= reset_at


class UsageLimitMixin(_base.DispatcherState):
    def _requeue_after_interrupt(
        self,
        job: Job,
        ws: Workspace,
        requeue: Callable[[], object],
        recover: "_Recovery",
        *,
        label: str,
    ) -> bool:
        try:
            requeue()
        except QueueError:
            return self._recover_stranded(job, ws, recover, label=label)
        return True

    def _recover_stranded(
        self, job: Job, ws: Workspace, recover: "_Recovery", *, label: str
    ) -> bool:
        current = self._queue.get_job(job.id)
        status = current.status if current is not None else None
        if status is JobStatus.PENDING:
            logger.info("%s requeue skipped for job %d: already pending", label, job.id)
            return True
        if status is JobStatus.COMPLETED:
            logger.info("%s requeue skipped for job %d: already completed", label, job.id)
            return False
        if status is JobStatus.FAILED and self._requeue_failed(job, recover, label=label):
            return True
        logger.warning("%s requeue failed for job %d", label, job.id)
        self._fail_workspace(ws, _STRANDED_ERROR)
        self._publish(job, "job_stranded", {"reason": label})
        return False

    def _requeue_failed(self, job: Job, recover: "_Recovery", *, label: str) -> bool:
        try:
            self._queue.requeue_failed_with_resume(
                job.id,
                recover.resume_inv_id,
                eligible_at=recover.eligible_at,
                server_error_retry_count=recover.server_error_retry_count,
            )
        except QueueError:
            logger.exception("%s failed-requeue lost job %d", label, job.id)
            return False
        logger.info("%s recovered stranded job %d", label, job.id)
        return True

    def _requeue_for_pause(
        self, job: Job, ws: Workspace, team: Team, inv_id: str, *, label: str
    ) -> bool:
        resume_inv_id = inv_id if team.supports_resume else None
        if resume_inv_id is not None:
            requeue = partial(self._queue.requeue_for_resume, job.id, resume_inv_id)
        else:
            workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)
            requeue = partial(self._queue.requeue_pending, job.id)
        return self._requeue_after_interrupt(
            job, ws, requeue, _Recovery(resume_inv_id=resume_inv_id), label=label
        )

    def _handle_server_error(
        self, job: Job, ws: Workspace, exc: Exception, inv_id: str, team: Team
    ) -> None:
        if job.server_error_retry_count >= _SERVER_ERROR_MAX_RETRIES:
            self._fail_genuine(job, ws, exc, inv_id, team)
            return
        workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)
        attempt = job.server_error_retry_count
        rng = random.SystemRandom()
        base = _positive_float_env(
            _SERVER_ERROR_BASE_DELAY_ENV, _DEFAULT_SERVER_ERROR_BASE_DELAY
        )
        cap = _positive_float_env(
            _SERVER_ERROR_DELAY_CAP_ENV, _DEFAULT_SERVER_ERROR_DELAY_CAP
        )
        raw = base * (2 ** attempt)
        jitter = rng.uniform(0, raw * 0.25)
        eligible_at = self._clock.now().value + min(cap, raw + jitter)
        requeued = self._requeue_after_interrupt(
            job,
            ws,
            partial(self._queue.requeue_with_server_error_backoff, job.id, eligible_at),
            _Recovery(eligible_at=eligible_at, server_error_retry_count=attempt + 1),
            label="server_error",
        )
        if not requeued:
            return
        self._publish(
            job,
            "job_retry_scheduled",
            {"attempt": str(attempt + 1), "eligible_at": str(eligible_at)},
        )

    def _write_forge_degraded_meta(self, exc: UsageLimitError) -> None:
        # Guarded read-compare-write, mirroring pause_until's monotonic-max:
        # a slower concurrent handler carrying an earlier reset_at must never
        # shorten the recorded window while the queue pause still holds.
        if _window_already_covers(
            self._queue.get_meta(FORGE_DEGRADED_META_KEY), exc.reset_at
        ):
            return
        remaining, limit = _known_headroom(
            _forge_quota.last_sample(_forge_quota.PR_FEEDBACK_CALL_SITE)
        )
        marker = ForgeDegradedMarker(
            reset_at=exc.reset_at,
            detail=exc.raw_message[:200],
            remaining=remaining,
            limit=limit,
        )
        self._queue.set_meta(
            FORGE_DEGRADED_META_KEY, json.dumps(marker.model_dump(exclude_none=True))
        )

    def _handle_usage_limit(
        self, job: Job, ws: Workspace, team: Team, exc: UsageLimitError, inv_id: str
    ) -> None:
        pause_error = _USAGE_LIMIT_PAUSE_ERROR
        if isinstance(exc, _forge_errors.ForgeRateLimited):
            pause_error = _FORGE_RATE_LIMIT_PAUSE_ERROR
            self._write_forge_degraded_meta(exc)
        if isinstance(exc, SpendCapReached):
            self._queue.stop_until_resume(exc.regain_at)
        else:
            self._queue.pause_until(exc.reset_at)
        try:
            workflow_interrupt.interrupt_workflow_on_disk(
                ws,
                inv_id,
                error=pause_error,
                resumable=team.supports_resume,
                recover_finalized=team.supports_resume,
            )
        except OSError:
            logger.exception("interrupt_workflow_on_disk failed for job %d", job.id)
        if not self._requeue_for_pause(job, ws, team, inv_id, label="usage_limit"):
            return
        mode = "resume" if team.supports_resume else "pending"
        self._publish(job, "queue_paused", {"reset_at": str(exc.reset_at), "mode": mode})

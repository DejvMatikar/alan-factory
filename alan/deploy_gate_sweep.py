from __future__ import annotations

import logging
import time
from dataclasses import replace

from alan import deploy_observe, deploy_signal
from alan import engine_config as _engine_config_mod
from alan.deploy_verify import GateRecord, is_live
from alan.queue import Job, JobStatus, Queue

logger = logging.getLogger(__name__)

_TEAM = "deploy_verify"


def promote_covered_jobs(queue: Queue) -> int:
    try:
        return _promote_covered_jobs(queue)
    except Exception as exc:  # noqa: BLE001 - sweep must never break process startup
        logger.warning("deploy_gate_sweep failed: %s", exc, exc_info=True)
        return 0


def _promote_covered_jobs(queue: Queue) -> int:
    promoted = 0
    for job in queue.list_jobs(status=JobStatus.PENDING):
        promoted += _handle_pending_job(queue, job)
    return promoted


def _handle_pending_job(queue: Queue, job: Job) -> int:
    ceiling_pair = _ceiling_record_for(job)
    if ceiling_pair is not None:
        return 1 if _promote_job(queue, job, *ceiling_pair) else 0
    live_pair = _awaiting_live_record_for(job)
    if live_pair is not None:
        return 1 if _promote_awaiting_live_job(queue, job, *live_pair) else 0
    return 0


def _ceiling_record_for(job: Job) -> tuple[GateRecord, str] | None:
    if job.team != _TEAM or job.eligible_at is None:
        return None
    record = GateRecord.from_json(job.instructions)
    if (
        record is None
        or record.path not in ("ceiling", "awaiting_restart")
        or record.merge_sha is None
    ):
        return None
    return record, record.merge_sha


def _promote_job(queue: Queue, job: Job, record: GateRecord, merge_sha: str) -> bool:
    try:
        if not deploy_signal.merge_is_served(merge_sha):
            return False
        updated = queue.advance_eligible_at(
            job.id, time.time(), instructions=replace(record, path="signal").to_json()
        )
    except Exception as exc:  # noqa: BLE001 - wrap with job identity for the outer catch
        raise RuntimeError(
            f"job={job.id} merge_sha={merge_sha} ({type(exc).__name__}: {exc})"
        ) from exc
    return updated is not None


def _default_repo_slug() -> str:
    cfg = _engine_config_mod.default_engine_config()
    return f"{cfg.repo.owner}/{cfg.repo.repo}"


def _awaiting_live_record_for(job: Job) -> tuple[GateRecord, str] | None:
    if job.team != _TEAM or job.eligible_at is None:
        return None
    record = GateRecord.from_json(job.instructions)
    if record is None or record.path != "awaiting_live" or record.merge_sha is None:
        return None
    return record, record.merge_sha


def _promote_awaiting_live_job(
    queue: Queue, job: Job, record: GateRecord, merge_sha: str
) -> bool:
    repo = record.repo or _default_repo_slug()
    if repo != _default_repo_slug():
        return False
    try:
        observed_live = is_live(deploy_observe.observe(merge_sha, health_url=None))
    except Exception as exc:  # noqa: BLE001 - one job's local-probe failure must not block others
        logger.warning(
            "deploy_gate_sweep job=%s merge_sha=%s local observation failed: %s",
            job.id, merge_sha, exc, exc_info=True,
        )
        return False
    if not observed_live:
        return False
    updated = queue.advance_eligible_at(job.id, time.time(), instructions=job.instructions)
    return updated is not None


__all__ = ["promote_covered_jobs"]

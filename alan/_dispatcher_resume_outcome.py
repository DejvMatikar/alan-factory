from __future__ import annotations

from alan.queue import Job
from alan.roots import logs_root
from alan.teams._resume_audit import record_resume_outcome

_RESUME_AUDIT_FILE = "resume_audit.jsonl"


def record_terminal_resume_outcome(job: Job, *, succeeded: bool) -> None:
    if not job.resume_inv_id:
        return
    record_resume_outcome(
        logs_root() / _RESUME_AUDIT_FILE,
        task_id=job.task_id,
        team=job.team,
        inv_id=job.resume_inv_id,
        outcome="resume_succeeded" if succeeded else "resume_failed",
        extra={"job_id": job.id},
    )


__all__ = ["record_terminal_resume_outcome"]

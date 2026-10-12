from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from alan import worker_records
from alan.queue import Queue
from alan.slot_pool import Reservation

logger = logging.getLogger("alan.worker_reaper")


def _remove_best_effort(session_id: str) -> None:
    try:
        worker_records.remove_record(session_id)
    except OSError:
        logger.warning("worker reaper: failed to remove record %s", session_id, exc_info=True)


def _reserve_or_drop(
    reserved: dict[Path, Reservation],
    dropped: list[str],
    record: worker_records.WorkerRecord,
    *,
    adoptable: bool,
) -> None:
    if record.slot_path:
        reserved[Path(record.slot_path)] = Reservation(record.session_id, adoptable)
    else:
        dropped.append(record.session_id)


def _reap_one(
    path: Path,
    record: worker_records.WorkerRecord | None,
    *,
    this_service: tuple[int, str],
    reserved: dict[Path, Reservation],
    dropped: list[str],
) -> None:
    if record is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("worker reaper: failed to remove record %s", path, exc_info=True)
        return
    verdict = worker_records.probe(record, this_service=this_service)
    if worker_records.is_detached_survivor(record, verdict):
        _reserve_or_drop(reserved, dropped, record, adoptable=True)
        return
    if worker_records.is_terminable(verdict):
        worker_records.terminate_pid(cast(int, record.pid))
        _remove_best_effort(record.session_id)
        return
    if worker_records.is_reservable(verdict):
        _reserve_or_drop(reserved, dropped, record, adoptable=record.pgid is not None)
        return
    _remove_best_effort(record.session_id)


def _remove_stale_hold(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("worker reaper: failed to remove slot hold %s", path, exc_info=True)


def _reserve_fresh_holds(reserved: dict[Path, Reservation], *, now: float) -> int:
    reserved_count = 0
    for path, hold in worker_records.load_all_slot_holds():
        if hold is None or not worker_records.is_slot_hold_fresh(hold, now=now):
            _remove_stale_hold(path)
            continue
        slot_path = Path(hold.slot_path)
        if slot_path in reserved:
            continue
        reserved[slot_path] = Reservation(f"hold:{hold.workspace_id}", adoptable=False)
        reserved_count += 1
    return reserved_count


def _list_running_ids_best_effort(queue: Queue) -> set[int]:
    try:
        return {job.id for job in queue.list_running()}
    except Exception:  # noqa: BLE001 - this read is logging-only; boot_clean_allowed is the actual gate
        logger.warning(
            "worker reaper: queue.list_running() failed while assembling the boot log",
            exc_info=True,
        )
        return set()


def reap_and_reserve(queue: Queue) -> Mapping[Path, Reservation]:
    this_service = worker_records.this_service_identity()
    reserved: dict[Path, Reservation] = {}
    dropped: list[str] = []
    loaded = 0
    for path, record in worker_records.load_all():
        loaded += 1
        _reap_one(path, record, this_service=this_service, reserved=reserved, dropped=dropped)
    record_based_reservations = len(reserved)
    hold_based_reservations = _reserve_fresh_holds(reserved, now=time.time())
    running_job_ids = _list_running_ids_best_effort(queue)
    logger.info(
        "worker reaper: %d record(s) loaded, %d reservation(s) made, %d dropped for empty "
        "slot_path, %d slot hold(s) reserved, %d job(s) RUNNING in queue",
        loaded, record_based_reservations, len(dropped), hold_based_reservations,
        len(running_job_ids),
    )
    if dropped:
        logger.warning(
            "worker reaper: %d record(s) dropped for empty slot_path: %s",
            len(dropped), ", ".join(dropped),
        )
    return reserved


def boot_clean_allowed(queue: Queue) -> bool:
    try:
        running = queue.list_running()
    except Exception:  # noqa: BLE001 - a failed read is contradictory evidence, treated as held
        logger.warning(
            "worker reaper: queue.list_running() failed at boot; refusing startup clean",
            exc_info=True,
        )
        return False
    if running:
        logger.warning(
            "worker reaper: %d job(s) RUNNING in queue at boot (job id(s): %s); refusing "
            "startup clean — a running job may hold a clone with no record to prove it",
            len(running),
            ", ".join(str(job.id) for job in running),
        )
        return False
    logger.info("worker reaper: no RUNNING jobs in queue at boot; startup clean permitted")
    return True


def _is_local_survivor(record: worker_records.WorkerRecord) -> bool:
    # Mirrors probe()'s foreign-host check: a foreign-host record's pgid
    # names nothing on this machine, so signalling it here — on the
    # ops-rollback route — could hit an unrelated live group that happens to
    # reuse the same pgid.
    return record.pgid is not None and record.host == worker_records.current_host()


def _terminate_detached_record(record: worker_records.WorkerRecord) -> None:
    if record.pid is not None:
        try:
            worker_records.guard_no_group_signal(record.pid)
        except worker_records.GroupSignalGuardError:
            logger.warning(
                "detached mode off: refusing to signal own process group for pid=%d",
                record.pid,
            )
            return
    logger.info(
        "detached mode off: terminating surviving worker group pgid=%d session=%s",
        record.pgid, record.session_id,
    )
    worker_records.terminate_group(cast(int, record.pgid))
    _remove_best_effort(record.session_id)


def terminate_all_detached() -> None:
    for _path, record in worker_records.load_all():
        if record is None or not _is_local_survivor(record):
            continue
        _terminate_detached_record(record)


__all__ = ["boot_clean_allowed", "reap_and_reserve", "terminate_all_detached"]

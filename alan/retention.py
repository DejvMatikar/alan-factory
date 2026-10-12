from __future__ import annotations

import gzip
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import alan.workspace as workspace_mod
from alan.queue import Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Status, Workspace

logger = logging.getLogger("alan.retention")

_DAYS_ENV = "ALAN_AUTO_ARCHIVE_DAYS"
_DRY_RUN_ENV = "ALAN_AUTO_ARCHIVE_DRY_RUN"
_AUDIT_FILE = "retention_audit.jsonl"
_INVOCATIONS_DIR = "invocations"
_STREAM_SUFFIX = ".stream.jsonl"
_GZ_SUFFIX = ".gz"
_GZ_TMP_SUFFIX = ".gz.tmp"
_MAX_COMPRESSIONS_PER_PASS = 200
_ARCHIVE_FILE_MODE = 0o600


def _logs_dir() -> Path:
    return logs_root()


def resolve_min_age_days(*, log_invalid: bool = True) -> int | None:
    raw = os.environ.get(_DAYS_ENV, "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        if log_invalid:
            logger.warning("retention: invalid %s=%r; auto-archive disabled", _DAYS_ENV, raw)
        return None
    return days if days > 0 else None


_FALSY_VALUES = frozenset({"", "0", "false", "no", "off"})


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() not in _FALSY_VALUES


def is_archivable_status(status: Status) -> bool:
    return status == Status.COMPLETED


def _is_stale_completed(ws: Workspace, cutoff: datetime) -> bool:
    return is_archivable_status(ws.meta.status) and ws.meta.updated_at <= cutoff


def _audit_row(ws: Workspace, min_age_days: int, dry_run: bool, moment: datetime) -> None:
    append_jsonl_audit(
        _logs_dir() / _AUDIT_FILE,
        {
            "task_id": ws.task_id,
            "updated_at": ws.meta.updated_at.isoformat(),
            "min_age_days": min_age_days,
            "dry_run": dry_run,
            "archived_at": moment.isoformat(),
        },
        log_label="retention",
    )


def _archive_one(ws: Workspace, dry_run: bool) -> bool:
    try:
        if not dry_run:
            workspace_mod.transition(ws, Status.ARCHIVED)
    except Exception:  # noqa: BLE001 - one bad workspace must not abort the pass
        logger.exception("retention: failed to archive %s", ws.task_id)
        return False
    return True


def _stale_candidates(
    queue: Queue, workspaces_root: Path | None, cutoff: datetime
) -> list[Workspace]:
    return [
        ws
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if _is_stale_completed(ws, cutoff) and not queue.has_active_work(ws.task_id)
    ]


def archive_stale_completed(
    *, queue: Queue, workspaces_root: Path | None, now: datetime | None = None
) -> list[str]:
    min_age_days = resolve_min_age_days()
    if min_age_days is None:
        return []
    moment = now if now is not None else datetime.now(UTC)
    dry_run = dry_run_enabled()
    archived: list[str] = []
    for ws in _stale_candidates(queue, workspaces_root, moment - timedelta(days=min_age_days)):
        if not _archive_one(ws, dry_run):
            continue
        archived.append(ws.task_id)
        _audit_row(ws, min_age_days, dry_run, moment)
    if archived:
        logger.info(
            "retention: %s %d stale completed workspace(s): %s",
            "would archive" if dry_run else "archived",
            len(archived),
            ", ".join(archived),
        )
    compress_archived_streams(workspaces_root=workspaces_root, now=moment)
    return archived


def _compress_one(path: Path) -> bool:
    tmp = path.with_name(path.name + _GZ_TMP_SUFFIX)
    try:
        with path.open("rb") as src, gzip.open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.chmod(_ARCHIVE_FILE_MODE)
        tmp.replace(path.with_name(path.name + _GZ_SUFFIX))
        path.unlink()
    except OSError:
        logger.exception("retention: failed to compress %s", path)
        tmp.unlink(missing_ok=True)
        return False
    return True


def _workspace_stream_files(ws: Workspace) -> list[Path]:
    inv_root = ws.path / _INVOCATIONS_DIR
    if not inv_root.is_dir():
        return []
    return sorted(p for p in inv_root.rglob(f"*{_STREAM_SUFFIX}") if p.is_file())


def _audit_compressed(ws: Workspace, count: int, moment: datetime) -> None:
    append_jsonl_audit(
        _logs_dir() / _AUDIT_FILE,
        {
            "task_id": ws.task_id,
            "compressed_streams": count,
            "compressed_at": moment.isoformat(),
        },
        log_label="retention",
    )


def _compress_workspace(ws: Workspace, budget: int, moment: datetime) -> int:
    done = 0
    for path in _workspace_stream_files(ws):
        if done >= budget:
            break
        if _compress_one(path):
            done += 1
    if done:
        _audit_compressed(ws, done, moment)
    return done


def _compression_enabled() -> bool:
    return resolve_min_age_days() is not None and not dry_run_enabled()


def compress_archived_streams(
    *, workspaces_root: Path | None, now: datetime | None = None
) -> int:
    if not _compression_enabled():
        return 0
    moment = now if now is not None else datetime.now(UTC)
    total = 0
    for ws in workspace_mod.list_workspaces(root=workspaces_root):
        if ws.meta.status != Status.ARCHIVED:
            continue
        total += _compress_workspace(ws, _MAX_COMPRESSIONS_PER_PASS - total, moment)
        if total >= _MAX_COMPRESSIONS_PER_PASS:
            break
    if total:
        logger.info("retention: compressed %d archived stream file(s)", total)
    return total

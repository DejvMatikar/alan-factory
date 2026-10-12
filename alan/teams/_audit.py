from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_AUDIT_FILE_MODE = 0o600


def append_jsonl_audit(
    path: Path, entry: dict[str, object], *, log_label: str
) -> None:
    """Append one JSON line to `path` with owner-only permissions.

    Every audit-writer in `alan/teams/` shares the same `os.open` +
    `os.fdopen` + write dance with the same error-swallowing semantics:
    log on failure, never raise (audit is best-effort). This helper
    centralises that pattern so a copy-paste regression can't slip in.

    `log_label` is interpolated into the warning prefix, e.g.
    `"impl"` → "impl audit write failed: ...".
    """
    write_failed = f"{log_label} audit write failed: %s"
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("%s audit dir mkdir failed: %s", log_label, exc)
        return
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _AUDIT_FILE_MODE
        )
    except OSError as exc:
        logger.warning(write_failed, exc)
        return
    try:
        fh = os.fdopen(fd, "a", encoding="utf-8")
    except OSError as exc:
        os.close(fd)
        logger.warning(write_failed, exc)
        return
    try:
        with fh:
            payload = {"timestamp_utc": datetime.now(UTC).isoformat(), **entry}
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(write_failed, exc)


__all__ = ["append_jsonl_audit"]

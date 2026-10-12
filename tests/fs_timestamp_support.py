from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alan.workspace import Workspace

_ONE_SECOND_NS = 1_000_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def bump_mtime(path: Path) -> Path:
    stamp_ns = path.stat().st_mtime_ns + _ONE_SECOND_NS
    os.utime(path, ns=(stamp_ns, stamp_ns))
    return path


def write_summary_sidecar(ws: Workspace, payload: str) -> None:
    sidecar = ws.path / "manager_summary.json"
    sidecar.write_text(payload, encoding="utf-8")
    updated_ns = (ws.meta.updated_at - _EPOCH) // timedelta(microseconds=1) * 1_000
    stamp_ns = updated_ns + _ONE_SECOND_NS
    os.utime(sidecar, ns=(stamp_ns, stamp_ns))

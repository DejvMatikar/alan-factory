from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

import alan.lane_limit as lane_limit_mod
import alan.workspace as workspace_mod
from alan.queue import Queue


async def create_within_lane_limit(
    queue: Queue,
    *,
    root: Path,
    engine_key: str | None,
    create: Callable[[], workspace_mod.Workspace],
) -> workspace_mod.Workspace:
    try:
        return await run_in_threadpool(
            lane_limit_mod.create_within_limit,
            queue,
            root=root,
            engine_key=engine_key,
            create=create,
        )
    except lane_limit_mod.LaneFullError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

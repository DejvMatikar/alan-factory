from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.teams.brief_refine as brief_refine_mod
import alan.workspace as workspace_mod
from alan._chat_core import ChatSession

logger = logging.getLogger("alan.server")


def _resolve_chat_loader(
    kind: prep_chat_mod.PrepChatKind, build_root: Path, explore_root: Path
) -> tuple[Callable[..., ChatSession], Path]:
    if kind is prep_chat_mod.PrepChatKind.BUILD:
        return build_chat_mod.load_session, build_root
    return explore_chat_mod.load_session, explore_root


async def write_brief_refine_source(
    prep_chat_kind: prep_chat_mod.PrepChatKind | None,
    prep_chat_session_id: str | None,
    ws: workspace_mod.Workspace,
    build_root: Path,
    explore_root: Path,
) -> None:
    if prep_chat_kind is None or prep_chat_session_id is None:
        return
    loader, root = _resolve_chat_loader(prep_chat_kind, build_root, explore_root)
    try:
        session = await run_in_threadpool(loader, prep_chat_session_id, root=root)
        text = "\n\n".join(m.content for m in session.transcript if m.role == "user")
        if text.strip():
            await run_in_threadpool(
                (ws.path / brief_refine_mod.SOURCE_FILE_NAME).write_text,
                text,
                encoding="utf-8",
            )
    except Exception:  # noqa: BLE001
        safe_session_id = prep_chat_session_id.replace("\r", "").replace("\n", "")
        logger.warning(
            "create_workspace: brief_refine source read failed for session %s", safe_session_id
        )


__all__ = ["write_brief_refine_source"]

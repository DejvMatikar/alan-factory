from __future__ import annotations

from fastapi.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from alan.workspace_index import WorkspaceIndex

ARCHIVED_REDIRECT_STATUS = 303

ARCHIVED_REDIRECT_RESPONSE: dict[int | str, dict[str, str]] = {
    ARCHIVED_REDIRECT_STATUS: {
        "description": "session archived; redirects to its workspace prep-chat transcript"
    },
}


async def archived_prep_chat_redirect(
    session_id: str, index: WorkspaceIndex
) -> Response | None:
    task_id = await run_in_threadpool(index.resolve_archived_prep_chat, session_id)
    if task_id is None:
        return None
    return RedirectResponse(
        url=f"/api/workspaces/{task_id}/prep-chat", status_code=ARCHIVED_REDIRECT_STATUS
    )

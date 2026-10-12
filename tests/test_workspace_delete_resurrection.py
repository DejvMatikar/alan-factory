from __future__ import annotations

import threading
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan._dispatcher_retire import retire_workspace
from alan.dispatcher import Dispatcher
from alan.queue import Queue
from alan.workspace import Status


def test_workflow_write_after_delete_does_not_resurrect_meta_json(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    workspace_mod.delete(ws.task_id, root=ws_root)
    assert workspace_mod.exists(ws.task_id, root=ws_root) is False

    with pytest.raises(workflow_mod.WorkflowError):
        workflow_mod.save_node_config(
            ws, "inv_001", "implementer", system_prompt="s", user_text="u",
            allowed_tools=[], max_turns=1, permission_mode="auto", model="sonnet",
            turns_source="static", derived_max_turns=1,
        )
    assert not ws.path.exists()

    with pytest.raises(OSError):
        workspace_mod.transition(ws, Status.AWAITING_INPUT)

    assert workspace_mod.exists(ws.task_id, root=ws_root) is False


def test_racing_writer_thread_cannot_resurrect_a_deleted_workspace(
    ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    start = threading.Event()
    stop = threading.Event()

    def straggler() -> None:
        start.wait()
        while not stop.is_set():
            try:
                workflow_mod.create_invocation_dir(ws)
            except workflow_mod.WorkflowError:
                pass

    worker = threading.Thread(target=straggler, daemon=True)
    worker.start()
    start.set()
    result = retire_workspace(d, ws.task_id, queue, ws_root, sleep=lambda _s: None)
    stop.set()
    worker.join(timeout=5.0)

    assert result.deleted is not workspace_mod.exists(ws.task_id, root=ws_root)

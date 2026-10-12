from __future__ import annotations

import inspect

import pytest

from alan.teams import TEAMS


@pytest.mark.parametrize("team_name", list(TEAMS))
def test_run_signature_has_task_id(team_name: str) -> None:
    team = TEAMS[team_name]
    sig = inspect.signature(type(team).run)
    assert "task_id" in sig.parameters, (
        f"{type(team).__name__}.run() is missing task_id kwarg"
    )
    param = sig.parameters["task_id"]
    assert param.default is None, (
        f"{type(team).__name__}.run() task_id default must be None"
    )
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"{type(team).__name__}.run() task_id must be keyword-only"
    )

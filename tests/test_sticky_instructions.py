from __future__ import annotations

from pathlib import Path

import pytest

from alan import sticky_instructions
from alan import workspace as workspace_mod
from alan.workspace import Workspace, create

_TRUNCATION_MARKER = "(truncated — full text in the workspace UI)"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return create("sticky-task", "d", root=tmp_path / "ws")


def test_resolve_returns_none_when_no_sticky_instruction(ws: Workspace) -> None:
    assert sticky_instructions.resolve(ws, ws.task_id) is None


def test_resolve_returns_the_persisted_sticky_instruction(ws: Workspace) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "do the thing", origin="dispatch")
    resolved = sticky_instructions.resolve(ws2, ws.task_id)
    assert resolved is not None
    assert resolved.text == "do the thing"
    assert resolved.origin == "dispatch"


def test_compose_returns_sticky_unchanged_when_no_generated_text() -> None:
    assert sticky_instructions.compose("sticky", None) == "sticky"
    assert sticky_instructions.compose("sticky", "") == "sticky"


def test_compose_joins_sticky_then_generated_with_blank_line() -> None:
    result = sticky_instructions.compose("sticky text", "generated text")
    assert result == "sticky text\n\ngenerated text"


def test_compose_under_cap_needs_no_truncation() -> None:
    result = sticky_instructions.compose("short sticky", "short generated")
    assert _TRUNCATION_MARKER not in result


def test_compose_truncates_the_sticky_half_when_over_cap() -> None:
    sticky = "S" * 39990
    generated = "G" * 100
    result = sticky_instructions.compose(sticky, generated)
    assert len(result) <= sticky_instructions.MAX_COMPOSED_CHARS
    assert result.endswith(generated)
    assert _TRUNCATION_MARKER in result


def test_compose_leaves_generated_half_intact_when_truncating() -> None:
    sticky = "S" * 60000
    generated = "G" * 500
    result = sticky_instructions.compose(sticky, generated)
    assert result.endswith(generated)
    assert ("G" * 500) in result
    assert len(result) <= sticky_instructions.MAX_COMPOSED_CHARS


def test_compose_drops_sticky_entirely_when_generated_alone_leaves_no_budget() -> None:
    generated = "G" * (sticky_instructions.MAX_COMPOSED_CHARS - 10)
    sticky = "sticky text"
    result = sticky_instructions.compose(sticky, generated)
    assert result == generated
    assert len(result) <= sticky_instructions.MAX_COMPOSED_CHARS


def test_compose_truncates_generated_when_it_alone_exceeds_the_cap() -> None:
    generated = "G" * (sticky_instructions.MAX_COMPOSED_CHARS + 500)
    result = sticky_instructions.compose("sticky", generated)
    assert len(result) == sticky_instructions.MAX_COMPOSED_CHARS
    assert result == generated[: sticky_instructions.MAX_COMPOSED_CHARS]


def test_should_persist_true_for_consuming_team() -> None:
    assert sticky_instructions.should_persist("implementation_fix") is True


@pytest.mark.parametrize("team", sorted(sticky_instructions.NON_CONSUMING_TEAMS))
def test_should_persist_false_for_non_consuming_team(team: str) -> None:
    assert sticky_instructions.should_persist(team) is False


def test_for_enqueue_passes_through_generated_unchanged_for_non_consuming_team(
    ws: Workspace,
) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "sticky", origin="dispatch")
    result, origin = sticky_instructions.for_enqueue(ws2, ws.task_id, "pr_merge", "gate json")
    assert result == "gate json"
    assert origin is None


def test_for_enqueue_returns_none_and_none_when_nothing_applies(ws: Workspace) -> None:
    result, origin = sticky_instructions.for_enqueue(ws, ws.task_id, "implementation_fix", None)
    assert result is None
    assert origin is None


def test_for_enqueue_returns_generated_with_generated_origin_when_no_sticky(
    ws: Workspace,
) -> None:
    result, origin = sticky_instructions.for_enqueue(
        ws, ws.task_id, "implementation_fix", "fresh brief"
    )
    assert result == "fresh brief"
    assert origin == "generated"


def test_for_enqueue_composes_sticky_and_generated_with_inherited_origin(
    ws: Workspace,
) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "sticky text", origin="dispatch")
    result, origin = sticky_instructions.for_enqueue(
        ws2, ws.task_id, "implementation_fix", "fresh brief"
    )
    assert result == "sticky text\n\nfresh brief"
    assert origin == "inherited"


def test_for_enqueue_returns_sticky_alone_with_inherited_origin_when_no_generated(
    ws: Workspace,
) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "sticky text", origin="dispatch")
    result, origin = sticky_instructions.for_enqueue(ws2, ws.task_id, "implementation_fix", None)
    assert result == "sticky text"
    assert origin == "inherited"

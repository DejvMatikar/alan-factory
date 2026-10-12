from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from alan.workspace import ScenarioBinding, create, read, update_scenario_binding


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def test_scenario_binding_defaults() -> None:
    binding = ScenarioBinding(reason="repointed after test reorg")
    assert binding.when is None
    assert binding.test_ref is None
    assert binding.waived is False
    assert binding.reason == "repointed after test reorg"
    assert binding.written_by == "operator"


def test_scenario_binding_written_by_defaults_to_operator_when_key_absent() -> None:
    binding = ScenarioBinding.model_validate({"reason": "r"})
    assert binding.written_by == "operator"


def test_scenario_binding_written_by_accepts_fix_rebind() -> None:
    binding = ScenarioBinding(reason="r", written_by="fix_rebind")
    assert binding.written_by == "fix_rebind"


def test_scenario_binding_written_by_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ScenarioBinding.model_validate({"reason": "r", "written_by": "somebody_else"})


def test_scenario_binding_written_by_survives_model_copy_round_trip() -> None:
    binding = ScenarioBinding(reason="r", written_by="fix_rebind")
    copied = binding.model_copy(update={"when": "uv run pytest -k y"})
    assert copied.written_by == "fix_rebind"


def test_scenario_binding_requires_non_empty_reason() -> None:
    with pytest.raises(ValidationError):
        ScenarioBinding(reason="")


def test_meta_scenario_bindings_defaults_to_empty_dict(ws_root: Path) -> None:
    ws = create("binding-defaults", "d", root=ws_root)
    assert ws.meta.scenario_bindings == {}


def test_update_scenario_binding_writes_and_persists(ws_root: Path) -> None:
    ws = create("binding-write", "d", root=ws_root)
    binding = ScenarioBinding(when="uv run pytest tests/test_x.py", reason="stale test_ref")
    updated = update_scenario_binding(ws, "scen-1", binding)
    assert updated.meta.scenario_bindings["scen-1"] == binding
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings["scen-1"] == binding


def test_update_scenario_binding_replaces_not_merges(ws_root: Path) -> None:
    ws = create("binding-replace", "d", root=ws_root)
    first = ScenarioBinding(when="uv run pytest tests/test_x.py", waived=True, reason="r1")
    ws = update_scenario_binding(ws, "scen-1", first)
    second = ScenarioBinding(test_ref="tests/test_y.py::test_y", reason="r2")
    ws = update_scenario_binding(ws, "scen-1", second)
    stored = ws.meta.scenario_bindings["scen-1"]
    assert stored == second
    assert stored.waived is False
    assert stored.when is None


def test_update_scenario_binding_delete_removes_entry(ws_root: Path) -> None:
    ws = create("binding-delete", "d", root=ws_root)
    ws = update_scenario_binding(
        ws, "scen-1", ScenarioBinding(when="uv run pytest -k x", reason="r")
    )
    ws = update_scenario_binding(ws, "scen-1", None)
    assert ws.meta.scenario_bindings == {}
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings == {}


def test_update_scenario_binding_delete_of_absent_key_is_noop(ws_root: Path) -> None:
    ws = create("binding-noop", "d", root=ws_root)
    result = update_scenario_binding(ws, "ghost", None)
    assert result is ws


def test_update_scenario_binding_leaves_other_ids_untouched(ws_root: Path) -> None:
    ws = create("binding-multi", "d", root=ws_root)
    ws = update_scenario_binding(
        ws, "scen-1", ScenarioBinding(when="uv run pytest -k a", reason="r1")
    )
    ws = update_scenario_binding(
        ws, "scen-2", ScenarioBinding(when="uv run pytest -k b", reason="r2")
    )
    ws = update_scenario_binding(ws, "scen-1", None)
    assert set(ws.meta.scenario_bindings) == {"scen-2"}


def test_update_scenario_binding_bumps_updated_at(ws_root: Path) -> None:
    ws = create("binding-touch", "d", root=ws_root)
    before = ws.meta.updated_at
    updated = update_scenario_binding(
        ws, "scen-1", ScenarioBinding(when="uv run pytest -k a", reason="r")
    )
    assert updated.meta.updated_at >= before

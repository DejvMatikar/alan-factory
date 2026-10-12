from __future__ import annotations

from alan.engine_config import EngineConfig, default_engine_config
from alan.teams import _inner_loop_gate, _native_capture
from alan.teams import implementation as _impl


def _ios_cfg() -> EngineConfig:
    return default_engine_config().model_copy(update={"preset": "ios"})


def test_inner_loop_gate_block_returns_guidance_for_ios_fix_team() -> None:
    assert (
        _inner_loop_gate.inner_loop_gate_block(_ios_cfg(), "implementation_fix")
        == _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE
    )


def test_inner_loop_gate_block_empty_for_ios_implementation_team() -> None:
    assert _inner_loop_gate.inner_loop_gate_block(_ios_cfg(), "implementation") == ""


def test_inner_loop_gate_block_empty_for_default_engine_fix_team() -> None:
    cfg = default_engine_config()
    assert _inner_loop_gate.inner_loop_gate_block(cfg, "implementation_fix") == ""


def test_inner_loop_gate_block_empty_for_default_engine_other_team() -> None:
    cfg = default_engine_config()
    assert _inner_loop_gate.inner_loop_gate_block(cfg, "implementation") == ""


def test_inner_loop_gate_block_empty_for_ios_unknown_team() -> None:
    assert _inner_loop_gate.inner_loop_gate_block(_ios_cfg(), "some_other_team") == ""


def test_inner_loop_gate_block_empty_for_empty_string_preset() -> None:
    cfg = default_engine_config().model_copy(update={"preset": ""})
    assert _inner_loop_gate.inner_loop_gate_block(cfg, "implementation_fix") == ""


def test_fix_teams_is_a_frozenset_with_exactly_one_member() -> None:
    assert isinstance(_inner_loop_gate._FIX_TEAMS, frozenset)  # noqa: SLF001
    assert len(_inner_loop_gate._FIX_TEAMS) == 1  # noqa: SLF001


def test_guidance_names_the_stages_flag_and_included_stage_tokens() -> None:
    guidance = _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE
    assert "--stages" in guidance
    assert "build,test_unit,test_ui" in guidance


def test_guidance_omits_the_skipped_stage_names() -> None:
    guidance = _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE
    assert "gate_selftest" not in guidance
    assert "layering_probe" not in guidance
    assert "archive_unsigned" not in guidance


def test_guidance_states_full_gate_is_the_only_verdict() -> None:
    guidance = _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE
    assert "no verdict" in guidance
    assert "only run that does" in guidance


def test_environment_block_for_orders_inner_loop_guidance_after_native_capture() -> None:
    cfg = _ios_cfg()
    rendered = _impl.environment_block_for(cfg, "implementation_fix")
    recipe_index = rendered.index(_native_capture.NATIVE_CAPTURE_RECIPE)
    guidance_index = rendered.index(_inner_loop_gate.INNER_LOOP_GATE_GUIDANCE)
    assert recipe_index < guidance_index


def test_environment_block_for_omits_guidance_for_ios_implementation_team() -> None:
    cfg = _ios_cfg()
    rendered = _impl.environment_block_for(cfg, "implementation")
    assert _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE not in rendered


def test_environment_block_for_omits_guidance_for_default_engine_fix_team() -> None:
    cfg = default_engine_config()
    rendered = _impl.environment_block_for(cfg, "implementation_fix")
    assert _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE not in rendered


def test_environment_block_for_omits_guidance_for_default_engine_other_team() -> None:
    cfg = default_engine_config()
    rendered = _impl.environment_block_for(cfg, "implementation")
    assert _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE not in rendered


def test_environment_block_for_separates_native_capture_from_inner_loop_guidance() -> None:
    cfg = _ios_cfg()
    rendered = _impl.environment_block_for(cfg, "implementation_fix")
    joined_without_separator = (
        _native_capture.NATIVE_CAPTURE_RECIPE + _inner_loop_gate.INNER_LOOP_GATE_GUIDANCE
    )
    assert joined_without_separator not in rendered
    recipe_end = rendered.index(_native_capture.NATIVE_CAPTURE_RECIPE) + len(
        _native_capture.NATIVE_CAPTURE_RECIPE
    )
    guidance_start = rendered.index(_inner_loop_gate.INNER_LOOP_GATE_GUIDANCE)
    assert rendered[recipe_end:guidance_start] == "\n\n"


def test_environment_block_for_byte_equal_without_inner_loop_gate_when_not_triggering() -> None:
    cfg = default_engine_config()
    rendered = _impl.environment_block_for(cfg, "implementation")
    expected = _impl.render_environment_block(cfg) + _native_capture.native_capture_block(
        cfg, "implementation"
    )
    assert rendered == expected

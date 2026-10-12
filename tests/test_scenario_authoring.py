from __future__ import annotations

from alan.engine_config import ScenarioConfig, default_engine_config
from alan.engine_presets import ios_preset
from alan.handover import TestScenario
from alan.teams import scenario_authoring


def _scenario(sid: str, when: str) -> TestScenario:
    return TestScenario(id=sid, description="d", given="g", when=when, then="t")


def test_shape_rejection_forbidden_operator_on_default_engine():
    when = "uv run --no-active pytest tests/x.py && rm -rf /"
    result = scenario_authoring.shape_rejection(when, None)
    assert result == "forbidden shell operator '&&' in when field"


def test_shape_rejection_unbalanced_quotes_on_default_engine():
    when = 'uv run --no-active pytest "tests/x.py'
    result = scenario_authoring.shape_rejection(when, None)
    assert result is not None
    assert result.startswith("when field has unbalanced quotes:")


def test_shape_rejection_path_traversal_on_default_engine():
    when = "uv run --no-active pytest ../../etc/passwd"
    result = scenario_authoring.shape_rejection(when, None)
    assert result is not None
    assert "path traversal" in result
    assert "../../etc/passwd" in result


def test_shape_rejection_forbidden_operator_on_ios_engine():
    cfg = ios_preset()
    when = "xcodebuild test -scheme Foo && rm -rf /"
    result = scenario_authoring.shape_rejection(when, cfg)
    assert result == "forbidden shell operator '&&' in when field"


def test_shape_rejection_unbalanced_quotes_on_ios_engine():
    cfg = ios_preset()
    when = 'xcodebuild test -scheme "Foo'
    result = scenario_authoring.shape_rejection(when, cfg)
    assert result is not None
    assert result.startswith("when field has unbalanced quotes:")


def test_shape_rejection_path_traversal_on_ios_engine():
    cfg = ios_preset()
    when = "xcodebuild test -scheme ../../etc/passwd"
    result = scenario_authoring.shape_rejection(when, cfg)
    assert result is not None
    assert "path traversal" in result


def test_shape_rejection_never_fires_for_a_prefix_outside_the_allowlist():
    when = "git push origin ../../etc/passwd"
    assert scenario_authoring.shape_rejection(when, None) is None


def test_shape_rejection_ignores_prose_with_apostrophes():
    when = "The user's session expires"
    assert scenario_authoring.shape_rejection(when, None) is None


def test_shape_rejection_returns_none_for_empty_when():
    assert scenario_authoring.shape_rejection("", None) is None


def test_collect_rejections_none_when_all_entries_clean():
    entries = [(0, _scenario("s1", "uv run --no-active pytest tests/x.py"))]
    assert scenario_authoring.collect_rejections(entries, None) is None


def test_collect_rejections_enumerates_every_offending_index_with_its_own_reason():
    entries = [
        (0, _scenario("s0", "uv run --no-active pytest tests/x.py && rm -rf /")),
        (1, _scenario("s1", "uv run --no-active pytest tests/x.py")),
        (2, _scenario("s2", "uv run --no-active pytest")),
    ]
    message = scenario_authoring.collect_rejections(entries, None)
    assert message is not None
    assert "test_scenarios[0].when" in message
    assert "test_scenarios[1].when" not in message
    assert "test_scenarios[2].when" in message
    assert "forbidden shell operator" in message
    assert "unscoped whole-suite command" in message


def test_collect_rejections_partial_repair_yields_a_different_string():
    two_offending = [
        (0, _scenario("s0", "uv run --no-active pytest")),
        (1, _scenario("s1", "npm --prefix alan/web run test")),
    ]
    one_offending = [
        (0, _scenario("s0", "uv run --no-active pytest tests/x.py")),
        (1, _scenario("s1", "npm --prefix alan/web run test")),
    ]
    first = scenario_authoring.collect_rejections(two_offending, None)
    second = scenario_authoring.collect_rejections(one_offending, None)
    assert first != second
    assert first is not None and second is not None


def test_default_engine_guidance_names_both_command_shapes_and_default_figures():
    section = scenario_authoring.scenario_section(None)
    assert "uv run --no-active pytest" in section
    assert "npm --prefix alan/web run" in section
    assert "120s" in section
    assert "600s" in section


def test_ios_engine_guidance_names_its_gate_script_and_its_own_figures():
    cfg = ios_preset()
    section = scenario_authoring.scenario_section(cfg)
    assert "bash scripts/gate.sh" in section
    assert "900s" in section


def test_ios_engine_guidance_contains_neither_default_engine_shape():
    cfg = ios_preset()
    section = scenario_authoring.scenario_section(cfg)
    assert "uv run" not in section
    assert "npm --prefix" not in section


def test_ios_engine_worked_example_uses_an_argument_bearing_scenario_prefix():
    cfg = ios_preset()
    section = scenario_authoring.scenario_section(cfg)
    assert 'when: "xcodebuild <target>"' in section
    assert 'when: "bash scripts/gate.sh' not in section


def test_scenario_timeout_block_reflects_the_runtime_override(monkeypatch):
    monkeypatch.setenv("ALAN_SCENARIO_TIMEOUT_S", "42")
    section = scenario_authoring.scenario_section(None)
    assert "42s" in section
    assert "120s" not in section


def test_scenario_example_block_falls_back_when_engine_has_no_shapes():
    cfg = default_engine_config().model_copy(
        update={"gate_commands": [], "scenario": ScenarioConfig()}
    )
    section = scenario_authoring.scenario_section(cfg)
    assert 'when: "uv run --no-active pytest <target>"' in section


def test_both_renderings_carry_the_shared_generic_rules():
    default_section = scenario_authoring.scenario_section(None)
    ios_section = scenario_authoring.scenario_section(ios_preset())
    for section in (default_section, ios_section):
        assert "rc=2" in section
        assert "rc=4" in section
        assert "rc=5" in section
        assert "advisory only" in section
        assert "never an unscoped whole-suite invocation" in section
        assert "YAML safety rule" in section


def test_synthesizer_prompt_composes_prefix_section_and_suffix():
    prompt = scenario_authoring.synthesizer_prompt(None)
    assert prompt.startswith("You are the synthesizer on a planning team.")
    assert "Handover.test_scenarios" in prompt
    assert "uv run --no-active pytest" in prompt

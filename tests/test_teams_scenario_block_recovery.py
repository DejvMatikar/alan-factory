from __future__ import annotations

import pytest
import yaml

from alan.handover import TestScenario
from alan.teams import _scenario_block as scenario_block_mod

_UNPARSED_NO_TOP_LEVEL_ENTRIES = ": broken: yaml: {{"
_UNPARSED_CONTROL_CHARACTER = "\x01bad"
_SCHEMA_INVALID_ENTRIES = "- id: s1\n- id: s2"
_MIXED_VALID_AND_COLON_SPACE_BREAK = (
    "- id: s1_valid\n"
    '  description: "Valid scenario"\n'
    '  given: "state"\n'
    '  when: "action"\n'
    '  then: "result"\n'
    "- id: s2_broken\n"
    "  description: bad: value with colon\n"
    '  given: "state"\n'
    '  when: "action"\n'
    '  then: "result"'
)


def _exact_fence(body: str) -> str:
    return f"```yaml test_scenarios.yaml\n{body}\n```"


def _heading_fence(body: str) -> str:
    return f"## TEST SCENARIOS\n```yaml\n{body}\n```"


def test_validate_scenario_returns_none_for_non_dict() -> None:
    assert scenario_block_mod.validate_scenario("not a dict") is None


def test_validate_scenario_returns_none_for_schema_mismatch() -> None:
    assert scenario_block_mod.validate_scenario({"id": "s1"}) is None


def test_validate_scenario_returns_scenario_for_valid_dict() -> None:
    result = scenario_block_mod.validate_scenario({
        "id": "s1", "description": "d", "given": "g", "when": "w", "then": "t",
    })
    assert isinstance(result, TestScenario)
    assert result.id == "s1"


def test_recover_entries_keeps_valid_entry_and_drops_colon_space_break() -> None:
    result = scenario_block_mod.recover_entries(_MIXED_VALID_AND_COLON_SPACE_BREAK)
    assert [s.id for s in result] == ["s1_valid"]


def test_recover_entries_returns_empty_when_no_top_level_entries() -> None:
    assert scenario_block_mod.recover_entries(_UNPARSED_NO_TOP_LEVEL_ENTRIES) == []


def test_recover_entries_returns_empty_for_uniformly_indented_block() -> None:
    block = (
        "  - id: s1\n"
        '    description: "d"\n'
        '    given: "g"\n'
        '    when: "w"\n'
        '    then: "t"'
    )
    assert scenario_block_mod.recover_entries(block) == []


def test_recover_entries_returns_empty_when_every_entry_fails_schema_validation() -> None:
    assert scenario_block_mod.recover_entries(_SCHEMA_INVALID_ENTRIES) == []


def test_recover_entries_ignores_preamble_before_first_top_level_item() -> None:
    block = (
        "some prose the worker left before the list\n"
        "more prose\n"
        "- id: s1\n"
        '  description: "d"\n'
        '  given: "g"\n'
        '  when: "w"\n'
        '  then: "t"'
    )
    result = scenario_block_mod.recover_entries(block)
    assert [s.id for s in result] == ["s1"]


def test_recover_entries_treats_nested_dash_lines_as_part_of_the_entry() -> None:
    block = (
        "- id: s1\n"
        '  description: "d"\n'
        '  given: "g"\n'
        '  when: "w"\n'
        "  then: |\n"
        "    - nested bullet point\n"
    )
    result = scenario_block_mod.recover_entries(block)
    assert [s.id for s in result] == ["s1"]
    assert "nested bullet point" in result[0].then


def test_recover_entries_returns_empty_for_empty_block() -> None:
    assert scenario_block_mod.recover_entries("") == []


def test_entry_scenario_returns_none_for_non_list_chunk() -> None:
    assert scenario_block_mod._entry_scenario("null\n") is None


def test_entry_scenario_returns_none_for_empty_list_chunk() -> None:
    assert scenario_block_mod._entry_scenario("[]\n") is None


def test_entry_scenario_returns_none_on_yaml_error() -> None:
    assert scenario_block_mod._entry_scenario(": bad: {{\n") is None


def test_source_line_returns_none_when_mark_is_none() -> None:
    assert scenario_block_mod._source_line("abc", None) is None


def test_source_line_returns_none_when_line_index_out_of_range() -> None:
    mark = yaml.Mark("test", 0, 99, 0, None, 0)
    assert scenario_block_mod._source_line("abc", mark) is None


def test_source_line_returns_matching_line_when_in_range() -> None:
    mark = yaml.Mark("test", 0, 1, 0, None, 0)
    assert scenario_block_mod._source_line("line0\nline1\n", mark) == "line1"


def test_parse_diagnostics_returns_empty_dict_when_block_parses_cleanly() -> None:
    assert scenario_block_mod._parse_diagnostics(_SCHEMA_INVALID_ENTRIES) == {}


def test_parse_diagnostics_marked_error_carries_parse_error_and_source_line() -> None:
    result = scenario_block_mod._parse_diagnostics(_UNPARSED_NO_TOP_LEVEL_ENTRIES)
    assert "parse_error" in result
    assert result["source_line"] == _UNPARSED_NO_TOP_LEVEL_ENTRIES


def test_parse_diagnostics_unmarked_error_carries_parse_error_only() -> None:
    result = scenario_block_mod._parse_diagnostics(_UNPARSED_CONTROL_CHARACTER)
    assert "parse_error" in result
    assert "source_line" not in result


def test_parse_diagnostics_omits_source_line_when_mark_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_block: str) -> None:
        raise yaml.MarkedYAMLError(problem="broken", problem_mark=None)

    monkeypatch.setattr(yaml, "safe_load", boom)
    result = scenario_block_mod._parse_diagnostics("irrelevant")
    assert "parse_error" in result
    assert "source_line" not in result


def test_unparsed_audit_row_returns_none_when_scenarios_present() -> None:
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    row = scenario_block_mod.unparsed_audit_row(
        _exact_fence(_UNPARSED_NO_TOP_LEVEL_ENTRIES),
        [scenario],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is None


def test_unparsed_audit_row_returns_none_when_no_positively_identified_block() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        "## Summary\nNothing to see here.\n",
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is None


def test_unparsed_audit_row_marked_syntax_error_shape() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        _exact_fence(_UNPARSED_NO_TOP_LEVEL_ENTRIES),
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is not None
    assert row["task_id"] == "task-1"
    assert row["action"] == "scenario_block_unparsed"
    assert row["team"] == "implementation_fix"
    assert "parse_error" in row
    assert row["source_line"] == _UNPARSED_NO_TOP_LEVEL_ENTRIES


def test_unparsed_audit_row_unmarked_syntax_error_shape() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        _exact_fence(_UNPARSED_CONTROL_CHARACTER),
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is not None
    assert "parse_error" in row
    assert "source_line" not in row


def test_unparsed_audit_row_no_exception_shape() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        _exact_fence(_SCHEMA_INVALID_ENTRIES),
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is not None
    assert row["task_id"] == "task-1"
    assert row["action"] == "scenario_block_unparsed"
    assert row["team"] == "implementation_fix"
    assert "parse_error" not in row
    assert "source_line" not in row


def test_unparsed_audit_row_matches_via_heading_when_exact_fence_absent() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        _heading_fence(_SCHEMA_INVALID_ENTRIES),
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is not None
    assert row["action"] == "scenario_block_unparsed"


def test_unparsed_audit_row_action_value_is_stable_and_keyword_free() -> None:
    row = scenario_block_mod.unparsed_audit_row(
        _exact_fence(_SCHEMA_INVALID_ENTRIES),
        [],
        task_id="task-1",
        team="implementation_fix",
    )
    assert row is not None
    action = row["action"]
    assert action == "scenario_block_unparsed"
    banned = ("fail", "error", "timeout", "exception", "401", "403", "5xx")
    lowered = str(action).lower()
    assert not any(keyword in lowered for keyword in banned)


def test_scenarios_block_guidance_covers_colon_space_and_quote_hazards() -> None:
    guidance = scenario_block_mod.SCENARIOS_BLOCK_GUIDANCE
    assert "colon followed by a space" in guidance
    assert "single quote" in guidance
    assert "backtick" in guidance
    assert "parses as YAML" in guidance

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from alan.handover import TestScenario
from alan.teams import _scenario_rebind_block
from alan.teams._scenario_rebind_block import (
    REBIND_BLOCK_GUIDANCE,
    current_scenarios_block,
    parse_rebinds,
    rejected_rebinds_block,
    unparsed_rebind_audit_row,
)

_UNPARSED_CONTROL_CHARACTER = "\x01bad"


def _fenced(body: str) -> str:
    return f"Some synthesis text.\n```yaml scenario_rebind.yaml\n{body}\n```\nmore text"


def test_parse_rebinds_no_block_returns_empty() -> None:
    assert parse_rebinds("no fence here at all") == {}


def test_parse_rebinds_simple_mapping() -> None:
    synthesis = _fenced("s1: uv run --no-active pytest tests/test_new.py::test_it")
    assert parse_rebinds(synthesis) == {
        "s1": "uv run --no-active pytest tests/test_new.py::test_it"
    }


def test_parse_rebinds_multiple_entries_preserve_order() -> None:
    synthesis = _fenced("s1: uv run pytest tests/test_a.py\ns2: uv run pytest tests/test_b.py")
    assert list(parse_rebinds(synthesis).keys()) == ["s1", "s2"]


def test_parse_rebinds_drops_dict_valued_entry_wholesale() -> None:
    synthesis = _fenced(
        "s1: uv run pytest tests/test_a.py\n"
        "s2:\n  when: uv run pytest tests/test_b.py\n  waived: true"
    )
    result = parse_rebinds(synthesis)
    assert result == {"s1": "uv run pytest tests/test_a.py"}
    assert "s2" not in result


def test_parse_rebinds_drops_nested_test_ref_entry_wholesale() -> None:
    synthesis = _fenced("s1:\n  test_ref: tests/test_new.py::test_it")
    assert parse_rebinds(synthesis) == {}


def test_parse_rebinds_drops_nested_given_then_entry_wholesale() -> None:
    synthesis = _fenced("s1:\n  given: g\n  then: t")
    assert parse_rebinds(synthesis) == {}


def test_parse_rebinds_drops_empty_string_value() -> None:
    synthesis = _fenced("s1: ''")
    assert parse_rebinds(synthesis) == {}


def test_parse_rebinds_drops_non_string_key() -> None:
    synthesis = _fenced("42: uv run pytest tests/test_a.py")
    assert parse_rebinds(synthesis) == {}


def test_parse_rebinds_malformed_yaml_returns_empty() -> None:
    synthesis = _fenced("s1: [unterminated")
    assert parse_rebinds(synthesis) == {}


def test_parse_rebinds_non_mapping_top_level_returns_empty() -> None:
    synthesis = _fenced("- s1\n- s2")
    assert parse_rebinds(synthesis) == {}


def test_unparsed_rebind_audit_row_none_when_rebinds_present() -> None:
    synthesis = _fenced("s1: uv run pytest tests/test_a.py")
    rebinds = parse_rebinds(synthesis)
    assert unparsed_rebind_audit_row(synthesis, rebinds, task_id="t1", team="x") is None


def test_unparsed_rebind_audit_row_none_when_no_block() -> None:
    assert unparsed_rebind_audit_row("no block", {}, task_id="t1", team="x") is None


def test_unparsed_rebind_audit_row_present_when_block_empty_after_parse() -> None:
    synthesis = _fenced("- s1\n- s2")
    row = unparsed_rebind_audit_row(synthesis, {}, task_id="t1", team="x")
    assert row is not None
    assert row["task_id"] == "t1"
    assert row["action"] == "scenario_rebind_unparsed"
    assert row["team"] == "x"


def test_unparsed_rebind_audit_row_marked_yaml_error_includes_source_line() -> None:
    synthesis = _fenced("s1: [unterminated")
    row = unparsed_rebind_audit_row(synthesis, {}, task_id="t1", team="x")
    assert row is not None
    assert "parse_error" in row
    assert row.get("source_line") == "s1: [unterminated"


def test_unparsed_rebind_audit_row_unmarked_yaml_error_has_no_source_line() -> None:
    synthesis = _fenced(_UNPARSED_CONTROL_CHARACTER)
    row = unparsed_rebind_audit_row(synthesis, {}, task_id="t1", team="x")
    assert row is not None
    assert "parse_error" in row
    assert "source_line" not in row


def test_parse_diagnostics_omits_source_line_when_mark_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_block: str) -> None:
        raise yaml.MarkedYAMLError(problem="broken", problem_mark=None)

    monkeypatch.setattr(yaml, "safe_load", boom)
    result = _scenario_rebind_block._parse_diagnostics("irrelevant")  # noqa: SLF001
    assert "parse_error" in result
    assert "source_line" not in result


def test_parse_diagnostics_omits_source_line_when_mark_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark = yaml.Mark("test", 0, 99, 0, None, 0)

    def boom(_block: str) -> None:
        raise yaml.MarkedYAMLError(problem="broken", problem_mark=mark)

    monkeypatch.setattr(yaml, "safe_load", boom)
    result = _scenario_rebind_block._parse_diagnostics("only one line")  # noqa: SLF001
    assert "parse_error" in result
    assert "source_line" not in result


def test_current_scenarios_block_empty_for_no_scenarios() -> None:
    assert current_scenarios_block([], None) == ""


def test_current_scenarios_block_renders_id_and_command() -> None:
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="uv run --no-active pytest tests/test_a.py", then="t",
    )
    block = current_scenarios_block([scenario], None)
    assert "s1" in block
    assert "uv run --no-active pytest tests/test_a.py" in block


def test_current_scenarios_block_renders_prose_command_verbatim() -> None:
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="The user manually verifies in the browser", then="t",
    )
    block = current_scenarios_block([scenario], None)
    assert "The user manually verifies in the browser" in block


def test_current_scenarios_block_renders_placeholder_for_empty_command() -> None:
    scenario = TestScenario(id="s1", description="d", given="g", when="", then="t")
    block = current_scenarios_block([scenario], None)
    assert "not rebindable" in block


def test_current_scenarios_block_synthesizes_command_from_test_ref() -> None:
    scenario = TestScenario(
        id="s1", description="d", given="g", when="", then="t",
        test_ref="tests/test_a.py::test_it",
    )
    block = current_scenarios_block([scenario], None)
    assert "uv run --no-active pytest tests/test_a.py::test_it" in block


def test_guidance_mentions_exact_fence() -> None:
    assert "```yaml scenario_rebind.yaml" in REBIND_BLOCK_GUIDANCE


def test_guidance_no_longer_promises_a_probe() -> None:
    assert "verified to resolve in the current checkout" not in REBIND_BLOCK_GUIDANCE
    assert "NOT executed before acceptance" in REBIND_BLOCK_GUIDANCE
    assert "the scenario FAILS the gate instead of skipping it" in REBIND_BLOCK_GUIDANCE


def _write_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **row: object
) -> Path:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    row.setdefault("rule", _scenario_rebind_block.RULE_ALLOWLIST)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return path


def test_rejected_rebinds_block_empty_when_no_audit_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_renders_matching_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
    )
    block = rejected_rebinds_block("ws-1")
    assert "s1" in block
    assert "curl http://example.com" in block
    assert "disallowed command" in block


def test_rejected_rebinds_block_filters_out_other_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="other-ws",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
    )
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_filters_out_non_rejection_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind", workspace_id="ws-1",
        scenario_id="s1", when="uv run pytest -k y", reason="fix_rebind",
    )
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_skips_malformed_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
    )
    block = rejected_rebinds_block("ws-1")
    assert "s1" in block


def test_rejected_rebinds_block_skips_blank_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
    )
    block = rejected_rebinds_block("ws-1")
    assert "s1" in block


def test_rejected_rebinds_block_skips_non_dict_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("42\n")
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
    )
    block = rejected_rebinds_block("ws-1")
    assert "s1" in block


def test_rejected_rebinds_block_caps_at_max_rendered_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "scenario_override_audit.jsonl"
    rows = [
        {
            "action": "fix_rebind_rejected", "workspace_id": "ws-1",
            "scenario_id": f"s{i}", "when": "curl http://example.com", "reason": "r",
            "rule": _scenario_rebind_block.RULE_ALLOWLIST,
        }
        for i in range(25)
    ]
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    block = rejected_rebinds_block("ws-1")
    assert "s0" not in block
    assert "s24" in block
    assert block.count("- `s") == 20


def test_rejected_rebinds_block_omits_rows_with_no_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
        rule=None,
    )
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_omits_rows_with_non_string_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="disallowed command",
        rule=[_scenario_rebind_block.RULE_ALLOWLIST],
    )
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_omits_rows_with_dead_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com", reason="non-default forge",
        rule="non_default_forge",
    )
    assert rejected_rebinds_block("ws-1") == ""


def test_rejected_rebinds_block_renders_rows_with_live_rule_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_audit_row(
        tmp_path, monkeypatch,
        action="fix_rebind_rejected", workspace_id="ws-1",
        scenario_id="s1", when="curl http://example.com",
        reason="command prefix 'curl' not in scenario runner allowlist",
        rule=_scenario_rebind_block.RULE_ALLOWLIST,
    )
    block = rejected_rebinds_block("ws-1")
    assert "s1" in block
    assert "command prefix 'curl' not in scenario runner allowlist" in block


def test_live_rebind_rejection_rules_excludes_the_dead_forge_rule() -> None:
    assert "non_default_forge" not in _scenario_rebind_block.LIVE_REBIND_REJECTION_RULES
    assert _scenario_rebind_block.LIVE_REBIND_REJECTION_RULES == frozenset(
        {
            _scenario_rebind_block.RULE_UNKNOWN_SCENARIO,
            _scenario_rebind_block.RULE_PROSE_WHEN,
            _scenario_rebind_block.RULE_ALLOWLIST,
            _scenario_rebind_block.RULE_REBIND_CAP,
            _scenario_rebind_block.RULE_TEST_REF_CONFLICT,
            _scenario_rebind_block.RULE_WAIVED_CONFLICT,
        }
    )

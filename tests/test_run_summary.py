from __future__ import annotations

from pathlib import Path

import pytest

from alan.claude_cli import _effort_for_model
from alan.run_summary import (
    ToolUseCount,
    derive_run_summary,
    run_summary_placeholder,
    tool_use_event_label,
    tool_use_label,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "run_summary_stream.jsonl"


def test_missing_stream_returns_none(tmp_path: Path) -> None:
    assert derive_run_summary(tmp_path / "nope.jsonl", system_prompt="p") is None


def test_empty_stream_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text("", encoding="utf-8")
    assert derive_run_summary(path, system_prompt="p") is None


def test_whitespace_only_stream_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text("\n\n   \n", encoding="utf-8")
    assert derive_run_summary(path, system_prompt="p") is None


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        "not json at all\n"
        '["also", "not", "an", "object"]\n'
        '{"type": "result", "total_cost_usd": 0.5, "usage": {"input_tokens": 10, '
        '"output_tokens": 20}}\n',
        encoding="utf-8",
    )
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.total_cost_usd == pytest.approx(0.5)
    assert summary.total_output_tokens == 20
    assert summary.total_input_tokens == 10
    assert summary.tool_counts == []


def test_result_event_without_usage_keeps_cost_only(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "total_cost_usd": 0.25}\n', encoding="utf-8")
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.total_cost_usd == pytest.approx(0.25)
    assert summary.total_output_tokens is None
    assert summary.total_input_tokens is None


def test_non_result_non_assistant_events_contribute_no_usage_or_tools(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "system", "subtype": "init"}\n'
        '{"type": "user", "content": [{"type": "tool_result", "content": "ok"}]}\n',
        encoding="utf-8",
    )
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.total_cost_usd is None
    assert summary.total_output_tokens is None
    assert summary.total_input_tokens is None
    assert summary.tool_counts == []


def test_assistant_event_with_non_tool_use_blocks_contributes_no_tools(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}\n'
        '{"type": "result", "total_cost_usd": 0.1}\n',
        encoding="utf-8",
    )
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.tool_counts == []


def test_assistant_event_with_missing_message_is_defensive(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "assistant"}\n'
        '{"type": "assistant", "message": "not-a-dict"}\n'
        '{"type": "assistant", "message": {"content": "not-a-list"}}\n'
        '{"type": "assistant", "message": {"content": [1, 2, "not-a-dict-block"]}}\n'
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": 5}]}}\n'
        '{"type": "result", "total_cost_usd": 0.1}\n',
        encoding="utf-8",
    )
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.tool_counts == []


def test_result_event_with_non_numeric_or_bool_usage_fields_skipped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "result", "total_cost_usd": true, '
        '"usage": {"input_tokens": true, "output_tokens": "50"}}\n',
        encoding="utf-8",
    )
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.total_cost_usd is None
    assert summary.total_output_tokens is None
    assert summary.total_input_tokens is None


def test_result_event_with_non_dict_usage_skipped(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "usage": "nope"}\n', encoding="utf-8")
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.total_input_tokens is None
    assert summary.total_output_tokens is None


def test_system_prompt_and_setup_are_passed_through(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "total_cost_usd": 0.1}\n', encoding="utf-8")
    summary = derive_run_summary(path, system_prompt="you are alan", model="sonnet")
    assert summary is not None
    assert summary.system_prompt == "you are alan"
    assert summary.model == "sonnet"
    assert summary.effort == "high"


@pytest.mark.parametrize(
    ("model", "expected_effort"),
    [("opus", "xhigh"), ("sonnet", "high"), ("haiku", None)],
)
def test_effort_derived_from_model(
    tmp_path: Path, model: str, expected_effort: str | None
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "total_cost_usd": 0.1}\n', encoding="utf-8")
    summary = derive_run_summary(path, system_prompt="p", model=model)
    assert summary is not None
    assert summary.effort == expected_effort


def test_default_model_is_opus(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "total_cost_usd": 0.1}\n', encoding="utf-8")
    summary = derive_run_summary(path, system_prompt="p")
    assert summary is not None
    assert summary.model == "opus"
    assert summary.effort == "xhigh"


def test_fixture_matches_expected_canonical_totals() -> None:
    summary = derive_run_summary(_FIXTURE, system_prompt="canonical")
    assert summary is not None
    assert summary.total_cost_usd == pytest.approx(0.0017)
    assert summary.total_output_tokens == 490
    assert summary.total_input_tokens == 320
    assert summary.tool_counts == [
        ToolUseCount(name="Read", count=3),
        ToolUseCount(name="Bash", count=1),
        ToolUseCount(name="Grep", count=1),
    ]


def test_fixture_derivation_is_deterministic_across_repeated_calls() -> None:
    first = derive_run_summary(_FIXTURE, system_prompt="canonical")
    second = derive_run_summary(_FIXTURE, system_prompt="canonical")
    assert first == second


def test_placeholder_has_zeroed_figures_and_passed_through_setup() -> None:
    placeholder = run_summary_placeholder(
        model="opus", effort="xhigh", system_prompt="you are alan"
    )
    assert placeholder.model == "opus"
    assert placeholder.effort == "xhigh"
    assert placeholder.system_prompt == "you are alan"
    assert placeholder.total_cost_usd == 0
    assert placeholder.total_output_tokens == 0
    assert placeholder.total_input_tokens == 0
    assert placeholder.tool_counts == []


@pytest.mark.parametrize(
    ("model", "expected_effort"),
    [("opus", "xhigh"), ("sonnet", "high"), ("haiku", None)],
)
def test_placeholder_effort_matches_derive_run_summary(
    tmp_path: Path, model: str, expected_effort: str | None
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text('{"type": "result", "total_cost_usd": 0.1}\n', encoding="utf-8")
    derived = derive_run_summary(path, system_prompt="p", model=model)
    assert derived is not None
    placeholder = run_summary_placeholder(
        model=model, effort=_effort_for_model(model), system_prompt="p"
    )
    assert placeholder.effort == expected_effort
    assert placeholder.effort == derived.effort


@pytest.mark.parametrize(
    ("name", "input_obj", "expected"),
    [
        ("Read", {"file_path": "alan/foo.py"}, "Read: alan/foo.py"),
        ("Write", {"file_path": "alan/foo.py"}, "Write: alan/foo.py"),
        ("Edit", {"file_path": "alan/foo.py"}, "Edit: alan/foo.py"),
        ("Bash", {"command": "uv run pytest"}, "Bash: uv run pytest"),
        ("Grep", {"pattern": "foo"}, "Grep: foo"),
        ("Glob", {"pattern": "**/*.py"}, "Glob: **/*.py"),
        ("WebFetch", {"url": "https://example.com"}, "WebFetch: https://example.com"),
        ("WebSearch", {"query": "alan"}, "WebSearch: alan"),
    ],
)
def test_tool_use_label_per_mapped_tool(
    name: str, input_obj: dict[str, str], expected: str
) -> None:
    assert tool_use_label(name, input_obj) == expected


def test_tool_use_label_truncates_at_80_chars_with_no_ellipsis() -> None:
    long_path = "a" * 200
    label = tool_use_label("Read", {"file_path": long_path})
    assert label == f"Read: {'a' * 80}"


def test_tool_use_label_unmapped_tool_falls_back_to_bare_name() -> None:
    assert tool_use_label("TodoWrite", {"todos": []}) == "TodoWrite"


def test_tool_use_label_mapped_tool_missing_key_falls_back_to_bare_name() -> None:
    assert tool_use_label("Read", {}) == "Read"


def test_tool_use_label_mapped_tool_none_input_falls_back_to_bare_name() -> None:
    assert tool_use_label("Read", None) == "Read"


def test_tool_use_label_mapped_tool_non_string_value_falls_back_to_bare_name() -> None:
    assert tool_use_label("Bash", {"command": 5}) == "Bash"


def test_tool_use_event_label_returns_first_tool_use_block() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    assert tool_use_event_label(event) == "Read: a.py"


def test_tool_use_event_label_non_string_name_is_question_mark() -> None:
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": 5}]},
    }
    assert tool_use_event_label(event) == "?"


def test_tool_use_event_label_no_tool_use_block_is_none() -> None:
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi"}]},
    }
    assert tool_use_event_label(event) is None


def test_tool_use_event_label_non_assistant_event_is_none() -> None:
    assert tool_use_event_label({"type": "result"}) is None


def test_tool_use_event_label_non_dict_input_falls_back_to_bare_name() -> None:
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Read", "input": "not-a-dict"}]},
    }
    assert tool_use_event_label(event) == "Read"


def test_tool_use_event_label_skips_non_dict_blocks() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                1,
                "not-a-dict-block",
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    assert tool_use_event_label(event) == "Bash: ls"

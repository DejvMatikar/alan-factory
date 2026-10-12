from __future__ import annotations

import ast
from pathlib import Path

import pytest

from alan import launch_spec
from alan.agent_key import BUILD_CHAT, COLLISION
from alan.claude_cli_grants import ACCEPT_EDITS_MODE, WRITE_TOOLS
from alan.launch_spec import (
    LABEL_SYSTEM_PROMPT,
    LABEL_TURN_BUDGET,
    TURN_BUDGET_PREFIX,
    LaunchSpec,
    PromptSegment,
)

_ALAN_ROOT = Path(__file__).resolve().parent.parent / "alan"


def _python_sources() -> list[Path]:
    return [
        path
        for path in _ALAN_ROOT.rglob("*.py")
        if "web" not in path.relative_to(_ALAN_ROOT).parts
    ]


def test_with_process_replaces_only_the_process() -> None:
    base = LaunchSpec(layout="agent", model="opus", add_dirs=("/a",))
    process = launch_spec.ProcessSpec(cwd="/checkout", fleet=True)
    updated = launch_spec.with_process(base, process)
    assert updated.process == process
    assert updated == LaunchSpec(layout="agent", model="opus", add_dirs=("/a",), process=process)
    assert base.process == launch_spec.ProcessSpec()


def test_with_add_dirs_replaces_only_the_added_directories() -> None:
    base = LaunchSpec(layout="operator", add_dirs=("/a",))
    updated = launch_spec.with_add_dirs(base, ["/b", "/c"])
    assert updated == LaunchSpec(layout="operator", add_dirs=("/b", "/c"))


@pytest.mark.parametrize(
    ("model", "expected"),
    [("opus", "xhigh"), ("sonnet", "high"), ("haiku", None), ("none", None), ("", None)],
)
def test_effort_for_model_maps_known_models(model: str, expected: str | None) -> None:
    assert launch_spec.effort_for_model(model) == expected


def test_resolve_effort_prefers_the_explicit_value() -> None:
    assert launch_spec.resolve_effort("low", "opus") == "low"
    assert launch_spec.resolve_effort(None, "opus") == "xhigh"
    assert launch_spec.resolve_effort(None, "haiku") is None


def test_split_turn_budget_separates_the_budget_tail() -> None:
    segments = launch_spec.split_turn_budget(f"body{TURN_BUDGET_PREFIX}150")
    assert segments == (
        PromptSegment(LABEL_SYSTEM_PROMPT, "body"),
        PromptSegment(LABEL_TURN_BUDGET, f"{TURN_BUDGET_PREFIX}150"),
    )


def test_split_turn_budget_leaves_a_plain_prompt_whole() -> None:
    assert launch_spec.split_turn_budget("body\n\nTurn budget for this run: many") == (
        PromptSegment(LABEL_SYSTEM_PROMPT, "body\n\nTurn budget for this run: many"),
    )


def test_appended_prompt_is_none_without_segments_and_joins_otherwise() -> None:
    assert launch_spec.appended_prompt(LaunchSpec(layout="operator")) is None
    spec = LaunchSpec(
        layout="agent", prompt=(PromptSegment("a", "one "), PromptSegment("b", "two"))
    )
    assert launch_spec.appended_prompt(spec) == "one two"


def test_effective_allowed_tools_narrows_unless_accept_edits() -> None:
    tools = ["Read", "Bash(uv run:*)", "Bash(npx:*)", "Bash(npm --prefix:*)"]
    assert launch_spec.effective_allowed_tools(tools, "auto") == ("Read",)
    assert launch_spec.effective_allowed_tools(tools, ACCEPT_EDITS_MODE) == tuple(tools)
    assert launch_spec.effective_allowed_tools(None, "auto") == ()


def test_flat_denied_tools_expands_the_joined_write_token() -> None:
    joined = " ".join(WRITE_TOOLS)
    assert launch_spec.flat_denied_tools([joined, "Bash(gh auth:*)"]) == (
        *WRITE_TOOLS,
        "Bash(gh auth:*)",
    )


def test_flat_denied_tools_splits_joined_rules_but_keeps_spaces_inside_parentheses() -> None:
    joined = "Bash(gh api:*) Bash(rm -rf:*) NotebookEdit"
    assert launch_spec.flat_denied_tools([joined, "Read(//data/a b/c)", "*"]) == (
        "Bash(gh api:*)",
        "Bash(rm -rf:*)",
        "NotebookEdit",
        "Read(//data/a b/c)",
        "*",
    )


def _agent_spec(**overrides: object) -> LaunchSpec:
    base: dict[str, object] = {"output_format": "stream-json", "max_turns": 9}
    prompt = (PromptSegment(LABEL_SYSTEM_PROMPT, "p"),)
    return launch_spec.agent_spec(prompt, **{**base, **overrides})  # type: ignore[arg-type]


def test_agent_spec_records_the_requested_and_effective_grants() -> None:
    spec = _agent_spec(
        allowed_tools=["Read", "Bash(uv run:*)"], add_dirs=["/x"], effort="high", agent=COLLISION
    )
    assert spec.layout == "agent"
    assert spec.agent == COLLISION
    assert spec.requested_tools == ("Read", "Bash(uv run:*)")
    assert spec.allowed_tools == ("Read",)
    assert spec.add_dirs == ("/x",)
    assert spec.effort == "high"
    assert spec.permission_mode == "auto"
    assert spec.denied[0] == " ".join(WRITE_TOOLS)


def test_agent_spec_deny_all_tools_drops_grants_and_denies_everything() -> None:
    spec = _agent_spec(allowed_tools=["Read"], add_dirs=["/x"], deny_all_tools=True)
    assert spec.deny_all_tools is True
    assert spec.allowed_tools == ()
    assert spec.add_dirs == ()
    assert spec.denied == ("*",)
    assert spec.requested_tools == ("Read",)


def test_agent_spec_accept_edits_keeps_write_tools_allowed() -> None:
    spec = _agent_spec(permission_mode=ACCEPT_EDITS_MODE, allowed_tools=["Bash(uv run:*)"])
    assert spec.allowed_tools == ("Bash(uv run:*)",)
    assert " ".join(WRITE_TOOLS) not in spec.denied


@pytest.mark.parametrize(
    ("session_id", "resume_session_id", "mode", "expected_id"),
    [
        (None, None, "none", None),
        ("new-id", None, "new", "new-id"),
        (None, "old-id", "resumed", "old-id"),
        ("new-id", "old-id", "resumed", "old-id"),
    ],
)
def test_agent_spec_session_mode(
    session_id: str | None, resume_session_id: str | None, mode: str, expected_id: str | None
) -> None:
    spec = _agent_spec(session_id=session_id, resume_session_id=resume_session_id)
    assert spec.session_mode == mode
    assert spec.session_id == expected_id


@pytest.mark.parametrize(("resume", "mode"), [(True, "resumed"), (False, "new")])
def test_chat_spec_session_and_defaults(resume: bool, mode: str) -> None:
    spec = launch_spec.chat_spec(
        model="opus",
        session_id="sid",
        resume=resume,
        max_turns=80,
        allowed_tools=["Read", "Bash(npx:*)"],
        system_prompt="chat prompt",
        effort="xhigh",
        agent=BUILD_CHAT,
    )
    assert spec.layout == "chat"
    assert spec.session_mode == mode
    assert spec.session_id == "sid"
    assert spec.allowed_tools == ("Read",)
    assert spec.requested_tools == ("Read", "Bash(npx:*)")
    assert spec.permission_mode == "auto"
    assert launch_spec.appended_prompt(spec) == "chat prompt"
    assert spec.agent == BUILD_CHAT


def test_to_argv_matches_each_layout_head() -> None:
    assert launch_spec.to_argv(_agent_spec())[:2] == ["claude", "-p"]
    assert launch_spec.to_argv(LaunchSpec(layout="operator"))[0] == "claude"


def test_to_argv_effective_allowed_tools_match_the_spec() -> None:
    spec = _agent_spec(allowed_tools=["Read", "Bash(uv run:*)", "Bash(git log:*)"])
    argv = launch_spec.to_argv(spec)
    start = argv.index("--allowedTools") + 1
    assert tuple(argv[start : start + len(spec.allowed_tools)]) == spec.allowed_tools


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_the_claude_literal_and_append_flag_live_only_in_launch_spec() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        if path.name == "launch_spec.py":
            continue
        for value in _string_constants(path):
            if value == "claude" or "--append-system-prompt" in value:
                offenders.append(f"{path.relative_to(_ALAN_ROOT)}: {value!r}")
    assert offenders == []

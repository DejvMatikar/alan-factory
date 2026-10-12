from __future__ import annotations

import pytest

from alan import claude_cli
from alan.behavioral_eval import _JUDGE_SYSTEM_PROMPT as PLANNER_GRADER_PROMPT
from alan.claude_cli import _HEADLESS_EXECUTION_BLOCK
from alan.claude_cli_grants import NO_TOOLS_BLOCK
from alan.collision import _SYS as COLLISION_PROMPT
from alan.compression import _COMPACTOR_SYSTEM_PROMPT
from alan.manager_summary import _SYSTEM_PROMPT as MANAGER_SUMMARY_PROMPT
from alan.teams.verification import judge_system_prompt

_TOOL_DENIED_CALLER_PROMPTS = {
    "compression": _COMPACTOR_SYSTEM_PROMPT,
    "collision": COLLISION_PROMPT,
    "manager_summary": MANAGER_SUMMARY_PROMPT,
    "judge_eval": judge_system_prompt(),
    "behavioral_eval": PLANNER_GRADER_PROMPT,
}


@pytest.fixture(autouse=True)
def _project_doc_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_cli, "_project_doc_block", lambda: "PROJECT_DOC_SENTINEL")


def _append_arg(system_prompt: str, *, deny_all_tools: bool) -> str:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        system_prompt, output_format="json", max_turns=1, deny_all_tools=deny_all_tools
    )
    return cmd[cmd.index("--append-system-prompt") + 1]


def test_block_states_the_agent_has_no_tools() -> None:
    assert "You have no tools" in NO_TOOLS_BLOCK


def test_block_forbids_calling_attempting_describing_or_simulating_a_tool_call() -> None:
    for verb in ("call", "attempt", "describe", "simulate"):
        assert verb in NO_TOOLS_BLOCK.split("tool call")[0]


def test_block_forbids_writing_tool_call_or_tool_result_blocks() -> None:
    assert "tool-call or tool-result block" in NO_TOOLS_BLOCK


def test_block_says_everything_needed_is_already_in_the_prompt() -> None:
    assert "Everything you need is already in this prompt" in NO_TOOLS_BLOCK


def test_block_requires_every_mandatory_machine_readable_line() -> None:
    assert "still emit every mandatory machine-readable line" in NO_TOOLS_BLOCK


def test_absent_evidence_sentence_is_strictly_conditional() -> None:
    absent = NO_TOOLS_BLOCK.index("evidence is absent from this prompt")
    assert NO_TOOLS_BLOCK.index("Only if your role's output contract asks for") < absent
    assert NO_TOOLS_BLOCK.count("evidence is absent") == 1


def test_block_carves_out_verbatim_transforms_and_short_summaries() -> None:
    tail = NO_TOOLS_BLOCK.split("If your role is to ")[1]
    assert tail.startswith("reproduce text verbatim or write a short summary")
    assert "do not mention missing evidence or missing tools at all" in tail


def test_deny_all_append_starts_with_the_bare_caller_prompt() -> None:
    assert _append_arg("caller prompt", deny_all_tools=True).startswith("caller prompt")


def test_deny_all_append_contains_the_block_exactly_once() -> None:
    assert _append_arg("caller prompt", deny_all_tools=True).count(NO_TOOLS_BLOCK) == 1


def test_deny_all_append_omits_headless_and_project_doc_blocks() -> None:
    appended = _append_arg("caller prompt", deny_all_tools=True)
    assert _HEADLESS_EXECUTION_BLOCK not in appended
    assert "PROJECT_DOC_SENTINEL" not in appended


def test_tool_enabled_append_is_unchanged_and_block_free() -> None:
    appended = _append_arg("caller prompt", deny_all_tools=False)
    assert appended == f"caller prompt\n\n{_HEADLESS_EXECUTION_BLOCK}PROJECT_DOC_SENTINEL"
    assert NO_TOOLS_BLOCK not in appended


@pytest.mark.parametrize("caller", sorted(_TOOL_DENIED_CALLER_PROMPTS))
def test_every_tool_denied_caller_prompt_gets_the_shared_block(caller: str) -> None:
    prompt = _TOOL_DENIED_CALLER_PROMPTS[caller]
    appended = _append_arg(prompt, deny_all_tools=True)
    assert appended.startswith(prompt)
    assert appended.count(NO_TOOLS_BLOCK) == 1
    assert _HEADLESS_EXECUTION_BLOCK not in appended
    assert "PROJECT_DOC_SENTINEL" not in appended

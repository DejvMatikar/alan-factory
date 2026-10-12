from __future__ import annotations

from alan.claude_cli import (
    _DEFAULT_CHAT_ALLOWED_TOOLS,
    CONSULTANTS,
    format_build_system_prompt,
    format_explore_system_prompt,
)
from alan.claude_cli_grants import CHAT_ISSUE_READ_TOOL, CHAT_ISSUE_SCOPE_CLAUSE

_ISSUE_NON_READ_VERBS = (
    "gh issue list",
    "gh issue create",
    "gh issue comment",
    "gh issue edit",
)


def test_default_chat_allowed_tools_grants_issue_read():
    assert CHAT_ISSUE_READ_TOOL in _DEFAULT_CHAT_ALLOWED_TOOLS
    assert "Bash(gh pr view:*)" in _DEFAULT_CHAT_ALLOWED_TOOLS


def test_no_issue_write_or_list_verb_in_default_chat_allowlist():
    joined = " ".join(_DEFAULT_CHAT_ALLOWED_TOOLS)
    for verb in _ISSUE_NON_READ_VERBS:
        assert verb not in joined


def test_build_prompt_advertises_issue_read_and_scope_clause():
    prompt = format_build_system_prompt()
    assert "gh issue view" in prompt
    assert CHAT_ISSUE_SCOPE_CLAUSE in prompt
    for verb in _ISSUE_NON_READ_VERBS:
        assert verb not in prompt


def test_explore_prompt_advertises_issue_read_and_scope_clause_for_every_consultant():
    for consultant in CONSULTANTS:
        prompt = format_explore_system_prompt(consultant.key)
        assert "gh issue view" in prompt
        assert CHAT_ISSUE_SCOPE_CLAUSE in prompt
        for verb in _ISSUE_NON_READ_VERBS:
            assert verb not in prompt


def test_scope_clause_reads_same_repo_pasted_url_in_same_turn():
    assert "pasted issue URL" in CHAT_ISSUE_SCOPE_CLAUSE
    assert "gh issue view <number>" in CHAT_ISSUE_SCOPE_CLAUSE
    assert "this same turn" in CHAT_ISSUE_SCOPE_CLAUSE

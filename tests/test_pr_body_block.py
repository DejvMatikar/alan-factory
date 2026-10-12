from __future__ import annotations

import re

from alan.teams import _pr_body_block as pb

_LEGACY_AUTHORED_BODY_RE = re.compile(
    r"(?m)^(`{3,})pr_body[ \t]*\r?\n(.*?)\r?\n\1[ \t]*$", re.DOTALL
)


def _legacy_extract(synthesis: str) -> str | None:
    match = _LEGACY_AUTHORED_BODY_RE.search(synthesis)
    if match is None:
        return None
    body = match.group(2)
    return body if body.strip() else None


def _extract(synthesis: str) -> str | None:
    result = pb.extract_pr_body_block(synthesis)
    return result.body if result is not None else None


def test_no_inner_fences_returns_full_body() -> None:
    synthesis = "## Summary\nDid the thing.\n\n```pr_body\n## Why\nExplains it.\n```\n"
    assert _extract(synthesis) == "## Why\nExplains it."


def test_returns_none_when_no_opener_present() -> None:
    assert pb.extract_pr_body_block("## Summary\nNo block here.") is None


def test_returns_none_for_blank_body() -> None:
    assert pb.extract_pr_body_block("```pr_body\n   \n```") is None


def test_returns_none_when_unterminated() -> None:
    assert pb.extract_pr_body_block("```pr_body\nno closing fence here") is None


def test_widened_outer_fence_with_backtick_run_inside_body() -> None:
    synthesis = "````pr_body\nbody containing a ```fenced``` code block\n````"
    assert _extract(synthesis) == "body containing a ```fenced``` code block"


def test_bare_opened_inner_block_publishes_whole_body() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "example:\n"
        "```\n"
        "code line\n"
        "```\n"
        "Fixes #9001\n"
        "```\n"
    )
    assert _extract(synthesis) == (
        "## Why\nexample:\n```\ncode line\n```\nFixes #9001"
    )


def test_info_opened_bare_closed_inner_block_publishes_whole_body() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "```yaml\n"
        "key: value\n"
        "```\n"
        "More text after.\n"
        "```\n"
    )
    assert _extract(synthesis) == (
        "## Why\n```yaml\nkey: value\n```\nMore text after."
    )


def test_mixed_bare_and_info_inner_blocks() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "```yaml\n"
        "key: value\n"
        "```\n"
        "```\n"
        "bare example\n"
        "```\n"
        "Fixes #9002\n"
        "```\n"
    )
    assert _extract(synthesis) == (
        "## Why\n```yaml\nkey: value\n```\n```\nbare example\n```\nFixes #9002"
    )


def test_body_followed_by_host_parsed_yaml_blocks_is_not_swallowed() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "Simple.\n"
        "```\n"
        "\n"
        "```yaml decisions.yaml\n"
        "id: x\n"
        "```\n"
    )
    assert _extract(synthesis) == "## Why\nSimple."


def test_unterminated_info_fence_swallows_to_eof_and_leaves_no_close() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "```yaml\n"
        "key: value with no closer at all\n"
    )
    assert pb.extract_pr_body_block(synthesis) is None


def test_info_fence_closer_must_be_at_least_as_long() -> None:
    synthesis = (
        "````pr_body\n"
        "## Why\n"
        "````yaml\n"
        "key: value\n"
        "```\n"
        "still inside the inner block\n"
        "````\n"
        "````\n"
    )
    assert _extract(synthesis) == (
        "## Why\n````yaml\nkey: value\n```\nstill inside the inner block\n````"
    )


def test_alternative_lengths_records_earlier_qualifying_candidate() -> None:
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "```\n"
        "code line\n"
        "```\n"
        "Fixes #9001\n"
        "```\n"
    )
    result = pb.extract_pr_body_block(synthesis)
    assert result is not None
    assert result.alternative_lengths == (len("## Why"),)


def test_no_ambiguity_records_no_alternatives() -> None:
    synthesis = "```pr_body\n## Why\nExplains it.\n```\n"
    result = pb.extract_pr_body_block(synthesis)
    assert result is not None
    assert result.alternative_lengths == ()


def test_multiple_pr_body_openers_falls_through_to_next_valid_one() -> None:
    synthesis = (
        "```pr_body\n"
        "abandoned attempt with no close\n"
        "```pr_body\n"
        "## Why\n"
        "the real one.\n"
        "```\n"
    )
    result = pb.extract_pr_body_block(synthesis)
    assert result is not None
    assert "the real one." in result.body


def test_parity_no_inner_fences_matches_legacy() -> None:
    synthesis = "## Summary\nDid the thing.\n\n```pr_body\n## Why\nExplains it.\n```\n"
    assert _extract(synthesis) == _legacy_extract(synthesis)


def test_parity_widened_outer_fence_matches_legacy() -> None:
    synthesis = "````pr_body\nbody containing a ```fenced``` code block\n````"
    assert _extract(synthesis) == _legacy_extract(synthesis)


def test_parity_blank_body_matches_legacy() -> None:
    synthesis = "```pr_body\n   \n```"
    assert _extract(synthesis) == _legacy_extract(synthesis)


def test_parity_unterminated_matches_legacy() -> None:
    synthesis = "```pr_body\nno closing fence here"
    assert _extract(synthesis) == _legacy_extract(synthesis)


def test_parity_absent_matches_legacy() -> None:
    synthesis = "## Summary\nNo block here."
    assert _extract(synthesis) == _legacy_extract(synthesis)


def test_scan_markdown_classifies_bare_fence() -> None:
    state = pb.scan_markdown("```\n")
    assert len(state.fences) == 1
    assert state.fences[0].is_bare is True
    assert state.fences[0].fence_len == 3


def test_scan_markdown_classifies_info_fence() -> None:
    state = pb.scan_markdown("```yaml decisions.yaml\n")
    assert len(state.fences) == 1
    assert state.fences[0].is_bare is False
    assert state.fences[0].fence_len == 3


def test_scan_markdown_ignores_backtick_inside_info_string() -> None:
    state = pb.scan_markdown("```py`thon\n")
    assert state.fences == ()


def test_scan_markdown_ignores_indented_backticks() -> None:
    state = pb.scan_markdown("  ```\n")
    assert state.fences == ()


def test_scan_markdown_bare_fence_with_trailing_whitespace() -> None:
    state = pb.scan_markdown("```   \n")
    assert len(state.fences) == 1
    assert state.fences[0].is_bare is True

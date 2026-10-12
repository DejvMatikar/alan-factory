from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from alan import forge_gh, process_runner
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _forge_errors, _pr_body
from alan.teams._pr_forge import GH_TIMEOUT_S
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, pr_read

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
)


def test_budget_is_the_roadmap_mandated_constant() -> None:
    assert _pr_body.PR_BODY_MAX_CHARS == 60000


def test_build_pr_body_empty_input_returns_empty() -> None:
    assert _pr_body.build_pr_body("") == ""


def test_build_pr_body_under_budget_returns_unchanged() -> None:
    text = "## Summary\nShort delivery record, well under budget."
    assert _pr_body.build_pr_body(text) == text


def test_normal_multi_section_body_cuts_at_last_heading_before_budget() -> None:
    section_one = "## Section One\n" + ("a" * 200)
    section_two = "\n\n## Section Two\n" + ("b" * 80000)
    body = section_one + section_two

    result = _pr_body.build_pr_body(body)

    assert result.startswith("## Section One")
    assert "Section Two" not in result
    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS


def test_no_headings_at_all_falls_back_to_word_cut() -> None:
    body = "word " * 20000

    result = _pr_body.build_pr_body(body)

    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    prefix = result[: -len(_pr_body._TRUNCATION_MARKER)]  # noqa: SLF001
    assert prefix == prefix.rstrip()
    assert prefix.endswith("word")
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS


def test_single_over_budget_section_falls_back_to_word_cut() -> None:
    body = "## Only Section\n" + ("filler word " * 20000)

    result = _pr_body.build_pr_body(body)

    assert result.startswith("## Only Section")
    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS


def test_no_word_boundary_falls_back_to_hard_slice() -> None:
    body = "z" * 90000

    result = _pr_body.build_pr_body(body)

    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS


def test_word_cut_treats_newlines_as_boundaries() -> None:
    body = "word\n" * 20000

    result = _pr_body.build_pr_body(body)

    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    prefix = result[: -len(_pr_body._TRUNCATION_MARKER)]  # noqa: SLF001
    assert prefix == prefix.rstrip()
    assert prefix.endswith("word")
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS


def test_word_cut_keeps_full_window_when_budget_lands_on_whitespace() -> None:
    budget = _pr_body.PR_BODY_MAX_CHARS - len(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    body = ("a" * budget) + " " + ("b" * 1000)

    result = _pr_body.build_pr_body(body)

    prefix = result[: -len(_pr_body._TRUNCATION_MARKER)]  # noqa: SLF001
    assert prefix == "a" * budget
    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001


def test_word_cut_backs_up_to_last_boundary_when_budget_lands_mid_word() -> None:
    budget = _pr_body.PR_BODY_MAX_CHARS - len(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    body = ("a" * (budget - 3)) + " word" + ("b" * 1000)

    result = _pr_body.build_pr_body(body)

    prefix = result[: -len(_pr_body._TRUNCATION_MARKER)]  # noqa: SLF001
    assert prefix == "a" * (budget - 3)
    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001


def test_preamble_single_newline_gap_before_heading_survives() -> None:
    body = (
        "Stopping here per instructions.\n"
        "## Summary\nImplemented the auth fix.\n\n"
        "## Decisions\n- used bcrypt\n"
    )

    result = _pr_body.build_pr_body(body)

    assert result == (
        "## Summary\nImplemented the auth fix.\n\n"
        "## Decisions\n- used bcrypt\n"
    )


def test_preamble_stripped_when_leading_and_followed_by_real_content() -> None:
    body = (
        "Stopping here per instructions to hand off my synthesis mid-way.\n\n"
        "## Actual work\nStuff got done here."
    )

    result = _pr_body.build_pr_body(body)

    assert result == "## Actual work\nStuff got done here."


def test_preamble_not_stripped_when_it_would_empty_the_body() -> None:
    body = "Stopping here per instructions."

    assert _pr_body.build_pr_body(body) == body


def test_preamble_phrase_further_down_survives_byte_identical() -> None:
    body = (
        "## Summary\nEverything requested is done.\n\n"
        "Stopping here per instructions to hand back to the reviewer.\n\n"
        "## Decisions\n- picked json storage"
    )

    assert _pr_body.build_pr_body(body) == body


def test_legitimate_opening_line_survives_byte_identical() -> None:
    body = "## Summary\nActual delivered work here, not a handover preamble."

    assert _pr_body.build_pr_body(body) == body


def test_pr_body_block_instructions_names_public_body_and_audience() -> None:
    text = _pr_body.PR_BODY_BLOCK_INSTRUCTIONS
    assert "public pull request body" in text.lower() or "PUBLIC pull request" in text
    assert "DIFFERENT artifact" in text
    assert "## Summary" in text


def test_pr_body_block_instructions_names_fence_budget_and_fallback() -> None:
    text = _pr_body.PR_BODY_BLOCK_INSTRUCTIONS
    assert "```pr_body" in text
    assert str(_pr_body.PR_BODY_MAX_CHARS) in text
    assert "Omitting this block" in text
    assert "REPLACES the live PR body wholesale" in text


def test_extract_authored_body_returns_none_when_absent() -> None:
    assert _pr_body.extract_authored_body("## Summary\nNo block here.") is None


def test_extract_authored_body_extracts_fenced_content() -> None:
    synthesis = (
        "## Summary\nDid the thing.\n\n"
        "```pr_body\n"
        "## Why\nFixes the login race.\n"
        "```\n"
    )
    assert _pr_body.extract_authored_body(synthesis) == "## Why\nFixes the login race."


def test_extract_authored_body_returns_none_for_blank_body() -> None:
    synthesis = "```pr_body\n   \n```"
    assert _pr_body.extract_authored_body(synthesis) is None


def test_extract_authored_body_returns_none_when_unterminated() -> None:
    synthesis = "```pr_body\nunterminated body with no closing fence"
    assert _pr_body.extract_authored_body(synthesis) is None


def test_extract_authored_body_handles_backtick_run_inside_body() -> None:
    synthesis = (
        "````pr_body\n"
        "body containing a ```fenced``` code block\n"
        "````"
    )
    assert (
        _pr_body.extract_authored_body(synthesis)
        == "body containing a ```fenced``` code block"
    )


_LEGACY_AUTHORED_BODY_RE = re.compile(
    r"(?m)^(`{3,})pr_body[ \t]*\r?\n(.*?)\r?\n\1[ \t]*$", re.DOTALL
)


def _legacy_extract_authored_body(synthesis: str) -> str | None:
    match = _LEGACY_AUTHORED_BODY_RE.search(synthesis)
    if match is None:
        return None
    body = match.group(2)
    return body if body.strip() else None


@pytest.mark.parametrize(
    "synthesis",
    [
        "## Summary\nDid the thing.\n\n```pr_body\n## Why\nExplains it.\n```\n",
        "````pr_body\nbody containing a ```fenced``` code block\n````",
        "```pr_body\n   \n```",
        "```pr_body\nunterminated body with no closing fence",
        "## Summary\nNo block here.",
        "```pr_body\r\n## Why\r\nExplains it.\r\n```",
        "```pr_body\n```",
    ],
)
def test_extract_authored_body_matches_legacy_regex(synthesis: str) -> None:
    assert _pr_body.extract_authored_body(synthesis) == _legacy_extract_authored_body(synthesis)


def test_authored_body_falls_back_to_synthesis_when_no_block() -> None:
    synthesis = "## Summary\nDelivered without an authored body block."
    result = _pr_body.build_pr_body(_pr_body.extract_authored_body(synthesis) or synthesis)
    assert result == _pr_body.build_pr_body(synthesis)


def test_authored_body_uses_extracted_block_and_normalizes() -> None:
    synthesis = "## Summary\nDid the thing.\n\n```pr_body\n## Why\nExplains it.\n```\n"
    result = _pr_body.build_pr_body(_pr_body.extract_authored_body(synthesis) or synthesis)
    assert result == "## Why\nExplains it."


def test_build_pr_body_reserve_zero_is_byte_identical_to_default() -> None:
    text = "## Summary\n" + ("word " * 20000)
    assert _pr_body.build_pr_body(text, reserve=0) == _pr_body.build_pr_body(text)


def test_build_pr_body_reserve_shrinks_the_effective_budget() -> None:
    text = "a" * _pr_body.PR_BODY_MAX_CHARS
    result = _pr_body.build_pr_body(text, reserve=500)
    assert len(result) <= _pr_body.PR_BODY_MAX_CHARS - 500
    assert result.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001


def test_build_pr_body_reserve_leaves_untruncated_text_untouched() -> None:
    text = "## Summary\nShort body well under any reasonable reserve."
    assert _pr_body.build_pr_body(text, reserve=1000) == text


def test_truncate_with_marker_large_reserve_does_not_index_error_on_short_body() -> None:
    text = "a" * 20
    result = _pr_body.build_pr_body(text, reserve=_pr_body.PR_BODY_MAX_CHARS - 10)
    assert len(result) <= 10


def test_truncate_with_marker_reserve_consuming_entire_budget_returns_empty_result() -> None:
    text = "some short body text"
    result = _pr_body.build_pr_body(text, reserve=_pr_body.PR_BODY_MAX_CHARS)
    assert result == ""


def test_fetch_pr_body_returns_the_body_string_read_through_the_port(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({"body": "## Existing body"}))
    assert _pr_body.fetch_pr_body(42, forge_target=_TARGET) == "## Existing body"
    assert _active_fake_forge.calls == [
        ("read_pr", (42, ("body",)), {"forge_target": _TARGET, "timeout": GH_TIMEOUT_S}),
    ]


@pytest.mark.parametrize(
    "outcome",
    [
        pr_read(returncode=1, stderr="boom"),
        pr_read({}),
        pr_read({"body": 42}),
        pr_read({"body": None}),
        FileNotFoundError("gh not installed"),
        subprocess.TimeoutExpired(cmd=["gh"], timeout=1),
    ],
    ids=["nonzero", "missing-key", "non-string", "null", "missing-binary", "timeout"],
)
def test_fetch_pr_body_returns_none_when_the_read_is_unusable(
    outcome: object, _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", outcome)
    assert _pr_body.fetch_pr_body(42, forge_target=_TARGET) is None


def test_amend_pr_body_success_appends_untruncated_audit_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    always(_active_fake_forge, "read_pr", pr_read({"body": "old body"}))
    always(_active_fake_forge, "update_pr_body", api_result())
    new_body = "x" * 900
    result = _pr_body.amend_pr_body(
        42, new_body, forge_target=_TARGET, task_id="task-x", team="implementation_fix",
    )
    assert result.ok is True
    assert result.previous_body == "old body"
    assert result.new_body == new_body
    assert [name for name, _args, _kwargs in _active_fake_forge.calls] == [
        "read_pr", "update_pr_body",
    ]
    assert _active_fake_forge.calls[1] == (
        "update_pr_body", (42, new_body), {"forge_target": _TARGET},
    )
    entries = [
        json.loads(line)
        for line in (tmp_path / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "pr_body_amend_ok")
    assert row["previous_body"] == "old body"
    assert row["new_body"] == new_body
    assert len(row["new_body"]) == 900


@pytest.mark.parametrize(
    "fault",
    [FileNotFoundError("gh not installed"), subprocess.TimeoutExpired(cmd=["gh"], timeout=1)],
    ids=["missing-binary", "timeout"],
)
def test_amend_pr_body_raises_forge_transient_on_a_transport_fault(
    fault: BaseException, _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({"body": "old"}))
    always(_active_fake_forge, "update_pr_body", fault)
    with pytest.raises(_forge_errors.ForgeTransientError) as raised:
        _pr_body.amend_pr_body(
            42, "new body", forge_target=_TARGET, task_id="task-x", team="implementation_fix",
        )
    assert str(raised.value) == f"gh pr body amend raised {fault}"
    assert raised.value.__cause__ is fault


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("dial tcp: connection refused", _forge_errors.ForgeTransientError),
        ("API rate limit exceeded", _forge_errors.ForgeRateLimited),
    ],
)
def test_amend_pr_body_classifies_an_infrastructure_failure_before_reporting_not_ok(
    stderr: str, expected: type[Exception], _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({"body": "old"}))
    always(_active_fake_forge, "update_pr_body", api_result(returncode=1, stderr=stderr))
    with pytest.raises(expected):
        _pr_body.amend_pr_body(
            42, "new body", forge_target=_TARGET, task_id="task-x", team="implementation_fix",
        )


def test_amend_pr_body_reports_not_ok_with_stderr_cut_to_500_characters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    always(_active_fake_forge, "read_pr", pr_read({"body": "old"}))
    always(_active_fake_forge, "update_pr_body", api_result(returncode=1, stderr="e" * 600))
    result = _pr_body.amend_pr_body(
        42, "new body", forge_target=_TARGET, task_id="task-x", team="implementation_fix",
    )
    assert result.ok is False
    assert result.previous_body == "old"
    assert result.new_body == "new body"
    assert result.error == "e" * 500
    assert not (tmp_path / "impl_audit.jsonl").exists()


def test_run_gh_api_json_input_posts_payload_via_input_file_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        input_path = cmd[cmd.index("--input") + 1]
        captured["input_path"] = input_path
        captured["payload"] = json.loads(Path(input_path).read_text())
        captured["path_exists_during_call"] = Path(input_path).exists()
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    result = forge_gh.run_gh_api_json_input(
        ["gh", "api", "-X", "PATCH", "/repos/acme/widget/pulls/1"], {"body": "hello"},
        forge_target=_TARGET,
    )
    assert result.returncode == 0
    assert captured["payload"] == {"body": "hello"}
    assert captured["path_exists_during_call"] is True
    assert not Path(str(captured["input_path"])).exists()

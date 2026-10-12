from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _forge_errors, _pr_body, _pr_forge
from alan.teams import _pr_closing as pc
from alan.teams._pr_handover import (  # pyright: ignore[reportPrivateUsage]
    _SALVAGE_IDENTITY_EMAIL,
    _SALVAGE_IDENTITY_NAME,
)
from alan.workspace import Task, TaskStatus, Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, pr_read

_SECONDARY_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)  # noqa: S603
    (repo / "README.md").write_text("seed\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )


def _base_sha(repo: Path) -> str:
    return subprocess.run(  # noqa: S603,S607
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()


def _commit(
    repo: Path, filename: str, message: str, *, author_name: str, author_email: str,
) -> None:
    (repo / filename).write_text("x\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", filename], cwd=str(repo), check=True, capture_output=True
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
    }
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", message], cwd=str(repo), check=True,
        capture_output=True, env=env,
    )


def _workspace(tmp_path: Path, description: str, *, extra_tasks: int = 0) -> Workspace:
    tasks = None
    if extra_tasks:
        tasks = [
            Task(id="root", status=TaskStatus.READY),
            *(
                Task(id=f"child-{i}", status=TaskStatus.READY)
                for i in range(extra_tasks)
            ),
        ]
    return create(
        "closing-task", description, root=tmp_path / "ws", tasks=tasks,
    )


def test_parse_delivers_trailer_absent_when_no_trailer(tmp_path: Path) -> None:
    trailer = pc.parse_delivers_trailer("Just a description with no trailer.")
    assert trailer == pc.Trailer(issues=(), malformed=False)


def test_parse_delivers_trailer_empty_description() -> None:
    assert pc.parse_delivers_trailer("") == pc.Trailer(issues=(), malformed=False)


def test_parse_delivers_trailer_trailing_blank_lines_still_found() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 12\n\n   \n")
    assert trailer.issues == (12,)
    assert trailer.malformed is False


def test_parse_delivers_trailer_single_issue(tmp_path: Path) -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 42")
    assert trailer.issues == (42,)
    assert trailer.malformed is False


def test_parse_delivers_trailer_multiple_issues(tmp_path: Path) -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 12, 34")
    assert trailer.issues == (12, 34)


def test_parse_delivers_trailer_deduplicates(tmp_path: Path) -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 12, 34, 12")
    assert trailer.issues == (12, 34)


def test_parse_delivers_trailer_first_line_then_prose_still_found() -> None:
    description = "Delivers-Issues: 12\n\nActually this is the real last line."
    trailer = pc.parse_delivers_trailer(description)
    assert trailer.issues == (12,)
    assert trailer.malformed is False


def test_parse_delivers_trailer_inside_fence_is_ignored() -> None:
    description = "```\nDelivers-Issues: 12\n```\n\nActually this is the real last line."
    trailer = pc.parse_delivers_trailer(description)
    assert trailer == pc.Trailer(issues=(), malformed=False)


def test_parse_delivers_trailer_inside_html_comment_is_ignored() -> None:
    description = "<!--\nDelivers-Issues: 12\n-->\n\nActually this is the real last line."
    trailer = pc.parse_delivers_trailer(description)
    assert trailer == pc.Trailer(issues=(), malformed=False)


def test_parse_delivers_trailer_duplicate_lines_last_one_wins() -> None:
    description = "Delivers-Issues: 12\n\nDelivers-Issues: 34"
    trailer = pc.parse_delivers_trailer(description)
    assert trailer.issues == (34,)


def test_parse_delivers_trailer_ignores_pr_body_shaped_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _workspace(tmp_path, "Ship it. No trailer in the brief.")
    synthesis_with_trailer = "## Summary\nDid the thing.\n\nDelivers-Issues: 99"
    body = pc.compose_pr_body(ws, synthesis_with_trailer, team="implementation", pr_number=None)
    assert "Fixes #99" not in body


def test_parse_delivers_trailer_malformed_no_numbers() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: none")
    assert trailer.malformed is True
    assert trailer.issues == ()


def test_parse_delivers_trailer_malformed_zero_is_not_positive() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 0")
    assert trailer.malformed is True


def test_parse_delivers_trailer_malformed_extra_trailing_text() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 12 and more")
    assert trailer.malformed is True


def test_parse_delivers_trailer_malformed_hash_prefix() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: #12")
    assert trailer.malformed is True


def test_parse_delivers_trailer_trailing_whitespace_tolerated() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\nDelivers-Issues: 12   \n")
    assert trailer.issues == (12,)
    assert trailer.malformed is False


def test_parse_delivers_trailer_leading_whitespace_tolerated() -> None:
    trailer = pc.parse_delivers_trailer("Ship it.\n\n  Delivers-Issues: 12")
    assert trailer.issues == (12,)
    assert trailer.malformed is False


def test_plan_pr_body_absent_returns_build_pr_body(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "No trailer at all.")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    composed = pc.plan_pr_body(ws, "## Summary\nBody text.", trailer)
    assert composed.outcome == "absent"
    assert composed.body == _pr_body.build_pr_body("## Summary\nBody text.")
    assert composed.issues == ()
    assert composed.appended == ()


def test_plan_pr_body_malformed(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: nope")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    composed = pc.plan_pr_body(ws, "## Summary\nBody text.", trailer)
    assert composed.outcome == "malformed"
    assert composed.issues == ()


def test_plan_pr_body_split_skipped_for_multi_task_workspace(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42", extra_tasks=1)
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    composed = pc.plan_pr_body(ws, "## Summary\nBody text.", trailer)
    assert composed.outcome == "split_skipped"
    assert composed.issues == (42,)
    assert composed.appended == ()
    assert "Fixes #42" not in composed.body


def test_plan_pr_body_present_when_closing_line_already_there(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\nBody text.\n\nFixes #42\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "present"
    assert composed.appended == ()
    assert composed.body.count("Fixes #42") == 1


def test_plan_pr_body_appended_when_missing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    composed = pc.plan_pr_body(ws, "## Summary\nBody text.", trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)
    assert composed.body.endswith("Fixes #42")


def test_plan_pr_body_appends_only_the_missing_issue(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 12, 34")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\nBody text.\n\nFixes #12\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (34,)
    assert "Fixes #12" in composed.body
    assert "Fixes #34" in composed.body


@pytest.mark.parametrize(
    "keyword", ["Fixes", "fixes", "FIXES", "Close", "closes", "Closed", "Resolve", "resolves"],
)
def test_closing_keyword_variants_are_recognized(tmp_path: Path, keyword: str) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = f"## Summary\nBody text.\n\n{keyword} #42\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "present"


def test_closing_line_inside_code_block_does_not_count(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n```\nFixes #42\n```\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_closing_line_inside_html_comment_does_not_count(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n<!--\nFixes #42\n-->\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_non_standalone_closing_form_does_not_count(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\nThis fixes #42 in prose.\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_appended_body_stays_within_max_chars_when_original_is_oversized(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    oversized = "## Summary\n" + ("a" * (_pr_body.PR_BODY_MAX_CHARS + 5000))
    composed = pc.plan_pr_body(ws, oversized, trailer)
    assert composed.outcome == "appended"
    assert "Fixes #42" in composed.body
    assert len(composed.body) <= _pr_body.PR_BODY_MAX_CHARS


def test_appended_body_closes_open_fence_before_appending(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n```python\nsome code without a closer"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.body.endswith("Fixes #42")
    assert "```\n\nFixes #42" in composed.body


def test_appended_body_closes_open_comment_before_appending(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n<!-- unterminated comment"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert "-->\n\nFixes #42" in composed.body


def test_appended_body_closes_open_fence_within_oversized_truncated_body(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    oversized = "## Summary\n```python\n" + ("x" * (_pr_body.PR_BODY_MAX_CHARS + 5000))
    composed = pc.plan_pr_body(ws, oversized, trailer)
    assert composed.outcome == "appended"
    assert composed.body.endswith("Fixes #42")
    assert "```\n\nFixes #42" in composed.body
    assert len(composed.body) <= _pr_body.PR_BODY_MAX_CHARS


def test_plan_pr_body_present_after_reserved_truncation_makes_line_standalone(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    marker_len = len(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001
    reserve = pc._reserve_for((42,), "")  # noqa: SLF001
    limit = _pr_body.PR_BODY_MAX_CHARS - reserve
    line = "Fixes #42"
    head_len = limit - marker_len - len(line) - 1
    head = ("a" * head_len) + "\n"
    suffix_len = marker_len + (reserve // 2)
    suffix = " " + ("Q" * (suffix_len - 1))
    base_text = head + line + suffix
    assert len(base_text) <= _pr_body.PR_BODY_MAX_CHARS
    assert len(base_text) > limit

    composed = pc.plan_pr_body(ws, base_text, trailer)

    assert composed.outcome == "present"
    assert composed.appended == ()
    assert "Q" not in composed.body
    assert composed.body.endswith(_pr_body._TRUNCATION_MARKER)  # noqa: SLF001


def test_indented_list_item_fence_hides_closing_line(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n- item\n  ```\n  Fixes #42\n  ```\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_nested_narrower_fence_cannot_close_wider_outer_fence(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\nExample transcript:\n````markdown\n```\nFixes #42\n````\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_info_string_line_inside_open_fence_does_not_close_it(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n```\nfoo\n```yaml\nFixes #42\n```\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_leading_whitespace_on_closing_line_does_not_count_as_present(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n\n    Fixes #42\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_tilde_fence_hides_closing_line(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n~~~\nFixes #42\n~~~\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_fence_character_mismatch_does_not_close_open_fence(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n```\n~~~\nFixes #42\n```\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.appended == (42,)


def test_appended_body_closes_four_backtick_fence_with_matching_length(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n````python\nsome code without a closer"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.body.endswith("````\n\nFixes #42")


def test_appended_body_closes_tilde_fence_with_matching_character(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n~~~python\nsome code without a closer"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "appended"
    assert composed.body.endswith("~~~\n\nFixes #42")


def test_backtick_in_opener_trailing_is_not_treated_as_a_fence(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    body_text = "## Summary\n```py`thon\nFixes #42\n"
    composed = pc.plan_pr_body(ws, body_text, trailer)
    assert composed.outcome == "present"
    assert composed.appended == ()


def test_appended_body_closes_five_backtick_fence_within_oversized_truncated_body(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    trailer = pc.parse_delivers_trailer(ws.meta.description)
    oversized = "## Summary\n`````python\n" + ("x" * (_pr_body.PR_BODY_MAX_CHARS + 5000))
    composed = pc.plan_pr_body(ws, oversized, trailer)
    assert composed.outcome == "appended"
    assert composed.body.endswith("`````\n\nFixes #42")
    assert len(composed.body) <= _pr_body.PR_BODY_MAX_CHARS


def test_compose_pr_body_writes_single_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: 42")
    body = pc.compose_pr_body(
        ws, "## Summary\nDid the thing.", team="implementation", pr_number=None,
    )
    assert "Fixes #42" in body
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    publish_rows = [e for e in entries if e["action"] == "pr_body_publish"]
    assert len(publish_rows) == 1
    row = publish_rows[0]
    assert row["outcome"] == "appended"
    assert row["issues"] == [42]
    assert row["appended"] == [42]
    assert row["team"] == "implementation"
    assert row["body_length"] == len(body)


def test_compose_pr_body_records_alternative_lengths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _workspace(tmp_path, "No trailer here.")
    synthesis = (
        "```pr_body\n"
        "## Why\n"
        "```\n"
        "code line\n"
        "```\n"
        "Trailing note.\n"
        "```\n"
    )
    pc.compose_pr_body(ws, synthesis, team="implementation", pr_number=7)
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "pr_body_publish")
    assert row["alternative_lengths"] == [len("## Why")]
    assert row["pr_number"] == 7


def test_gh_pr_closing_issue_numbers_parses_numbers(_active_fake_forge: FakeForge) -> None:
    fields: dict[str, object] = {"closingIssuesReferences": [{"number": 12}, {"number": 34}]}
    always(_active_fake_forge, "read_pr", pr_read(fields))
    assert pc.gh_pr_closing_issue_numbers(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == frozenset({12, 34})


def test_gh_pr_closing_issue_numbers_empty_list_returns_empty(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({"closingIssuesReferences": []}))
    assert pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET) == frozenset()


def test_gh_pr_closing_issue_numbers_skips_malformed_entries(
    _active_fake_forge: FakeForge,
) -> None:
    fields: dict[str, object] = {
        "closingIssuesReferences": ["not-a-dict", {"number": "not-an-int"}, {"number": 7}],
    }
    always(_active_fake_forge, "read_pr", pr_read(fields))
    assert pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET) == frozenset({7})


def test_gh_pr_closing_issue_numbers_missing_key_returns_empty(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({}))
    assert pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET) == frozenset()


def test_gh_pr_closing_issue_numbers_rc_nonzero_infra_returns_none(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read(returncode=1, stderr="odd failure"))
    assert pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_gh_pr_closing_issue_numbers_rate_limited_raises(
    _active_fake_forge: FakeForge,
) -> None:
    always(
        _active_fake_forge, "read_pr",
        pr_read(returncode=1, stderr="could not resolve host: api.github.com"),
    )
    with pytest.raises(_forge_errors.ForgeTransientError):
        pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET)


@pytest.mark.parametrize(
    "cause",
    [subprocess.TimeoutExpired(cmd="gh", timeout=60.0), FileNotFoundError("gh")],
    ids=["timeout", "missing_binary"],
)
def test_gh_pr_closing_issue_numbers_transport_fault_raises_forge_transient_from_cause(
    _active_fake_forge: FakeForge, cause: BaseException,
) -> None:
    always(_active_fake_forge, "read_pr", cause)
    with pytest.raises(_forge_errors.ForgeTransientError) as caught:
        pc.gh_pr_closing_issue_numbers(42, forge_target=DEFAULT_FORGE_TARGET)
    assert caught.value.__cause__ is cause
    assert str(caught.value).startswith("gh pr view closingIssuesReferences raised ")


def test_gh_pr_closing_issue_numbers_reads_closing_references_for_the_target(
    _active_fake_forge: FakeForge,
) -> None:
    always(_active_fake_forge, "read_pr", pr_read({}))
    pc.gh_pr_closing_issue_numbers(42, forge_target=_SECONDARY_TARGET)
    assert _active_fake_forge.calls == [
        (
            "read_pr",
            (42, ("closingIssuesReferences",)),
            {"forge_target": _SECONDARY_TARGET, "timeout": _pr_forge.GH_TIMEOUT_S},
        ),
    ]


def test_compose_pr_body_records_trailer_line_when_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = _workspace(tmp_path, "Ship it.\n\nDelivers-Issues: bogus")
    pc.compose_pr_body(ws, "## Summary\nStuff.", team="implementation", pr_number=None)
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "pr_body_publish")
    assert row["outcome"] == "malformed"
    assert row["trailer_line"] == "Delivers-Issues: bogus"


def test_is_salvage_only_true_when_every_commit_is_alan_salvage(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _base_sha(repo)
    _commit(
        repo, "wip.py", "chore: salvage uncommitted implementation output",
        author_name=_SALVAGE_IDENTITY_NAME, author_email=_SALVAGE_IDENTITY_EMAIL,
    )
    assert pc.is_salvage_only(repo, base) is True


def test_is_salvage_only_false_when_a_worker_commit_is_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _base_sha(repo)
    _commit(
        repo, "wip.py", "feat: worker did this",
        author_name="Worker", author_email="worker@example.com",
    )
    _commit(
        repo, "wip2.py", "chore: salvage uncommitted implementation output",
        author_name=_SALVAGE_IDENTITY_NAME, author_email=_SALVAGE_IDENTITY_EMAIL,
    )
    assert pc.is_salvage_only(repo, base) is False


def test_is_salvage_only_false_when_no_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _base_sha(repo)
    assert pc.is_salvage_only(repo, base) is False


def test_is_salvage_only_false_on_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert pc.is_salvage_only(tmp_path, "HEAD") is False


def test_salvaged_diff_stat_empty_on_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert pc._salvaged_diff_stat(tmp_path, "HEAD") == ""  # noqa: SLF001


def test_salvaged_diff_stat_empty_on_nonzero_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal: bad revision")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert pc._salvaged_diff_stat(tmp_path, "HEAD") == ""  # noqa: SLF001


def test_compose_salvaged_pr_body_omits_description_paragraph_when_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _base_sha(repo)
    _commit(
        repo, "wip.py", "chore: salvage uncommitted implementation output",
        author_name=_SALVAGE_IDENTITY_NAME, author_email=_SALVAGE_IDENTITY_EMAIL,
    )
    ws = create("salvaged-blank-desc", "   ", root=tmp_path / "ws")

    body = pc.compose_salvaged_pr_body(ws, repo, base, team="implementation", pr_number=None)

    assert "was salvaged" in body
    assert body.count("\n\n") < 4


def test_compose_salvaged_pr_body_omits_changes_section_when_diff_stat_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("salvaged-no-diff", "Fix the thing.", root=tmp_path / "ws")
    monkeypatch.setattr(pc, "_salvaged_diff_stat", lambda *_a, **_kw: "")

    body = pc.compose_salvaged_pr_body(
        ws, tmp_path / "missing-repo", "HEAD", team="implementation", pr_number=None,
    )

    assert "### Changes" not in body
    assert "was salvaged" in body


def test_compose_salvaged_pr_body_names_title_and_states_salvaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    repo = tmp_path / "repo"
    _init_repo(repo)
    base = _base_sha(repo)
    _commit(
        repo, "wip.py", "chore: salvage uncommitted implementation output",
        author_name=_SALVAGE_IDENTITY_NAME, author_email=_SALVAGE_IDENTITY_EMAIL,
    )
    ws = create("salvaged-task", "Fix the thing.", root=tmp_path / "ws")

    body = pc.compose_salvaged_pr_body(ws, repo, base, team="implementation", pr_number=None)

    assert ws.meta.title in body
    assert "was salvaged" in body
    assert "wip.py" in body
    assert "Fix the thing." in body

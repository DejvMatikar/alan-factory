from __future__ import annotations

import re
from pathlib import Path

import pytest

from alan.claude_cli_grants import (
    CREDENTIAL_BASH_DENIES,
    GENERAL_EXEC_GRANTS,
    PROMPT_INSTRUCTED_GRANTS,
)
from alan.teams import TEAMS
from alan.teams._static_pipeline import StaticPipelineTeam

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOC = _REPO_ROOT / "docs" / "architecture" / "worker_credential_boundary.md"
_INDEX = _REPO_ROOT / "docs" / "architecture" / "README.md"
_AUDIT = _REPO_ROOT / "docs" / "audits" / "auto-mode-boundary-2026-07" / "README.md"

_REQUIRED_SECTIONS = (
    "## Claude Code version this relies on",
    "## Deny rules",
    "## Narrowing rule",
    "## Reach table",
    "## The canary",
    "## Remaining risks",
    "## Stored versus enforced",
    "## Dedicated macOS user",
    "## Follow-ups (not in this change)",
)

_BANNED = {
    "an absolute home path": re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\"),
    "an email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "a token prefix": re.compile(
        r"\b(?:(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]|github_pat_|sk-ant-|sk-[A-Za-z0-9]{8}"
        r"|xox[abprs]-|AKIA[0-9A-Z]{8})"
    ),
    "a tailnet host": re.compile(r"[\w-]+\.ts\.net"),
}

_SCANNED = (
    _DOC,
    _REPO_ROOT / "alan" / "teams" / "_maintenance_secrets.py",
    _REPO_ROOT / "alan" / "claude_cli_grants.py",
    _REPO_ROOT / "tests" / "test_worker_credential_boundary.py",
    _REPO_ROOT / "tests" / "test_worker_credential_reachability.py",
    _REPO_ROOT / "tests" / "test_worker_credential_canary.py",
    _REPO_ROOT / "tests" / "test_maintenance_secret_permissions.py",
)


def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


def section(heading: str) -> str:
    text = doc_text()
    start = text.index(heading) + len(heading)
    rest = text[start:]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def test_the_architecture_index_lists_the_design_doc() -> None:
    assert "(worker_credential_boundary.md)" in _INDEX.read_text(encoding="utf-8")


def test_the_boundary_audit_points_to_the_design_doc() -> None:
    assert "docs/architecture/worker_credential_boundary.md" in _AUDIT.read_text(encoding="utf-8")


def test_the_boundary_audit_no_longer_claims_alan_adds_no_deny_surface() -> None:
    text = _AUDIT.read_text(encoding="utf-8")
    assert "Corrected 2026-09-29 (P1.12)" in text
    claim = "alan adds no deny-surface of its own beyond the `Write`/`Edit`/`NotebookEdit` tool"
    assert claim not in text


@pytest.mark.parametrize("heading", _REQUIRED_SECTIONS)
def test_the_design_doc_has_every_required_section(heading: str) -> None:
    assert re.search(rf"^{re.escape(heading)}$", doc_text(), re.MULTILINE)


def test_the_reach_table_names_every_team_and_every_static_role() -> None:
    table = section("## Reach table")
    for name, team in TEAMS.items():
        assert f"`{name}`" in table, name
        if isinstance(team, StaticPipelineTeam):
            for role in team.roles:
                assert f"`{role.name}`" in table, f"{name}/{role.name}"
    assert "`clause_auditor`" in table


def test_the_reach_table_marks_the_implementation_rows_as_the_only_exempt_ones() -> None:
    rows = [line for line in section("## Reach table").splitlines() if line.startswith("| `")]
    accept_edits = [row.split("|")[1].strip() for row in rows if "`acceptEdits`" in row]
    assert accept_edits == ["`implementation`", "`implementation_fix`"]


def test_the_design_doc_records_a_claude_code_version() -> None:
    assert re.search(r"\bv\d+\.\d+\.\d+\b", section("## Claude Code version this relies on"))


def test_the_design_doc_states_the_real_cli_canary_is_a_post_merge_release_driver_step() -> None:
    canary = section("## The canary")
    assert "post-merge step the release driver runs" in canary
    assert "unverified until this step runs" in canary
    for probe in ("`Read` tool", "Bash `cat", "git diff --no-index", "a gate that must still run"):
        assert probe in canary


def test_the_design_doc_lists_every_remaining_risk_the_spec_names() -> None:
    risks = section("## Remaining risks")
    for phrase in (
        "Script execution by the implementation roles",
        "`find -exec`",
        "Recursive grep over a parent directory",
        "`diff` and `nl`",
        "Argument-pattern git denies catch only the leading form",
        "A secrets-file location moved by the deploy-time override",
    ):
        assert phrase in risks


def test_the_design_doc_states_stored_grants_differ_from_enforced_grants() -> None:
    text = section("## Stored versus enforced")
    assert "engine-configuration page" in text
    assert "argument builder's override" in text
    assert "keep the argument list they were started with" in text


def test_the_design_doc_names_the_constants_it_documents() -> None:
    text = doc_text()
    for grant in GENERAL_EXEC_GRANTS:
        assert f"`{grant}`" in text
    for deny in CREDENTIAL_BASH_DENIES:
        assert f"`{deny}`" in text
    assert "PROMPT_INSTRUCTED_GRANTS" in text
    assert len(PROMPT_INSTRUCTED_GRANTS) == 10


def test_the_design_doc_points_to_the_dedicated_user_advice_and_the_decision() -> None:
    assert "owns nothing else" in section("## Dedicated macOS user")
    assert "credential leak found in the public-release review" in doc_text()
    assert "review-2026-09-26.md" not in doc_text()
    assert "PD17" in doc_text()


@pytest.mark.parametrize("path", _SCANNED, ids=lambda path: path.name)
@pytest.mark.parametrize("kind", list(_BANNED))
def test_new_content_names_no_real_credential_or_owner_identifier(path: Path, kind: str) -> None:
    hits = _BANNED[kind].findall(path.read_text(encoding="utf-8"))
    assert hits == [], f"{path.name} contains {kind}"


def test_the_ban_patterns_do_catch_what_they_name() -> None:
    samples = {
        "an absolute home path": "/" + "Users/someone/.alan",
        "an email address": "someone" + "@example.com",
        "a token prefix": "gh" + "p_" + "abcdefgh",
        "a tailnet host": "box" + ".ts" + ".net",
    }
    for kind, sample in samples.items():
        assert _BANNED[kind].search(sample), kind

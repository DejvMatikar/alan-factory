from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path

import pytest

from alan import setup_verdicts as sv
from alan.branch_protection_compat import COMPATIBILITY_MATRIX
from tests._setup_api_stubs import MATRIX_KEYS

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "alan" / "setup_verdicts.py"


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str, stdin: str = ""
) -> tuple[int, list[str], str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = sv.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _findings(**statuses: str) -> str:
    rows = [{"check": check, "status": status, "message": ""} for check, status in statuses.items()]
    return json.dumps({"findings": rows})


def _all_pass(**overrides: str) -> dict[str, str]:
    checks = ["author_token_valid", "repo_clonable", *sv.MATRIX_CHECKS]
    return {**dict.fromkeys(checks, "pass"), **overrides}


def test_the_owned_checks_contain_every_row_of_the_compatibility_matrix() -> None:
    keys = tuple(row.key for row in COMPATIBILITY_MATRIX)
    assert sv.MATRIX_CHECKS == keys
    assert set(keys) <= sv.OWNED_CHECKS
    assert MATRIX_KEYS == keys


def test_owned_and_pending_checks_do_not_overlap_and_cover_the_credential_checks() -> None:
    assert sv.OWNED_CHECKS.isdisjoint(sv.PENDING_CHECKS)
    assert {"author_token_valid", "repo_clonable", "branch_protection_readable"} <= sv.OWNED_CHECKS
    assert {"reviewer_distinct_from_author", "dispatch_readiness"} == sv.PENDING_CHECKS


def test_the_verdict_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported
    assert imported <= set(sys.stdlib_module_names)


def test_engine_slug_reads_the_forge_repository_in_the_case_the_engine_holds() -> None:
    body = json.dumps({"forge": {"repo": {"owner": "Acme", "repo": "Widget"}}})
    assert sv.engine_slug([], body) == (0, ["Acme/Widget"])


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"forge": None}),
        json.dumps({"repo": {"owner": "a", "repo": "b"}}),
        json.dumps({"forge": {"repo": {"owner": "a"}}}),
        json.dumps({"forge": {"repo": {"owner": "", "repo": "b"}}}),
        json.dumps({"forge": {"repo": "a/b"}}),
    ],
)
def test_engine_slug_refuses_a_record_that_names_no_forge_repository(body: str) -> None:
    with pytest.raises(sv.VerdictError, match="no forge repository"):
        sv.engine_slug([], body)


@pytest.mark.parametrize(
    ("text", "message"),
    [("not json", "not JSON"), ('["a"]', "not a JSON object"), ("", "not JSON")],
)
def test_input_that_is_not_a_json_object_is_refused(text: str, message: str) -> None:
    with pytest.raises(sv.VerdictError, match=message):
        sv.visibility([], text)


def test_has_secret_matches_the_record_name_exactly() -> None:
    body = json.dumps({"secrets": [{"name": "a/b:transport"}, {"name": "a/b:reviewer"}, 3]})
    assert sv.has_secret(["a/b:transport"], body) == (0, ["yes"])
    assert sv.has_secret(["A/B:transport"], body) == (0, ["no"])
    assert sv.has_secret(["a/b:transport"], json.dumps({"secrets": "none"})) == (0, ["no"])
    assert sv.has_secret(["a/b:transport"], "{}") == (0, ["no"])


def test_has_secret_needs_the_name_to_look_for() -> None:
    with pytest.raises(sv.VerdictError, match="needs an argument"):
        sv.has_secret([], "{}")


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"visibility": "public"}, "public"), ({"visibility": "private"}, "private"), ({}, "unknown")],
)
def test_visibility_is_read_from_the_repository(body: dict[str, str], expected: str) -> None:
    assert sv.visibility([], json.dumps(body)) == (0, [expected])


def _repo(visibility: str = "public", **fields: object) -> str:
    analysis = {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"},
    }
    body = {
        "visibility": visibility,
        "security_and_analysis": analysis,
        "pull_request_creation_policy": "collaborators_only",
        "has_issues": True,
    }
    return json.dumps({**body, **fields})


def _limits(tmp_path: Path, body: object) -> list[str]:
    path = tmp_path / "limits.json"
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return [str(path)]


def _plan(lines: list[str]) -> dict[str, tuple[str, str]]:
    plan: dict[str, tuple[str, str]] = {}
    for line in lines:
        setting, state, *reason = line.split(" ", 2)
        plan[setting] = (state, " ".join(reason))
    return plan


def test_a_bound_public_copy_needs_nothing(tmp_path: Path) -> None:
    code, lines = sv.harden_plan(_limits(tmp_path, {"limit": "collaborators_only"}), _repo())
    assert code == 0
    assert _plan(lines) == {
        "secret_scanning": ("already", ""),
        "push_protection": ("already", ""),
        "interaction_limits": ("already", ""),
        "pr_policy": ("already", ""),
        "issues": ("already", ""),
    }


@pytest.mark.parametrize("visibility", ["private", "internal", "", "weird"])
def test_public_only_settings_are_unavailable_when_the_copy_is_not_public(
    tmp_path: Path, visibility: str
) -> None:
    _, lines = sv.harden_plan(_limits(tmp_path, {}), _repo(visibility))
    plan = _plan(lines)
    for setting in ("secret_scanning", "push_protection", "interaction_limits"):
        assert plan[setting] == ("unavailable", "the repository is not public")
    assert plan["pr_policy"] == ("already", "")


def test_the_pr_policy_is_decided_on_every_copy(tmp_path: Path) -> None:
    for visibility in ("public", "private"):
        repo = _repo(visibility, pull_request_creation_policy="all")
        held = sv.harden_plan(_limits(tmp_path, {}), repo)
        assert _plan(held[1])["pr_policy"] == ("apply", "")
        absent = json.loads(_repo(visibility))
        del absent["pull_request_creation_policy"]
        assert _plan(sv.harden_plan([], json.dumps(absent))[1])["pr_policy"] == ("apply", "")


@pytest.mark.parametrize("status", ["disabled", "", None])
def test_a_feature_that_is_not_enabled_is_applied(tmp_path: Path, status: str | None) -> None:
    analysis = {
        "secret_scanning": {"status": status},
        "secret_scanning_push_protection": {"status": status},
    }
    _, lines = sv.harden_plan(_limits(tmp_path, {}), _repo(security_and_analysis=analysis))
    plan = _plan(lines)
    assert plan["secret_scanning"] == ("apply", "")
    assert plan["push_protection"] == ("apply", "")


def test_a_feature_github_does_not_return_is_unavailable_with_the_reason(tmp_path: Path) -> None:
    analysis = {"secret_scanning": {"status": "enabled"}}
    _, lines = sv.harden_plan(_limits(tmp_path, {}), _repo(security_and_analysis=analysis))
    plan = _plan(lines)
    assert plan["secret_scanning"] == ("already", "")
    assert plan["push_protection"] == (
        "unavailable",
        "GitHub does not offer push protection for this repository",
    )


def test_a_missing_security_block_makes_both_features_unavailable() -> None:
    body = json.loads(_repo())
    del body["security_and_analysis"]
    plan = _plan(sv.harden_plan([], json.dumps(body))[1])
    assert plan["secret_scanning"][0] == "unavailable"
    assert plan["push_protection"][0] == "unavailable"
    assert "secret scanning" in plan["secret_scanning"][1]


@pytest.mark.parametrize(
    "body", [{}, {"limit": "existing_users"}, {"limit": None}, {"origin": "repository"}]
)
def test_interaction_limits_are_applied_when_missing_or_not_collaborators_only(
    tmp_path: Path, body: dict[str, object]
) -> None:
    plan = _plan(sv.harden_plan(_limits(tmp_path, body), _repo())[1])
    assert plan["interaction_limits"] == ("apply", "")


@pytest.mark.parametrize("origin", ["repository", "organization"])
def test_interaction_limits_held_from_any_origin_are_not_renewed(
    tmp_path: Path, origin: str
) -> None:
    limits = {"limit": "collaborators_only", "origin": origin}
    plan = _plan(sv.harden_plan(_limits(tmp_path, limits), _repo())[1])
    assert plan["interaction_limits"] == ("already", "")


@pytest.mark.parametrize("unreadable", ["", "not json", "[1]"])
def test_an_unreadable_limits_answer_counts_as_missing(tmp_path: Path, unreadable: str) -> None:
    plan = _plan(sv.harden_plan(_limits(tmp_path, unreadable), _repo())[1])
    assert plan["interaction_limits"] == ("apply", "")


def test_a_limits_file_that_does_not_exist_or_is_not_named_counts_as_missing(
    tmp_path: Path,
) -> None:
    gone = [str(tmp_path / "gone.json")]
    assert _plan(sv.harden_plan(gone, _repo())[1])["interaction_limits"] == ("apply", "")
    assert _plan(sv.harden_plan([], _repo())[1])["interaction_limits"] == ("apply", "")


def test_protection_matches_only_when_all_five_rows_pass() -> None:
    assert sv.protection_probe([], _findings(**_all_pass())) == (0, ["match"])


@pytest.mark.parametrize("check", sv.MATRIX_CHECKS)
@pytest.mark.parametrize("status", ["fail", "unknown"])
def test_one_row_that_does_not_pass_means_protection_differs(check: str, status: str) -> None:
    assert sv.protection_probe([], _findings(**_all_pass(**{check: status}))) == (0, ["differs"])


def test_a_missing_row_means_protection_differs() -> None:
    passing = _all_pass()
    del passing["required_checks"]
    assert sv.protection_probe([], _findings(**passing)) == (0, ["differs"])


def test_an_unreadable_protection_finding_means_protection_differs_even_when_rows_pass() -> None:
    body = _findings(**_all_pass(branch_protection_readable="pass"))
    assert sv.protection_probe([], body) == (0, ["differs"])


def test_findings_that_are_not_objects_are_ignored() -> None:
    body = json.dumps({"findings": ["x", 3, {"check": "required_checks", "status": "pass"}]})
    assert sv.protection_probe([], body) == (0, ["differs"])
    assert sv.protection_probe([], "{}") == (0, ["differs"])


def test_protect_reports_applied_only_with_applied_keys_and_nothing_deferred() -> None:
    state = {"protection_applied_keys": ["required_checks"], "protection_deferred": []}
    assert sv.protect_outcome([], json.dumps(state)) == (0, ["applied"])


def test_protect_reports_every_deferred_finding_with_its_reason() -> None:
    deferred = [
        {"check": "required_checks", "status": "fail", "message": "cannot   read\nprotection"},
        {"check": "delete_branch_on_merge", "status": "fail"},
        {"status": "fail", "message": "no name"},
    ]
    state = {"protection_applied_keys": ["a"], "protection_deferred": deferred}
    code, lines = sv.protect_outcome([], json.dumps(state))
    assert code == 0
    assert lines == [
        "deferred",
        "required_checks: cannot read protection",
        "delete_branch_on_merge: no reason given",
        "unknown: no name",
    ]


@pytest.mark.parametrize(
    "state", [{}, {"protection_applied_keys": [], "protection_deferred": []}, {"detail": "boom"}]
)
def test_protect_reports_deferred_when_nothing_was_applied(state: dict[str, object]) -> None:
    assert sv.protect_outcome([], json.dumps(state)) == (
        0,
        ["deferred", "the protect action reported no applied settings"],
    )


def test_a_long_message_is_cut_to_one_short_line() -> None:
    state = {"protection_deferred": [{"check": "c", "message": "x " * 400}]}
    line = sv.protect_outcome([], json.dumps(state))[1][1]
    assert len(line) <= len("c: ") + 200
    assert "\n" not in line


def test_confirm_passes_and_classifies_every_finding() -> None:
    findings = [
        {"check": "author_token_valid", "status": "pass", "message": ""},
        {"check": "reviewer_distinct_from_author", "status": "unknown", "message": "no reviewer"},
        {"check": "repo_clonable", "status": "pass", "message": ""},
        {"check": "flow_graph_and_posture_valid", "status": "fail", "message": "bad"},
        *({"check": key, "status": "pass", "message": ""} for key in sv.MATRIX_CHECKS),
        {"check": "dispatch_readiness", "status": "fail", "message": "missing"},
    ]
    code, lines = sv.confirm([], json.dumps({"findings": findings}))
    assert code == 0
    assert "pending unknown reviewer_distinct_from_author no reviewer" in lines
    assert "other fail flow_graph_and_posture_valid bad" in lines
    assert "pending fail dispatch_readiness missing" in lines
    assert "owned pass author_token_valid" in lines


@pytest.mark.parametrize("check", ["author_token_valid", "repo_clonable", *sv.MATRIX_CHECKS])
@pytest.mark.parametrize("status", ["fail", "unknown"])
def test_confirm_fails_when_an_owned_finding_does_not_pass(check: str, status: str) -> None:
    code, lines = sv.confirm([], _findings(**_all_pass(**{check: status})))
    assert code == 1
    assert f"owned {status} {check}" in lines


def test_confirm_fails_when_an_owned_finding_is_missing() -> None:
    passing = _all_pass()
    del passing["repo_clonable"]
    del passing["required_checks"]
    code, lines = sv.confirm([], _findings(**passing))
    assert code == 1
    assert "owned unknown repo_clonable the preflight reported no finding" in lines
    assert "owned unknown required_checks the preflight reported no finding" in lines


def test_confirm_accepts_the_unreadable_protection_finding_in_place_of_the_rows_and_fails() -> None:
    body = _findings(
        author_token_valid="pass", repo_clonable="pass", branch_protection_readable="fail"
    )
    code, lines = sv.confirm([], body)
    assert code == 1
    assert "owned fail branch_protection_readable" in lines
    assert not any("the preflight reported no finding" in line for line in lines)


def test_confirm_treats_a_finding_with_no_status_as_unknown() -> None:
    body = json.dumps({"findings": [{"check": "repo_clonable"}]})
    code, lines = sv.confirm([], body)
    assert code == 1
    assert "owned unknown repo_clonable" in lines


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        ({"status": "fail", "message": "boom"}, "other fail unknown boom"),
        ({"check": 7, "status": "pass"}, "other pass unknown"),
    ],
)
def test_confirm_names_a_finding_with_no_check_unknown_and_keeps_the_message_in_its_slot(
    finding: dict[str, object], expected: str
) -> None:
    passing = [{"check": check, "status": "pass"} for check in _all_pass()]
    code, lines = sv.confirm([], json.dumps({"findings": [finding, *passing]}))
    assert code == 0
    assert lines[0] == expected


def test_main_prints_the_verdict_lines_and_returns_the_verdict_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines, err = _run(monkeypatch, capsys, "visibility", stdin='{"visibility": "public"}')
    assert (code, lines, err) == (0, ["public"], "")
    code, lines, _ = _run(monkeypatch, capsys, "confirm", stdin="{}")
    assert code == 1
    assert lines


def test_main_reports_unreadable_input_on_stderr_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines, err = _run(monkeypatch, capsys, "visibility", stdin="nope")
    assert (code, lines) == (2, [])
    assert err == "setup_verdicts: the input is not JSON\n"


@pytest.mark.parametrize("argv", [[], ["nonsense"]])
def test_main_rejects_a_missing_or_unknown_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    code, _, err = _run(monkeypatch, capsys, *argv)
    assert code == 2
    assert "expected one of" in err


def test_main_reads_the_process_arguments_when_none_are_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["setup_verdicts", "visibility"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"visibility": "private"}'))
    assert sv.main() == 0
    assert capsys.readouterr().out == "private\n"


@pytest.mark.parametrize("visibility", ["public", "private"])
@pytest.mark.parametrize("has_issues", [False, None, "true"])
def test_issues_are_applied_on_every_copy_unless_github_reports_them_on(
    tmp_path: Path, visibility: str, has_issues: object
) -> None:
    _, lines = sv.harden_plan(_limits(tmp_path, {}), _repo(visibility, has_issues=has_issues))
    assert _plan(lines)["issues"] == ("apply", "")


def test_run_progress_counts_done_running_and_queued_jobs() -> None:
    jobs = [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "tests", "status": "in_progress"},
        {"name": "audit", "status": "queued"},
        {"name": "e2e", "status": "waiting"},
    ]
    code, lines = sv.run_progress([], json.dumps({"status": "in_progress", "jobs": jobs}))
    assert code == 0
    assert lines == ["1 of 4 jobs done, 1 running, 2 queued"]


def test_run_progress_of_a_run_without_jobs_reports_zero_of_zero() -> None:
    assert sv.run_progress([], json.dumps({"status": "queued"}))[1] == [
        "0 of 0 jobs done, 0 running, 0 queued"
    ]

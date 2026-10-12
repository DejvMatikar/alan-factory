from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from alan.engine_config import EngineConfig, RepoIdentity, register
from alan.predispatch_scan import (
    _scan_anti_scope,
    _scan_in_flight_pr,
    _scan_prior_workspace,
    scan_predispatch,
)
from alan.teams._pr_branch import OpenPrInfo
from alan.workspace import create


def _register_sprocket_engine() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))


def test_scan_predispatch_returns_empty_list_when_no_prs_no_workspaces(
    tmp_path: Path,
) -> None:
    with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
        findings = scan_predispatch("new task", "some description", workspaces_root=tmp_path)
    assert findings == []


def test_scan_predispatch_returns_empty_list_on_in_flight_exception(
    tmp_path: Path,
) -> None:
    def _raise() -> list[OpenPrInfo]:
        raise RuntimeError("gh not found")

    with patch("alan.predispatch_scan.list_open_prs", side_effect=_raise):
        findings = scan_predispatch("new task", "desc", workspaces_root=tmp_path)
    assert findings == []


def test_scan_predispatch_returns_empty_list_on_prior_workspace_exception(
    tmp_path: Path,
) -> None:
    def _raise(*, root: Path | None) -> list[object]:
        raise RuntimeError("io error")

    with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
        with patch("alan.workspace.list_workspaces", side_effect=_raise):
            findings = scan_predispatch("x", "y", workspaces_root=tmp_path)
    assert findings == []


def test_scan_in_flight_pr_detects_overlap() -> None:
    prs = [OpenPrInfo(number=42, title="add authentication to the API")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = _scan_in_flight_pr("add authentication flow")
    assert len(findings) == 1
    assert findings[0].kind == "in_flight_pr"
    assert "42" in findings[0].summary
    assert findings[0].suggested_action == "needs_user_call"


def test_scan_in_flight_pr_ignores_unrelated_prs() -> None:
    prs = [OpenPrInfo(number=7, title="fix typo in readme")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = _scan_in_flight_pr("add authentication to the API endpoint")
    assert findings == []


def test_scan_in_flight_pr_returns_empty_when_no_prs() -> None:
    with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
        findings = _scan_in_flight_pr("any query")
    assert findings == []


def test_scan_prior_workspace_detects_duplicate(tmp_path: Path) -> None:
    create("add authentication flow", "implement oauth2", root=tmp_path)
    with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
        findings = _scan_prior_workspace(
            "add authentication flow",
            workspaces_root=tmp_path,
            exclude_task_id=None,
        )
    assert len(findings) == 1
    assert findings[0].kind == "prior_workspace"
    assert findings[0].suggested_action == "needs_user_call"


def test_scan_prior_workspace_excludes_task_id(tmp_path: Path) -> None:
    ws = create("add authentication flow", "implement oauth2", root=tmp_path)
    findings = _scan_prior_workspace(
        "add authentication flow",
        workspaces_root=tmp_path,
        exclude_task_id=ws.task_id,
    )
    assert findings == []


def test_scan_prior_workspace_ignores_unrelated_workspace(tmp_path: Path) -> None:
    create("fix database migrations", "update schema version", root=tmp_path)
    findings = _scan_prior_workspace(
        "add react component for sidebar",
        workspaces_root=tmp_path,
        exclude_task_id=None,
    )
    assert findings == []


def test_scan_predispatch_caps_at_ten_findings(tmp_path: Path) -> None:
    prs = [OpenPrInfo(number=i, title="add authentication to the API") for i in range(15)]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = scan_predispatch("add authentication", "desc", workspaces_root=tmp_path)
    assert len(findings) <= 10


def test_scan_predispatch_uses_title_and_description(tmp_path: Path) -> None:
    prs = [OpenPrInfo(number=1, title="refactor workspace creation endpoint")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings_title = scan_predispatch(
            "refactor workspace creation", "desc", workspaces_root=tmp_path
        )
        findings_desc = scan_predispatch(
            "unrelated title",
            "refactor workspace creation endpoint",
            workspaces_root=tmp_path,
        )
    assert len(findings_title) >= 1
    assert len(findings_desc) >= 1


def test_scan_predispatch_references_pr_number(tmp_path: Path) -> None:
    prs = [OpenPrInfo(number=123, title="add scope audit to create task surface")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = scan_predispatch(
            "add scope audit", "desc", workspaces_root=tmp_path
        )
    assert any("123" in f.references[0] for f in findings if f.references)


def test_scan_predispatch_prior_workspace_references_task_id(tmp_path: Path) -> None:
    ws = create("add scope audit to create task", "desc", root=tmp_path)
    with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
        findings = scan_predispatch(
            "add scope audit to create task surface",
            "desc",
            workspaces_root=tmp_path,
        )
    assert any(ws.task_id in f.references for f in findings if f.references)


def test_scan_anti_scope_detects_violation() -> None:
    anti_scope = "In scope: chat UI.\nAnti-scope: do not touch the billing module."
    findings = _scan_anti_scope("Refactor billing module payment flow", anti_scope)
    assert len(findings) == 1
    assert findings[0].kind == "anti_scope"
    assert findings[0].suggested_action == "out_of_scope"


def test_scan_anti_scope_ignores_positive_scope_match() -> None:
    anti_scope = "In scope: chat UI improvements.\nAnti-scope: do not touch the billing module."
    findings = _scan_anti_scope("Improve chat UI styling", anti_scope)
    assert findings == []


def test_scan_anti_scope_returns_empty_without_negation_markers() -> None:
    findings = _scan_anti_scope("touch billing module", "In scope: billing module work.")
    assert findings == []


def test_scan_anti_scope_returns_empty_for_blank_section() -> None:
    assert _scan_anti_scope("anything", "") == []


def test_scan_anti_scope_skips_constraint_with_no_content_tokens() -> None:
    findings = _scan_anti_scope("anything", "Anti-scope: must not.")
    assert findings == []


def test_scan_predispatch_anti_scope_runs_first(tmp_path: Path) -> None:
    prs = [OpenPrInfo(number=1, title="add authentication to the API")]
    anti_scope = "Anti-scope: do not touch the billing module."
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = scan_predispatch(
            "Refactor billing module and add authentication",
            "desc",
            workspaces_root=tmp_path,
            anti_scope=anti_scope,
        )
    assert findings[0].kind == "anti_scope"


def test_scan_predispatch_anti_scope_none_skips_check(tmp_path: Path) -> None:
    with patch("alan.predispatch_scan._scan_anti_scope") as mock_scan:
        with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
            findings = scan_predispatch("x", "y", workspaces_root=tmp_path, anti_scope=None)
    mock_scan.assert_not_called()
    assert findings == []


def test_scan_predispatch_anti_scope_empty_string_skips_check(tmp_path: Path) -> None:
    with patch("alan.predispatch_scan._scan_anti_scope") as mock_scan:
        with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
            scan_predispatch("x", "y", workspaces_root=tmp_path, anti_scope="")
    mock_scan.assert_not_called()


def test_scan_predispatch_anti_scope_exception_does_not_break_other_checks(
    tmp_path: Path,
) -> None:
    with patch("alan.predispatch_scan._scan_anti_scope", side_effect=RuntimeError("boom")):
        with patch("alan.predispatch_scan.list_open_prs", return_value=[]):
            findings = scan_predispatch(
                "x", "y", workspaces_root=tmp_path, anti_scope="Anti-scope: do not touch x."
            )
    assert findings == []


def test_scan_prior_workspace_excludes_out_of_scope_workspace(tmp_path: Path) -> None:
    _register_sprocket_engine()
    create(
        "add authentication flow",
        "implement oauth2",
        root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    findings = _scan_prior_workspace(
        "add authentication flow",
        workspaces_root=tmp_path,
        exclude_task_id=None,
        scope_engine_key=None,
    )
    assert findings == []


def test_scan_prior_workspace_explicit_default_scope_matches_none_keyed_workspace(
    tmp_path: Path,
) -> None:
    create("add authentication flow", "implement oauth2", root=tmp_path)
    findings = _scan_prior_workspace(
        "add authentication flow",
        workspaces_root=tmp_path,
        exclude_task_id=None,
        scope_engine_key="DejvMatikar/alan",
    )
    assert len(findings) == 1


def test_scan_prior_workspace_none_scope_matches_explicit_default_slug_workspace(
    tmp_path: Path,
) -> None:
    create(
        "add authentication flow",
        "implement oauth2",
        root=tmp_path,
        engine_key="DejvMatikar/alan",
    )
    findings = _scan_prior_workspace(
        "add authentication flow",
        workspaces_root=tmp_path,
        exclude_task_id=None,
        scope_engine_key=None,
    )
    assert len(findings) == 1


def test_scan_prior_workspace_sprocket_scope_matches_sprocket_workspace(tmp_path: Path) -> None:
    _register_sprocket_engine()
    create(
        "add authentication flow",
        "implement oauth2",
        root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    findings = _scan_prior_workspace(
        "add authentication flow",
        workspaces_root=tmp_path,
        exclude_task_id=None,
        scope_engine_key="sprocket-co/sprocket",
    )
    assert len(findings) == 1


def test_scan_predispatch_non_default_scope_skips_in_flight_pr_check(tmp_path: Path) -> None:
    _register_sprocket_engine()
    prs = [OpenPrInfo(number=1, title="add authentication to the API")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs) as mock_prs:
        findings = scan_predispatch(
            "add authentication",
            "desc",
            workspaces_root=tmp_path,
            scope_engine_key="sprocket-co/sprocket",
        )
    mock_prs.assert_not_called()
    assert findings == []


def test_scan_predispatch_default_scope_still_runs_in_flight_pr_check(tmp_path: Path) -> None:
    prs = [OpenPrInfo(number=1, title="add authentication to the API")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = scan_predispatch(
            "add authentication", "desc", workspaces_root=tmp_path, scope_engine_key=None
        )
    assert any(f.kind == "in_flight_pr" for f in findings)


def test_scan_predispatch_explicit_default_slug_scope_still_runs_in_flight_pr_check(
    tmp_path: Path,
) -> None:
    prs = [OpenPrInfo(number=1, title="add authentication to the API")]
    with patch("alan.predispatch_scan.list_open_prs", return_value=prs):
        findings = scan_predispatch(
            "add authentication",
            "desc",
            workspaces_root=tmp_path,
            scope_engine_key="DejvMatikar/alan",
        )
    assert any(f.kind == "in_flight_pr" for f in findings)

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from pydantic import ValidationError

from alan import branch_protection_compat as bpc
from alan import github_repos as gr

_QueuedResponse = subprocess.CompletedProcess[str] | BaseException

_MUTATING_MARKERS = ("-X", "--method", "POST", "PATCH", "DELETE", "PUT", "pulls", "issues")


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _queued_invoke(
    monkeypatch: pytest.MonkeyPatch, responses: list[_QueuedResponse]
) -> list[list[str]]:
    seen: list[list[str]] = []
    it = iter(responses)

    def _fake(args: list[str], *, token: str, timeout: float) -> subprocess.CompletedProcess[str]:
        del token, timeout
        seen.append(list(args))
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(gr, "_invoke_gh_api", _fake)
    return seen


def _row(
    full_name: str = "acme/widget", *, pull: bool = True, push: bool = True, admin: bool = True
) -> dict[str, Any]:
    return {"full_name": full_name, "permissions": {"pull": pull, "push": push, "admin": admin}}


def _scoped_response(header_lines: list[str]) -> subprocess.CompletedProcess[str]:
    header = "HTTP/2.0 200 OK\r\n" + "".join(f"{line}\r\n" for line in header_lines)
    return _completed(stdout=header + "\r\n" + '{"login":"stub"}\n')


def test_required_permissions_has_both_token_types_and_nonempty_fields() -> None:
    types = {p.token_type for p in gr.REQUIRED_PERMISSIONS}
    assert types == {"classic", "fine-grained"}
    for perm in gr.REQUIRED_PERMISSIONS:
        assert perm.scope.strip()
        assert perm.description.strip()


def test_required_permissions_present_on_every_return_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(rc=1, stderr="bad credentials")])
    invalid = gr.probe_repos("tok")
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps([_row()]))])
    ok = gr.probe_repos("tok")
    for result in (invalid, ok):
        assert result.required_permissions == gr.REQUIRED_PERMISSIONS


def test_models_are_frozen() -> None:
    cap = gr.RepoCapability(slug="a", read=True, write=True, admin=True)
    with pytest.raises(ValidationError):
        cap.read = False  # type: ignore[misc]
    perm = gr.RequiredPermission(token_type="classic", scope="repo", description="d")
    with pytest.raises(ValidationError):
        perm.scope = "x"  # type: ignore[misc]
    result = gr.GithubReposResult()
    with pytest.raises(ValidationError):
        result.repos = ()  # type: ignore[misc]


def test_probe_repos_maps_full_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps([_row()]))])
    result = gr.probe_repos("tok")
    assert len(result.repos) == 1
    cap = result.repos[0]
    assert cap.slug == "acme/widget"
    assert (cap.read, cap.write, cap.admin) == (True, True, True)
    assert cap.finding is None
    assert result.findings == ()


def test_probe_repos_non_admin_repo_gets_named_capability_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(
        monkeypatch, [_completed(), _completed(stdout=json.dumps([_row(admin=False)]))]
    )
    result = gr.probe_repos("tok")
    cap = result.repos[0]
    assert cap.admin is False
    assert cap.finding is not None
    assert cap.finding.check == "repo_admin"
    assert "admin" in cap.finding.message
    assert result.findings == ()


def test_probe_repos_read_only_repo_finding_not_conflated_with_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(
        monkeypatch,
        [_completed(), _completed(stdout=json.dumps([_row(push=False, admin=False)]))],
    )
    result = gr.probe_repos("tok")
    cap = result.repos[0]
    assert cap.read is True
    assert cap.write is False
    assert cap.admin is False
    assert cap.finding is not None
    assert cap.finding.check == "repo_admin"


def test_probe_repos_missing_permissions_defaults_to_no_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"full_name": "acme/no-perms"}]
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps(rows))])
    result = gr.probe_repos("tok")
    cap = result.repos[0]
    assert (cap.read, cap.write, cap.admin) == (False, False, False)
    assert cap.finding is not None


def test_probe_repos_skips_rows_with_empty_or_missing_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"full_name": "", "permissions": {"pull": True, "push": True, "admin": True}},
        {"permissions": {"pull": True, "push": True, "admin": True}},
        _row(full_name="acme/keep"),
    ]
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps(rows))])
    result = gr.probe_repos("tok")
    assert [r.slug for r in result.repos] == ["acme/keep"]


def test_parse_repo_list_filters_non_dict_items(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[Any] = [_row(full_name="acme/keep"), "not-a-dict", 123, None]
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps(rows))])
    result = gr.probe_repos("tok")
    assert [r.slug for r in result.repos] == ["acme/keep"]


def test_probe_repos_invalid_token_returns_named_finding_not_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(rc=1, stderr="gh: Bad credentials (HTTP 401)")])
    result = gr.probe_repos("bad-token")  # noqa: S106
    assert result.repos == ()
    assert len(result.findings) == 1
    assert result.findings[0].check == "token_valid"
    assert result.findings[0].status == "fail"


def test_probe_repos_token_check_raises_ghapicallerror_becomes_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [bpc._GhApiCallError("boom")])  # noqa: SLF001
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "token_valid"
    assert "boom" in result.findings[0].message


def test_invalid_token_finding_falls_back_when_stderr_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(rc=1, stderr="")])
    result = gr.probe_repos("tok")
    assert result.findings[0].message == "gh api user failed"


def test_probe_repos_listing_403_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(
        monkeypatch,
        [_completed(), _completed(rc=1, stderr="API rate limit exceeded (HTTP 403)")],
    )
    result = gr.probe_repos("tok")
    assert result.repos == ()
    assert result.findings[0].check == "rate_limited"


def test_probe_repos_listing_429_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(
        monkeypatch, [_completed(), _completed(rc=1, stderr="secondary rate limit (429)")]
    )
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "rate_limited"


def test_probe_repos_listing_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(
        monkeypatch, [_completed(), _completed(rc=1, stderr="boom, service unavailable")]
    )
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "repo_list_failed"


def test_listing_failure_falls_back_message_when_stderr_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(), _completed(rc=1, stderr="")])
    result = gr.probe_repos("tok")
    assert "page 1 failed" in result.findings[0].message


def test_probe_repos_listing_raises_ghapicallerror_becomes_generic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(), bpc._GhApiCallError("network blip")])  # noqa: SLF001
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "repo_list_failed"
    assert "network blip" in result.findings[0].message


def test_probe_repos_listing_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout="not json")])
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "repo_list_failed"


def test_probe_repos_listing_non_array_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps({"a": 1}))])
    result = gr.probe_repos("tok")
    assert result.findings[0].check == "repo_list_failed"


def test_probe_repos_short_page_stops_after_one_listing_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps([_row()]))])
    gr.probe_repos("tok")
    listing_calls = [a for a in seen if a[0].startswith("user/repos")]
    assert len(listing_calls) == 1


def test_probe_repos_full_pages_truncates_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    full_page = [_row(full_name=f"acme/repo-{i}") for i in range(gr._PER_PAGE)]  # noqa: SLF001
    responses: list[_QueuedResponse] = [
        _completed(),
        *(_completed(stdout=json.dumps(full_page)) for _ in range(gr._MAX_PAGES)),  # noqa: SLF001
    ]
    seen = _queued_invoke(monkeypatch, responses)
    result = gr.probe_repos("tok")
    listing_calls = [a for a in seen if a[0].startswith("user/repos")]
    assert len(listing_calls) == gr._MAX_PAGES  # noqa: SLF001
    assert len(result.repos) == gr._MAX_PAGES * gr._PER_PAGE  # noqa: SLF001
    assert any(f.check == "repo_list_truncated" for f in result.findings)




def test_probe_workflow_scope_present(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _queued_invoke(
        monkeypatch, [_scoped_response(["X-OAuth-Scopes: repo, workflow, gist"])]
    )
    assert gr.probe_workflow_scope("tok") == "present"
    assert seen == [["-i", "user"]]


def test_probe_workflow_scope_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_invoke(monkeypatch, [_scoped_response(["X-OAuth-Scopes: repo, gist"])])
    assert gr.probe_workflow_scope("tok") == "absent"


def test_probe_workflow_scope_unverifiable_when_header_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_scoped_response([])])
    assert gr.probe_workflow_scope("tok") == "unverifiable"


def test_probe_workflow_scope_unverifiable_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_completed(rc=1, stderr="bad credentials")])
    assert gr.probe_workflow_scope("tok") == "unverifiable"


def test_probe_workflow_scope_unverifiable_on_ghapicallerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [bpc._GhApiCallError("boom")])  # noqa: SLF001
    assert gr.probe_workflow_scope("tok") == "unverifiable"


def test_probe_workflow_scope_case_insensitive_header_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(monkeypatch, [_scoped_response(["x-oauth-scopes: workflow"])])
    assert gr.probe_workflow_scope("tok") == "present"


def test_probe_workflow_scope_multi_scope_with_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(
        monkeypatch, [_scoped_response(["X-OAuth-Scopes:  repo ,  workflow  , gist"])]
    )
    assert gr.probe_workflow_scope("tok") == "present"


def test_probe_workflow_scope_present_when_response_has_no_blank_line_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queued_invoke(
        monkeypatch, [_completed(stdout="HTTP/2.0 200 OK\r\nX-OAuth-Scopes: workflow")]
    )
    assert gr.probe_workflow_scope("tok") == "present"


def test_probe_repos_never_issues_mutating_or_pr_issue_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _queued_invoke(monkeypatch, [_completed(), _completed(stdout=json.dumps([_row()]))])
    gr.probe_repos("tok")
    for args in seen:
        assert args[0] == "user" or args[0].startswith("user/repos?")
        joined = " ".join(args)
        for marker in _MUTATING_MARKERS:
            assert marker not in joined

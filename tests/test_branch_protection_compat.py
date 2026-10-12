from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from alan import branch_protection_compat as bpc
from alan import secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
    authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
)

_REPO_JSON_PASS: dict[str, Any] = {
    "default_branch": "main",
    "allow_squash_merge": True,
    "delete_branch_on_merge": True,
}

_PROTECTION_JSON_PASS: dict[str, Any] = {
    "required_conversation_resolution": {"enabled": True},
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "required_status_checks": {"contexts": ["SonarCloud Code Analysis"], "checks": []},
}


def _completed(
    *, rc: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


_QueuedResponse = subprocess.CompletedProcess[str] | BaseException


def _queued_run(
    monkeypatch: pytest.MonkeyPatch, responses: list[_QueuedResponse]
) -> list[list[str]]:
    seen: list[list[str]] = []
    it = iter(responses)

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(subprocess, "run", _run)
    return seen


def _with_token(monkeypatch: pytest.MonkeyPatch, token: str | None = "tok-123") -> None:  # noqa: S107
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: token)  # noqa: ARG005


def _pass_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    return copy.deepcopy(_REPO_JSON_PASS), copy.deepcopy(_PROTECTION_JSON_PASS)


def test_matrix_has_five_rows_with_unique_keys() -> None:
    assert len(bpc.COMPATIBILITY_MATRIX) == 5
    keys = [row.key for row in bpc.COMPATIBILITY_MATRIX]
    assert len(keys) == len(set(keys))


def test_matrix_rows_have_nonempty_fields() -> None:
    for row in bpc.COMPATIBILITY_MATRIX:
        assert row.assumption.strip()
        assert row.required_setting.strip()
        assert row.remedy.strip()


def test_gh_api_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [_completed(stdout='{"a": 1}')])
    assert bpc._gh_api_json(["repos/x"], token="t", timeout=5) == {"a": 1}  # noqa: SLF001


def test_gh_api_json_403_raises_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(
        monkeypatch,
        [_completed(rc=1, stderr="gh: Must have admin rights to Repository. (HTTP 403)")],
    )
    with pytest.raises(bpc.BranchProtectionUnreadableError):
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_gh_api_json_other_nonzero_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [_completed(rc=1, stderr="rate limited (HTTP 429)")])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_gh_api_json_malformed_json_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [_completed(stdout="not json")])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_gh_api_json_non_object_json_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [_completed(stdout="[1, 2, 3]")])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_gh_api_json_timeout_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [subprocess.TimeoutExpired(cmd=["gh"], timeout=5)])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_gh_api_json_oserror_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [FileNotFoundError("no gh binary")])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._gh_api_json(["repos/x"], token="t", timeout=5)  # noqa: SLF001


def test_default_branch_present() -> None:
    assert bpc._default_branch({"default_branch": "trunk"}) == "trunk"  # noqa: SLF001


def test_default_branch_missing_falls_back_to_main() -> None:
    assert bpc._default_branch({}) == "main"  # noqa: SLF001


def test_default_branch_non_string_falls_back_to_main() -> None:
    assert bpc._default_branch({"default_branch": 12}) == "main"  # noqa: SLF001


def test_no_credential_returns_single_unknown_and_no_subprocess_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch, None)

    def _boom(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called without a credential")

    monkeypatch.setattr(subprocess, "run", _boom)
    findings = bpc.compare_branch_protection(_FORGE)
    assert len(findings) == 1
    assert findings[0].check == "branch_protection_readable"
    assert findings[0].status == "unknown"


def test_403_on_repo_call_returns_single_named_admin_scope_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [_completed(rc=1, stderr="gh: Must have admin rights to Repository. (HTTP 403)")],
    )
    findings = bpc.compare_branch_protection(_FORGE)
    assert len(findings) == 1
    assert findings[0].check == "branch_protection_readable"
    assert findings[0].status == "fail"
    assert "admin scope" in findings[0].message


def test_403_on_protection_call_returns_single_named_admin_scope_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    repo_json, _ = _pass_pair()
    calls = _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps(repo_json)),
            _completed(rc=1, stderr="gh: Must have admin rights to Repository. (HTTP 403)"),
        ],
    )
    findings = bpc.compare_branch_protection(_FORGE)
    assert len(calls) == 2
    assert len(findings) == 1
    assert findings[0].status == "fail"
    assert "admin scope" in findings[0].message


def test_403_without_admin_rights_text_routes_to_generic_unknown_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [
            _completed(
                rc=1, stderr="gh: SAML enforcement is active for this organization. (HTTP 403)"
            )
        ],
    )
    findings = bpc.compare_branch_protection(_FORGE)
    assert len(findings) == 1
    assert findings[0].check == "branch_protection_readable"
    assert findings[0].status == "unknown"
    assert "admin scope" not in findings[0].message
    assert "SAML enforcement" in findings[0].message


def test_generic_gh_error_returns_single_unknown_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    _queued_run(monkeypatch, [subprocess.TimeoutExpired(cmd=["gh"], timeout=5)])
    findings = bpc.compare_branch_protection(_FORGE)
    assert len(findings) == 1
    assert findings[0].check == "branch_protection_readable"
    assert findings[0].status == "unknown"


def _run_full(
    monkeypatch: pytest.MonkeyPatch, repo_json: dict[str, Any], protection_json: dict[str, Any]
) -> tuple[bpc.Finding, ...]:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps(repo_json)),
            _completed(stdout=json.dumps(protection_json)),
        ],
    )
    return bpc.compare_branch_protection(_FORGE)


def test_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_json, protection_json = _pass_pair()
    findings = _run_full(monkeypatch, repo_json, protection_json)
    assert len(findings) == 5
    assert all(f.status == "pass" for f in findings)
    assert all(f.remedy == "" for f in findings)


def _break_conversation_resolution(repo: dict[str, Any], protection: dict[str, Any]) -> None:
    protection["required_conversation_resolution"] = {"enabled": False}


def _break_required_approvals(repo: dict[str, Any], protection: dict[str, Any]) -> None:
    protection["required_pull_request_reviews"] = {"required_approving_review_count": 2}


def _break_squash_merge(repo: dict[str, Any], protection: dict[str, Any]) -> None:
    repo["allow_squash_merge"] = False


def _break_delete_branch(repo: dict[str, Any], protection: dict[str, Any]) -> None:
    repo["delete_branch_on_merge"] = False


def _break_required_checks(repo: dict[str, Any], protection: dict[str, Any]) -> None:
    protection["required_status_checks"] = {"contexts": [], "checks": []}


_BREAKERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
    "conversation_resolution": _break_conversation_resolution,
    "required_approvals": _break_required_approvals,
    "squash_merge_enabled": _break_squash_merge,
    "delete_branch_on_merge": _break_delete_branch,
    "required_checks": _break_required_checks,
}

_MATRIX_BY_KEY = {row.key: row for row in bpc.COMPATIBILITY_MATRIX}


@pytest.mark.parametrize("row_key", sorted(_BREAKERS))
def test_per_row_mismatch_reports_remedy_from_matrix(
    monkeypatch: pytest.MonkeyPatch, row_key: str
) -> None:
    repo_json, protection_json = _pass_pair()
    _BREAKERS[row_key](repo_json, protection_json)
    findings = _run_full(monkeypatch, repo_json, protection_json)
    by_check = {f.check: f for f in findings}
    assert by_check[row_key].status == "fail"
    assert by_check[row_key].remedy == _MATRIX_BY_KEY[row_key].remedy
    for other_key, finding in by_check.items():
        if other_key != row_key:
            assert finding.status == "pass", f"{other_key} unexpectedly failed"


def test_required_checks_passes_when_authoritative_checks_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge = ForgeTarget(
        repo=RepoIdentity(owner="acme", repo="nocheck"),
        credential_env_name="ACME2_GH_TOKEN",
        authoritative_checks=frozenset(),
    )
    _with_token(monkeypatch)
    repo_json, protection_json = _pass_pair()
    protection_json["required_status_checks"] = {"contexts": [], "checks": []}
    _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps(repo_json)),
            _completed(stdout=json.dumps(protection_json)),
        ],
    )
    findings = bpc.compare_branch_protection(forge)
    by_check = {f.check: f for f in findings}
    assert by_check["required_checks"].status == "pass"


def test_required_checks_reads_checks_list_context_field(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_json, protection_json = _pass_pair()
    protection_json["required_status_checks"] = {
        "contexts": [],
        "checks": [{"context": "SonarCloud Code Analysis", "app_id": 1}],
    }
    findings = _run_full(monkeypatch, repo_json, protection_json)
    by_check = {f.check: f for f in findings}
    assert by_check["required_checks"].status == "pass"


def test_required_approvals_passes_when_review_block_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_json, protection_json = _pass_pair()
    del protection_json["required_pull_request_reviews"]
    findings = _run_full(monkeypatch, repo_json, protection_json)
    by_check = {f.check: f for f in findings}
    assert by_check["required_approvals"].status == "pass"


def test_as_dict_non_dict_returns_none() -> None:
    assert bpc._as_dict([1, 2]) is None  # noqa: SLF001
    assert bpc._as_dict("nope") is None  # noqa: SLF001
    assert bpc._as_dict({"a": 1}) == {"a": 1}  # noqa: SLF001


def test_as_str_list_non_list_returns_empty() -> None:
    assert bpc._as_str_list(None) == []  # noqa: SLF001
    assert bpc._as_str_list("not-a-list") == []  # noqa: SLF001
    assert bpc._as_str_list(["a", 1, "b"]) == ["a", "b"]  # noqa: SLF001


def test_required_check_contexts_missing_block_returns_empty_set() -> None:
    assert bpc._required_check_contexts({}) == set()  # noqa: SLF001


def test_required_check_contexts_missing_checks_key_still_returns_contexts() -> None:
    protection = {"required_status_checks": {"contexts": ["A"]}}
    assert bpc._required_check_contexts(protection) == {"A"}  # noqa: SLF001


def test_required_check_contexts_merges_and_ignores_malformed_check_items() -> None:
    protection = {
        "required_status_checks": {
            "contexts": ["A", "B"],
            "checks": [
                {"context": "C"},
                {"no_context": "nope"},
                "not-a-dict-item",
                123,
            ],
        }
    }
    assert bpc._required_check_contexts(protection) == {"A", "B", "C"}  # noqa: SLF001


def test_finding_remedy_url_defaults_empty() -> None:
    finding = bpc.Finding(check="x", status="pass", message="ok")
    assert finding.remedy_url == ""


def test_finding_remedy_url_settable() -> None:
    finding = bpc.Finding(check="x", status="fail", message="no", remedy_url="https://x")
    assert finding.remedy_url == "https://x"


def test_apply_no_credential_defers_all_rows_unknown_and_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch, None)

    def _boom(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called without a credential")

    monkeypatch.setattr(subprocess, "run", _boom)
    outcomes = bpc.apply_branch_protection(_FORGE)
    assert len(outcomes) == 5
    assert all(o.status == "deferred" for o in outcomes)
    assert all(o.finding is not None and o.finding.status == "unknown" for o in outcomes)


def test_apply_admin_scope_403_defers_all_rows_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [_completed(rc=1, stderr="gh: Must have admin rights to Repository. (HTTP 403)")],
    )
    outcomes = bpc.apply_branch_protection(_FORGE)
    assert len(outcomes) == 5
    assert all(o.status == "deferred" for o in outcomes)
    assert all(
        o.finding is not None
        and o.finding.status == "fail"
        and "admin scope" in o.finding.message
        for o in outcomes
    )


def test_apply_generic_repo_read_error_defers_all_rows_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    _queued_run(monkeypatch, [subprocess.TimeoutExpired(cmd=["gh"], timeout=5)])
    outcomes = bpc.apply_branch_protection(_FORGE)
    assert all(
        o.status == "deferred" and o.finding is not None and o.finding.status == "unknown"
        for o in outcomes
    )


def test_apply_success_marks_all_rows_applied_with_no_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    calls = _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps({"default_branch": "main"})),
            _completed(stdout="{}"),
            _completed(stdout="{}"),
        ],
    )
    outcomes = bpc.apply_branch_protection(_FORGE)
    assert len(calls) == 3
    assert all(o.status == "applied" and o.finding is None for o in outcomes)


def test_apply_patch_failure_defers_only_repo_setting_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps({"default_branch": "main"})),
            _completed(rc=1, stderr="patch failed"),
            _completed(stdout="{}"),
        ],
    )
    outcomes = bpc.apply_branch_protection(_FORGE)
    by_key = {o.key: o for o in outcomes}
    assert by_key["squash_merge_enabled"].status == "deferred"
    assert by_key["delete_branch_on_merge"].status == "deferred"
    assert by_key["conversation_resolution"].status == "applied"
    assert by_key["required_approvals"].status == "applied"
    assert by_key["required_checks"].status == "applied"


def test_apply_put_failure_defers_only_protection_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps({"default_branch": "main"})),
            _completed(stdout="{}"),
            _completed(rc=1, stderr="put failed"),
        ],
    )
    outcomes = bpc.apply_branch_protection(_FORGE)
    by_key = {o.key: o for o in outcomes}
    assert by_key["squash_merge_enabled"].status == "applied"
    assert by_key["delete_branch_on_merge"].status == "applied"
    assert by_key["conversation_resolution"].status == "deferred"
    assert by_key["required_approvals"].status == "deferred"
    assert by_key["required_checks"].status == "deferred"


def test_apply_deferred_finding_uses_matrix_remedy_verbatim_and_settings_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    _queued_run(
        monkeypatch,
        [
            _completed(stdout=json.dumps({"default_branch": "main"})),
            _completed(rc=1, stderr="patch failed"),
            _completed(rc=1, stderr="put failed"),
        ],
    )
    outcomes = bpc.apply_branch_protection(_FORGE)
    matrix_by_key = {row.key: row for row in bpc.COMPATIBILITY_MATRIX}
    for outcome in outcomes:
        assert outcome.finding is not None
        assert outcome.finding.remedy == matrix_by_key[outcome.key].remedy
        assert (
            outcome.finding.remedy_url
            == f"https://github.com/{_FORGE.repo.slug}/settings/branches"
        )


def test_apply_sends_required_status_checks_from_forge_authoritative_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    calls: list[list[str]] = []
    bodies: list[dict[str, Any]] = []

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "input" in kwargs:
            bodies.append(json.loads(kwargs["input"]))
        if len(calls) == 1:
            return _completed(stdout=json.dumps({"default_branch": "main"}))
        return _completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", _run)
    bpc.apply_branch_protection(_FORGE)
    put_body = bodies[-1]
    assert put_body["required_status_checks"]["contexts"] == sorted(_FORGE.authoritative_checks)
    assert put_body["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert put_body["required_conversation_resolution"] is True


def test_apply_patch_raises_defers_only_repo_setting_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_token(monkeypatch)
    calls = {"n": 0}

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return _completed(stdout=json.dumps({"default_branch": "main"}))
        if "-X" in cmd and cmd[cmd.index("-X") + 1] == "PATCH":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
        return _completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", _run)
    outcomes = bpc.apply_branch_protection(_FORGE)
    by_key = {o.key: o for o in outcomes}
    assert by_key["squash_merge_enabled"].status == "deferred"
    assert by_key["conversation_resolution"].status == "applied"


def test_apply_put_raises_defers_only_protection_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_token(monkeypatch)
    calls = {"n": 0}

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return _completed(stdout=json.dumps({"default_branch": "main"}))
        if "-X" in cmd and cmd[cmd.index("-X") + 1] == "PUT":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
        return _completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", _run)
    outcomes = bpc.apply_branch_protection(_FORGE)
    by_key = {o.key: o for o in outcomes}
    assert by_key["squash_merge_enabled"].status == "applied"
    assert by_key["conversation_resolution"].status == "deferred"


def test_invoke_gh_api_write_oserror_raises_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    _queued_run(monkeypatch, [FileNotFoundError("no gh binary")])
    with pytest.raises(bpc._GhApiCallError):  # noqa: SLF001
        bpc._invoke_gh_api_write(["-X", "PATCH", "repos/x"], token="t", timeout=5, body={})  # noqa: SLF001

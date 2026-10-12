from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _forge_errors, _pr_branch
from tests._pr_branch_support import _audit_entries
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_RATE_LIMIT_STDERR = "GraphQL: API rate limit already exceeded for user ID 24228929.\n"
_NOT_FOUND_STDERR = (
    "GraphQL: Could not resolve to a PullRequest with the number of 42. "
    "(repository.pullRequest)"
)
_NETWORK_STDERR = 'Post "https://api.github.com/graphql": dial tcp: i/o timeout'
_WIDGET = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


@pytest.fixture(autouse=True)
def _logs_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _actions(tmp_path: Path) -> list[object]:
    return [e["action"] for e in _audit_entries(tmp_path)]


def test_resolve_not_found_returns_none_with_not_found_audit(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("pr_head_branch", api_result(returncode=1, stderr=_NOT_FOUND_STDERR))
    assert _pr_branch.resolve_existing_branch(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None
    entries = _audit_entries(tmp_path)
    assert [e["action"] for e in entries] == ["pr_view_not_found"]
    assert entries[0]["pr_number"] == 42
    assert "Could not resolve to a PullRequest" in str(entries[0]["stderr"])


def test_resolve_rate_limit_raises_forge_rate_limited(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("pr_head_branch", api_result(returncode=1, stderr=_RATE_LIMIT_STDERR))
    with pytest.raises(_forge_errors.ForgeRateLimited) as excinfo:
        _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert "gh pr view PR #42 failed (rc=1)" in excinfo.value.raw_message
    entries = _audit_entries(tmp_path)
    assert [e["action"] for e in entries] == ["pr_view_failed"]
    assert entries[0]["failure_class"] == "rate_limited"


def test_resolve_network_failure_raises_forge_transient(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("pr_head_branch", api_result(returncode=1, stderr=_NETWORK_STDERR))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert _actions(tmp_path) == ["pr_view_failed"]


def test_resolve_auth_failure_raises_forge_transient(forge: ForgeScript) -> None:
    forge.queue("pr_head_branch", api_result(returncode=1, stderr="Bad credentials"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_resolve_unknown_failure_raises_forge_transient(forge: ForgeScript) -> None:
    forge.queue("pr_head_branch", api_result(returncode=1, stderr="something inscrutable"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


@pytest.mark.parametrize(
    "fault",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired(cmd=["gh"], timeout=60.0)],
    ids=["missing-binary", "timeout"],
)
def test_resolve_transport_fault_raises_forge_transient_with_audit(
    forge: ForgeScript, tmp_path: Path, fault: BaseException,
) -> None:
    forge.queue("pr_head_branch", fault)
    with pytest.raises(_forge_errors.ForgeTransientError) as excinfo:
        _pr_branch.resolve_existing_branch(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert excinfo.value.__cause__ is fault
    entries = _audit_entries(tmp_path)
    assert [e["action"] for e in entries] == ["pr_view_failed"]
    assert entries[0]["error"] == str(fault)


def test_resolve_success_and_empty_paths_unchanged(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("pr_head_branch", api_result("teams/x\n"), api_result(""))
    assert _pr_branch.resolve_existing_branch(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) == "teams/x"
    assert _pr_branch.resolve_existing_branch(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None
    assert _actions(tmp_path) == ["pr_view_ok", "pr_view_empty"]


def test_resolve_asks_the_port_for_the_head_branch_of_the_forge_target(
    forge: ForgeScript,
) -> None:
    forge.queue("pr_head_branch", api_result("teams/x\n"))
    _pr_branch.resolve_existing_branch(42, "task-x", forge_target=_WIDGET)
    assert forge.calls("pr_head_branch") == [((42,), {"forge_target": _WIDGET})]


def test_resolve_without_a_pr_number_never_reaches_the_port(forge: ForgeScript) -> None:
    assert _pr_branch.resolve_existing_branch(
        None, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None
    assert forge.calls("pr_head_branch") == []


def test_handle_pr_view_failure_accepts_an_api_result(tmp_path: Path) -> None:
    result = api_result(returncode=1, stderr=_NOT_FOUND_STDERR)
    _pr_branch._handle_pr_view_failure("task-x", 42, "gh pr view PR #42", result)
    assert _actions(tmp_path) == ["pr_view_not_found"]


def test_update_pr_branch_outcome_updated_on_success(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("request_branch_update", api_result())
    outcome = _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.UPDATED
    assert _actions(tmp_path) == ["pr_update_branch_ok"]


def test_update_pr_branch_outcome_conflict_on_conflict_shaped_stderr(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue(
        "request_branch_update",
        api_result(returncode=1, stderr="not mergeable: merge conflict detected"),
    )
    outcome = _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.CONFLICT
    assert _actions(tmp_path) == ["pr_update_branch_failed"]


def test_update_pr_branch_outcome_failed_on_other_stderr(forge: ForgeScript) -> None:
    forge.queue("request_branch_update", api_result(returncode=1, stderr="permission denied"))
    outcome = _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.FAILED


def test_update_pr_branch_outcome_failure_row_carries_returncode_and_capped_stderr(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("request_branch_update", api_result(returncode=4, stderr="e" * 800))
    _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    [row] = _audit_entries(tmp_path)
    del row["timestamp_utc"]
    assert row == {
        "task_id": "task-x", "action": "pr_update_branch_failed", "pr_number": 42,
        "returncode": 4, "stderr": "e" * 500,
    }


@pytest.mark.parametrize(
    "fault",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired(cmd=["gh"], timeout=60.0)],
    ids=["missing-binary", "timeout"],
)
def test_update_pr_branch_outcome_failed_on_transport_fault_with_audit(
    forge: ForgeScript, tmp_path: Path, fault: BaseException,
) -> None:
    forge.queue("request_branch_update", fault)
    outcome = _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.FAILED
    entries = _audit_entries(tmp_path)
    assert [e["action"] for e in entries] == ["pr_update_branch_failed"]
    assert entries[0]["error"] == str(fault)


def test_update_pr_branch_outcome_issues_only_the_update_attempt(
    forge: ForgeScript,
) -> None:
    forge.queue("request_branch_update", api_result())
    _pr_branch.update_pr_branch_outcome(42, "task-x", forge_target=_WIDGET)
    assert forge.calls("request_branch_update") == [((42,), {"forge_target": _WIDGET})]
    assert [name for name, _a, _k in forge.fake.calls] == ["request_branch_update"]


def test_classify_credential_failure_maps_auth_to_expired_token() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="Bad credentials"
    )
    assert result == "expired_token"


def test_classify_credential_failure_keeps_rate_limit_transient() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr=_RATE_LIMIT_STDERR
    )
    assert result == "transient"


def test_classify_credential_failure_missing_token_short_circuits() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=False, returncode=1, stderr="Bad credentials"
    )
    assert result == "missing_token"


def test_classify_credential_failure_unknown_stderr_stays_transient() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="something inscrutable"
    )
    assert result == "transient"


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [("OPEN\n", True), ("CLOSED\n", False), ("MERGED\n", False)],
)
def test_pr_is_open_classifies_state(
    forge: ForgeScript, stdout: str, expected: bool,
) -> None:
    forge.queue("pr_open_state", api_result(stdout))
    assert _pr_branch.pr_is_open(42, "task-x", forge_target=DEFAULT_FORGE_TARGET) is expected


def test_pr_is_open_none_on_nonzero_returncode(forge: ForgeScript) -> None:
    forge.queue("pr_open_state", api_result(returncode=1, stderr="not found"))
    assert _pr_branch.pr_is_open(42, "task-x", forge_target=DEFAULT_FORGE_TARGET) is None


def test_pr_is_open_none_on_empty_stdout(forge: ForgeScript) -> None:
    forge.queue("pr_open_state", api_result(""))
    assert _pr_branch.pr_is_open(42, "task-x", forge_target=DEFAULT_FORGE_TARGET) is None


@pytest.mark.parametrize(
    "fault",
    [
        subprocess.TimeoutExpired(cmd=["gh"], timeout=60.0),
        FileNotFoundError("gh not on PATH"),
        RuntimeError("no active forge installed"),
    ],
    ids=["timeout", "missing-binary", "any-exception"],
)
def test_pr_is_open_none_on_raised_exception(forge: ForgeScript, fault: BaseException) -> None:
    forge.queue("pr_open_state", fault)
    assert _pr_branch.pr_is_open(42, "task-x", forge_target=DEFAULT_FORGE_TARGET) is None


def test_pr_is_open_passes_the_forge_target_to_the_port(forge: ForgeScript) -> None:
    forge.queue("pr_open_state", api_result("OPEN\n"))
    _pr_branch.pr_is_open(7, "task-x", forge_target=_WIDGET)
    assert forge.calls("pr_open_state") == [((7,), {"forge_target": _WIDGET})]


def test_head_sha_raised_fault_reports_the_subprocess_error(forge: ForgeScript) -> None:
    failures: list[tuple[int, str]] = []
    fault = FileNotFoundError("gh not on PATH")
    forge.queue("head_sha", fault)
    sha = _pr_branch._fetch_pr_head_sha(
        42, on_fail=lambda pr, reason: failures.append((pr, reason)),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert sha is None
    assert failures == [(42, f"gh subprocess error: {fault}")]


def test_head_sha_timeout_reports_the_subprocess_error(forge: ForgeScript) -> None:
    failures: list[tuple[int, str]] = []
    fault = subprocess.TimeoutExpired(cmd=["gh"], timeout=60.0)
    forge.queue("head_sha", fault)
    sha = _pr_branch._fetch_pr_head_sha(
        42, on_fail=lambda pr, reason: failures.append((pr, reason)),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert sha is None
    assert failures == [(42, f"gh subprocess error: {fault}")]


def test_head_sha_nonzero_exit_reports_returncode_and_capped_stderr(forge: ForgeScript) -> None:
    failures: list[tuple[int, str]] = []
    forge.queue("head_sha", api_result(returncode=3, stderr="s" * 300))
    sha = _pr_branch._fetch_pr_head_sha(
        42, on_fail=lambda pr, reason: failures.append((pr, reason)),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert sha is None
    assert failures == [(42, f"gh returncode=3: {'s' * 200}")]


@pytest.mark.parametrize("stdout", ["", "  \n"], ids=["empty", "whitespace"])
def test_head_sha_blank_output_reports_head_sha_empty(
    forge: ForgeScript, stdout: str,
) -> None:
    failures: list[tuple[int, str]] = []
    forge.queue("head_sha", api_result(stdout))
    sha = _pr_branch._fetch_pr_head_sha(
        42, on_fail=lambda pr, reason: failures.append((pr, reason)),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert sha is None
    assert failures == [(42, "head_sha_empty")]


def test_head_sha_success_returns_the_stripped_sha_without_reporting(
    forge: ForgeScript,
) -> None:
    failures: list[tuple[int, str]] = []
    forge.queue("head_sha", api_result("abc123\n"))
    sha = _pr_branch._fetch_pr_head_sha(
        42, forge_target=_WIDGET, on_fail=lambda pr, reason: failures.append((pr, reason)),
    )
    assert sha == "abc123"
    assert failures == []
    assert forge.calls("head_sha") == [((42,), {"forge_target": _WIDGET})]


def test_flake_guard_head_sha_passes_the_reason_through_to_the_audit_row(
    forge: ForgeScript, tmp_path: Path,
) -> None:
    forge.queue("head_sha", api_result(returncode=1, stderr="boom"))
    assert _pr_branch.fetch_flake_guard_head_sha(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) is None
    entries = _audit_entries(tmp_path)
    assert [(e["task_id"], e["action"], e["pr_number"], e["reason"]) for e in entries] == [
        ("task-x", "flake_head_sha_fetch_failed", 42, "gh returncode=1: boom"),
    ]


def test_flake_guard_head_sha_returns_the_sha_on_success(forge: ForgeScript) -> None:
    forge.queue("head_sha", api_result("deadbeef\n"))
    assert _pr_branch.fetch_flake_guard_head_sha(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET,
    ) == "deadbeef"


def test_open_prs_success_returns_the_parsed_titles(forge: ForgeScript) -> None:
    forge.queue(
        "open_pr_titles",
        api_result('[{"number": 3, "title": "t"}, {"number": "x", "title": "bad"}]'),
    )
    assert _pr_branch.list_open_prs() == [_pr_branch.OpenPrInfo(number=3, title="t")]
    assert forge.calls("open_pr_titles") == [((), {"forge_target": DEFAULT_FORGE_TARGET})]


@pytest.mark.parametrize(
    "fault",
    [FileNotFoundError("gh not on PATH"), subprocess.TimeoutExpired(cmd=["gh"], timeout=60.0)],
    ids=["missing-binary", "timeout"],
)
def test_open_prs_empty_on_transport_fault(forge: ForgeScript, fault: BaseException) -> None:
    forge.queue("open_pr_titles", fault)
    assert _pr_branch.list_open_prs() == []


def test_open_prs_empty_on_nonzero_exit(forge: ForgeScript) -> None:
    forge.queue("open_pr_titles", api_result('[{"number": 3, "title": "t"}]', returncode=1))
    assert _pr_branch.list_open_prs() == []


def test_open_prs_empty_on_unparseable_output(forge: ForgeScript) -> None:
    forge.queue("open_pr_titles", api_result("not json"))
    assert _pr_branch.list_open_prs() == []


def test_open_prs_propagates_other_os_errors(forge: ForgeScript) -> None:
    forge.queue("open_pr_titles", PermissionError("denied"))
    with pytest.raises(PermissionError):
        _pr_branch.list_open_prs()

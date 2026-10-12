from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Literal

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import RowsRead
from alan.forge_gh import GhForge

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)
_FORGE = GhForge()
_ENV = {"GH_TOKEN": "transport-tok"}
_TRANSPORT_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]

_ENVELOPE_CALLS: dict[str, tuple[Callable[[], RowsRead], str]] = {
    "commit_check_runs": (
        lambda: _FORGE.commit_check_runs("main", forge_target=_TARGET, timeout=9.0),
        "check_runs",
    ),
    "latest_workflow_run": (
        lambda: _FORGE.latest_workflow_run("tests.yml", forge_target=_TARGET, timeout=9.0),
        "workflow_runs",
    ),
    "workflows": (
        lambda: _FORGE.workflows(forge_target=_TARGET, timeout=9.0),
        "workflows",
    ),
}
_ENVELOPE_OPS = list(_ENVELOPE_CALLS)


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout: str | None = ""
        self.stderr: str | None = ""
        self.returncode = 0
        self.raises: BaseException | None = None

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr,
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Transport:
    fake = _Transport()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *_a, **_k: "transport-tok")
    return fake


def _envelope(op: str, transport: _Transport, body: str) -> RowsRead:
    transport.stdout = body
    return _ENVELOPE_CALLS[op][0]()


def test_default_branch_builds_the_repo_argv_and_returns_the_declared_branch(
    transport: _Transport,
) -> None:
    transport.stdout = '{"default_branch": "trunk"}'
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) == "trunk"
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "repos/acme/widget"]
    assert kwargs == {"cwd": None, "timeout": 11.0, "env": _ENV}


@pytest.mark.parametrize(
    "body",
    ["{}", '{"default_branch": ""}', '{"default_branch": 7}', '{"default_branch": null}'],
)
def test_default_branch_falls_back_to_main_for_a_missing_empty_or_non_string_value(
    body: str, transport: _Transport,
) -> None:
    transport.stdout = body
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) == "main"


def test_default_branch_is_none_on_a_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stdout = '{"default_branch": "trunk"}'
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) is None


@pytest.mark.parametrize("body", ["[1, 2]", "not json", "", '"main"'])
def test_default_branch_is_none_when_a_successful_body_is_not_an_object(
    body: str, transport: _Transport,
) -> None:
    transport.stdout = body
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) is None


@pytest.mark.parametrize("error", _TRANSPORT_FAULTS)
def test_default_branch_is_none_on_a_transport_fault(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) is None


def test_default_branch_treats_a_missing_stdout_as_a_failed_read(transport: _Transport) -> None:
    transport.stdout = None
    assert _FORGE.default_branch(forge_target=_TARGET, timeout=11.0) is None


def test_commit_check_runs_builds_the_check_runs_argv_for_the_ref(
    transport: _Transport,
) -> None:
    transport.stdout = '{"check_runs": [{"name": "a"}]}'
    result = _FORGE.commit_check_runs("main", forge_target=_TARGET, timeout=13.0)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "repos/acme/widget/commits/main/check-runs"]
    assert kwargs == {"cwd": None, "timeout": 13.0, "env": _ENV}
    assert result == RowsRead(outcome="ok", rows=({"name": "a"},), returncode=0, stderr="")


def test_latest_workflow_run_without_a_commit_filter_queries_only_the_page_size(
    transport: _Transport,
) -> None:
    transport.stdout = '{"workflow_runs": [{"status": "completed"}]}'
    result = _FORGE.latest_workflow_run("77", forge_target=_TARGET, timeout=14.0)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "repos/acme/widget/actions/workflows/77/runs?per_page=1"]
    assert kwargs == {"cwd": None, "timeout": 14.0, "env": _ENV}
    assert result.rows == ({"status": "completed"},)


def test_latest_workflow_run_with_a_commit_filter_queries_the_head_sha_before_the_page_size(
    transport: _Transport,
) -> None:
    transport.stdout = '{"workflow_runs": [{"check_suite_id": 5}]}'
    result = _FORGE.latest_workflow_run(
        "tests.yml", forge_target=_TARGET, timeout=14.0, head_sha="abc1234def",
    )
    assert transport.calls[0][0] == [
        "gh", "api",
        "repos/acme/widget/actions/workflows/tests.yml/runs?head_sha=abc1234def&per_page=1",
    ]
    assert result.rows == ({"check_suite_id": 5},)


def test_latest_workflow_run_percent_encodes_the_workflow_and_the_head_sha(
    transport: _Transport,
) -> None:
    _FORGE.latest_workflow_run(
        "../../evil.yml", forge_target=_TARGET, timeout=14.0, head_sha="a&b=c",
    )
    path = transport.calls[0][0][2]
    workflow_segment = path.split("/actions/workflows/", 1)[1].split("/runs?", 1)[0]
    assert workflow_segment == "..%2F..%2Fevil.yml"
    assert path.endswith("/runs?head_sha=a%26b%3Dc&per_page=1")


def test_latest_workflow_run_reports_a_lone_non_object_run_as_no_rows(
    transport: _Transport,
) -> None:
    transport.stdout = '{"workflow_runs": ["nope"]}'
    result = _FORGE.latest_workflow_run("77", forge_target=_TARGET, timeout=14.0)
    assert result.outcome == "ok"
    assert result.rows == ()


def test_workflows_builds_the_workflow_list_argv(transport: _Transport) -> None:
    transport.stdout = '{"workflows": [{"id": 1}]}'
    result = _FORGE.workflows(forge_target=_TARGET, timeout=15.0)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "repos/acme/widget/actions/workflows"]
    assert kwargs == {"cwd": None, "timeout": 15.0, "env": _ENV}
    assert result.rows == ({"id": 1},)


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
def test_envelope_ops_report_ok_with_empty_rows_for_an_empty_list(
    op: str, transport: _Transport,
) -> None:
    key = _ENVELOPE_CALLS[op][1]
    result = _envelope(op, transport, f'{{"{key}": []}}')
    assert result.outcome == "ok"
    assert result.rows == ()


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
@pytest.mark.parametrize("body", ["{}", '{"other": []}'])
def test_envelope_ops_report_ok_with_no_rows_when_the_key_is_missing(
    op: str, body: str, transport: _Transport,
) -> None:
    result = _envelope(op, transport, body)
    assert result.outcome == "ok"
    assert result.rows is None


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
@pytest.mark.parametrize("value", ['"nope"', "{}", "5", "null"])
def test_envelope_ops_report_ok_with_no_rows_when_the_key_is_not_a_list(
    op: str, value: str, transport: _Transport,
) -> None:
    key = _ENVELOPE_CALLS[op][1]
    result = _envelope(op, transport, f'{{"{key}": {value}}}')
    assert result.outcome == "ok"
    assert result.rows is None


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
def test_envelope_ops_drop_non_object_items(op: str, transport: _Transport) -> None:
    key = _ENVELOPE_CALLS[op][1]
    result = _envelope(op, transport, f'{{"{key}": ["nope", {{"id": 1}}, 3, null, [1]]}}')
    assert result.rows == ({"id": 1},)


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
def test_envelope_ops_report_failed_with_the_exit_status_on_a_nonzero_exit(
    op: str, transport: _Transport,
) -> None:
    transport.returncode = 4
    transport.stderr = "HTTP 403"
    assert _envelope(op, transport, '{"anything": []}') == RowsRead(
        outcome="failed", rows=None, returncode=4, stderr="HTTP 403",
    )


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
@pytest.mark.parametrize("body", ["[1, 2]", "not json", "", '"text"'])
def test_envelope_ops_report_failed_when_a_successful_exit_carries_a_non_object_body(
    op: str, body: str, transport: _Transport,
) -> None:
    transport.stderr = "warning: something"
    assert _envelope(op, transport, body) == RowsRead(
        outcome="failed", rows=None, returncode=0, stderr="warning: something",
    )


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
def test_envelope_ops_treat_missing_output_streams_as_empty(
    op: str, transport: _Transport,
) -> None:
    transport.stderr = None
    result = _envelope(op, transport, "")
    assert result.outcome == "failed"
    assert result.stderr == ""


@pytest.mark.parametrize("op", _ENVELOPE_OPS)
@pytest.mark.parametrize("error", _TRANSPORT_FAULTS)
def test_envelope_ops_report_raised_on_a_transport_fault(
    op: str, error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _ENVELOPE_CALLS[op][0]()
    assert result.outcome == "raised"
    assert result.rows is None
    assert result.returncode == -1
    assert str(error) in result.stderr


@pytest.mark.parametrize("state", ["open", "all"])
def test_prs_by_author_builds_the_list_argv_with_author_state_fields_and_limit(
    state: Literal["open", "all"], transport: _Transport,
) -> None:
    transport.stdout = '[{"createdAt": "c", "url": "u"}]'
    result = _FORGE.prs_by_author(
        "app/dependabot", ("createdAt", "url"),
        forge_target=_TARGET, state=state, limit=1, timeout=16.0,
    )
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "list", "--repo", "acme/widget", "--author", "app/dependabot",
        "--state", state, "--json", "createdAt,url", "--limit", "1",
    ]
    assert kwargs == {"cwd": None, "timeout": 16.0, "env": _ENV}
    assert result == RowsRead(
        outcome="ok", rows=({"createdAt": "c", "url": "u"},), returncode=0, stderr="",
    )


def test_prs_by_author_reports_an_empty_listing_as_ok_with_no_rows(transport: _Transport) -> None:
    transport.stdout = "[]"
    result = _FORGE.prs_by_author(
        "@me", ("number",), forge_target=_TARGET, state="open", limit=100, timeout=16.0,
    )
    assert result.outcome == "ok"
    assert result.rows == ()


def test_prs_by_author_reports_a_failed_listing_distinct_from_an_empty_one(
    transport: _Transport,
) -> None:
    transport.returncode = 1
    transport.stderr = "denied"
    result = _FORGE.prs_by_author(
        "@me", ("number",), forge_target=_TARGET, state="open", limit=100, timeout=16.0,
    )
    assert result == RowsRead(outcome="failed", rows=None, returncode=1, stderr="denied")


@pytest.mark.parametrize("body", ["not json", '{"not": "a list"}'])
def test_prs_by_author_reports_an_unparseable_listing_as_ok_with_unknown_rows(
    body: str, transport: _Transport,
) -> None:
    transport.stdout = body
    result = _FORGE.prs_by_author(
        "@me", ("number",), forge_target=_TARGET, state="open", limit=100, timeout=16.0,
    )
    assert result.outcome == "ok"
    assert result.rows is None


def test_prs_by_author_drops_non_object_rows(transport: _Transport) -> None:
    transport.stdout = '["nope", {"number": 1}, 2]'
    result = _FORGE.prs_by_author(
        "@me", ("number",), forge_target=_TARGET, state="open", limit=100, timeout=16.0,
    )
    assert result.rows == ({"number": 1},)


@pytest.mark.parametrize("error", _TRANSPORT_FAULTS)
def test_prs_by_author_reports_raised_on_a_transport_fault(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _FORGE.prs_by_author(
        "@me", ("number",), forge_target=_TARGET, state="open", limit=100, timeout=16.0,
    )
    assert result.outcome == "raised"
    assert result.rows is None


def test_a_padded_secret_is_sent_stripped_as_the_gh_token(
    transport: _Transport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *_a, **_k: "  padded-tok\n")
    _FORGE.workflows(forge_target=_TARGET, timeout=9.0)
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "padded-tok"}


@pytest.mark.parametrize("secret", [None, "", "   \t"])
def test_an_absent_secret_sends_no_environment_override(
    secret: str | None, transport: _Transport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *_a, **_k: secret)
    _FORGE.workflows(forge_target=_TARGET, timeout=9.0)
    assert transport.calls[0][1]["env"] is None

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Literal

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import RowsRead
from alan.forge_gh import GhForge

_SENTINEL_ENV_NAME = "SENTINEL_TRIAGE_CREDENTIAL_ENV"
_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name=_SENTINEL_ENV_NAME,  # noqa: S106  # nosec B106
)
_FORGE = GhForge()
_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]
_RAISED_FAULTS = [*_FAULTS, PermissionError("denied")]
_REF_FIELDS = "number,state,headRefName"
_ROLLUP_FIELDS = "number,reviewDecision,mergeStateStatus,statusCheckRollup"
_RUN_FIELDS = "databaseId,status,conclusion,headSha"


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout: str | None = ""
        self.stderr: str | None = "denied"
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
    monkeypatch.setattr(secrets_store, "transport_credential", lambda _t: "transport-tok")
    return fake


def _prefix_list(
    state: Literal["open", "all"] = "all", fields: str = _REF_FIELDS, limit: int = 100,
    timeout: float = 30,
) -> RowsRead:
    return _FORGE.prs_by_head_prefix(
        "teams/", tuple(fields.split(",")),
        forge_target=_TARGET, state=state, limit=limit, timeout=timeout,
    )


def _latest_run(timeout: float = 30) -> RowsRead:
    return _FORGE.latest_branch_run(
        "tests.yml", "main", forge_target=_TARGET, timeout=timeout,
    )


_OPERATIONS: dict[str, Callable[[], RowsRead]] = {
    "prs_by_head_prefix": _prefix_list,
    "latest_branch_run": _latest_run,
}
_OPERATION_NAMES = list(_OPERATIONS)


@pytest.mark.parametrize(
    ("state", "fields", "limit"),
    [("all", _REF_FIELDS, 100), ("open", _ROLLUP_FIELDS, 30)],
)
def test_prs_by_head_prefix_builds_the_prefix_search_list_argv(
    state: Literal["open", "all"], fields: str, limit: int, transport: _Transport,
) -> None:
    _prefix_list(state, fields, limit)
    assert transport.calls == [
        (
            [
                "gh", "pr", "list", "--repo", "acme/widget", "--search", "head:teams/",
                "--state", state, "--json", fields, "--limit", str(limit),
            ],
            {"cwd": None, "timeout": 30, "env": None},
        ),
    ]


def test_latest_branch_run_builds_the_branch_filtered_run_list_argv(
    transport: _Transport,
) -> None:
    _latest_run()
    assert transport.calls == [
        (
            [
                "gh", "run", "list", "--repo", "acme/widget", "--workflow", "tests.yml",
                "--branch", "main", "--limit", "1", "--json", _RUN_FIELDS,
            ],
            {"cwd": None, "timeout": 30, "env": None},
        ),
    ]


def test_latest_branch_run_puts_the_limit_before_the_json_fields(transport: _Transport) -> None:
    _latest_run()
    argv = transport.calls[0][0]
    assert argv.index("--limit") < argv.index("--json")


def test_latest_branch_run_honours_the_requested_workflow_and_branch(
    transport: _Transport,
) -> None:
    _FORGE.latest_branch_run("ci-deploy.yml", "release", forge_target=_TARGET, timeout=30)
    argv = transport.calls[0][0]
    assert argv[argv.index("--workflow") + 1] == "ci-deploy.yml"
    assert argv[argv.index("--branch") + 1] == "release"


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_run_under_the_ambient_identity_even_when_a_token_resolves(
    op: str, transport: _Transport,
) -> None:
    _OPERATIONS[op]()
    argv, kwargs = transport.calls[0]
    assert kwargs["env"] is None
    assert _SENTINEL_ENV_NAME not in argv
    assert _SENTINEL_ENV_NAME not in repr(transport.calls)
    assert "transport-tok" not in repr(transport.calls)


def test_prs_by_head_prefix_passes_the_timeout_through(transport: _Transport) -> None:
    _prefix_list(timeout=17.5)
    assert transport.calls[0][1]["timeout"] == 17.5


def test_latest_branch_run_passes_the_timeout_through(transport: _Transport) -> None:
    _latest_run(timeout=17.5)
    assert transport.calls[0][1]["timeout"] == 17.5


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_return_every_object_row(op: str, transport: _Transport) -> None:
    listing = [{"number": 1, "state": "OPEN"}, {"number": 2, "state": "MERGED"}]
    transport.stdout = json.dumps(listing)
    assert _OPERATIONS[op]() == RowsRead(
        outcome="ok", rows=tuple(listing), returncode=0, stderr="denied",
    )


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_report_an_empty_listing_as_ok_with_no_rows(
    op: str, transport: _Transport,
) -> None:
    transport.stdout = "[]"
    result = _OPERATIONS[op]()
    assert result.outcome == "ok"
    assert result.rows == ()


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_report_a_failed_listing_on_nonzero_exit(
    op: str, transport: _Transport,
) -> None:
    transport.returncode = 1
    transport.stdout = json.dumps([{"number": 1}])
    assert _OPERATIONS[op]() == RowsRead(
        outcome="failed", rows=None, returncode=1, stderr="denied",
    )


@pytest.mark.parametrize("op", _OPERATION_NAMES)
@pytest.mark.parametrize("body", ["not json", '{"number": 1}', "", None])
def test_triage_operations_report_an_unparseable_listing_as_ok_with_unknown_rows(
    op: str, body: str | None, transport: _Transport,
) -> None:
    transport.stdout = body
    result = _OPERATIONS[op]()
    assert result.outcome == "ok"
    assert result.rows is None


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_drop_non_object_items(op: str, transport: _Transport) -> None:
    transport.stdout = json.dumps(["nope", 3, None, [1], {"number": 7}])
    assert _OPERATIONS[op]().rows == ({"number": 7},)


@pytest.mark.parametrize("op", _OPERATION_NAMES)
@pytest.mark.parametrize("error", _RAISED_FAULTS)
def test_triage_operations_report_raised_on_a_transport_fault(
    op: str, error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _OPERATIONS[op]()
    assert result.outcome == "raised"
    assert result.rows is None
    assert result.returncode == -1
    assert str(error) in result.stderr


@pytest.mark.parametrize("op", _OPERATION_NAMES)
def test_triage_operations_treat_missing_output_streams_as_empty(
    op: str, transport: _Transport,
) -> None:
    transport.stdout = None
    transport.stderr = None
    result = _OPERATIONS[op]()
    assert result.stderr == ""

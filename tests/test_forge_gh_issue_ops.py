from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, RowsRead
from alan.forge_gh import GhForge
from alan.teams._pr_forge import GH_TIMEOUT_S

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)
_FORGE = GhForge()
_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]
_RAISED_FAULTS = [*_FAULTS, PermissionError("denied")]
_TOKEN = "transport-tok"  # noqa: S105  # nosec B105
_PADDED_TOKEN = "  tok\n"  # noqa: S105  # nosec B105
_CALL_KWARGS = {"cwd": None, "timeout": GH_TIMEOUT_S}
_TRANSPORT_KWARGS = {"cwd": None, "timeout": GH_TIMEOUT_S, "env": {"GH_TOKEN": _TOKEN}}


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.payloads: list[object] = []
        self.stdout: str | None = ""
        self.returncode = 0
        self.raises: BaseException | None = None
        self.token: str | None = _TOKEN
        self.resolved: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def resolve_secret(self, *args: object, **kwargs: object) -> str | None:
        self.resolved.append((args, kwargs))
        return self.token

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        if "--input" in cmd:
            self.payloads.append(json.loads(Path(cmd[cmd.index("--input") + 1]).read_text()))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr="denied",
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Transport:
    fake = _Transport()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "resolve_secret", fake.resolve_secret)
    return fake


def _search(query: str = "needle") -> tuple[dict[str, object], ...]:
    return _FORGE.search_open_issues(query, forge_target=_TARGET, label="triage")


def _open_prs(
    fields: tuple[str, ...] = ("number", "mergeable", "title"),
) -> tuple[dict[str, object], ...]:
    return _FORGE.open_prs_by_author(
        "app/dependabot", fields, forge_target=_TARGET, limit=100,
    )


def _label() -> bool:
    return _FORGE.create_label(
        "triage", forge_target=_TARGET, color="d4c5f9", description="Needs a look",
    )


def _issue() -> bool:
    return _FORGE.create_issue("Title", "Body", forge_target=_TARGET, label="triage")


def _issue_search(title: str = "needle") -> RowsRead:
    return _FORGE.open_issues_by_title(title, forge_target=_TARGET, label="unblocker")


def _numbered_issue() -> ApiResult:
    return _FORGE.create_numbered_issue("Title", "Body", forge_target=_TARGET, label="unblocker")


def _issue_comment() -> ApiResult:
    return _FORGE.comment_on_issue(42, "hello", forge_target=_TARGET)


def test_pr_patch_builds_the_pr_diff_argv_with_the_default_timeout(
    transport: _Transport,
) -> None:
    transport.stdout = "-x\n+y\n"
    assert _FORGE.pr_patch(42, forge_target=_TARGET) == "-x\n+y\n"
    assert transport.calls == [
        (["gh", "pr", "diff", "42", "--repo", "acme/widget"], _CALL_KWARGS),
    ]


def test_pr_patch_returns_none_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stdout = "partial"
    assert _FORGE.pr_patch(42, forge_target=_TARGET) is None


@pytest.mark.parametrize("error", _FAULTS)
def test_pr_patch_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _FORGE.pr_patch(42, forge_target=_TARGET) is None


def test_pr_patch_keeps_an_empty_diff_distinct_from_a_fault(transport: _Transport) -> None:
    transport.stdout = ""
    assert _FORGE.pr_patch(42, forge_target=_TARGET) == ""


def test_pr_patch_treats_missing_output_as_an_empty_diff(transport: _Transport) -> None:
    transport.stdout = None
    assert _FORGE.pr_patch(42, forge_target=_TARGET) == ""


def test_search_open_issues_builds_the_fuzzy_list_argv_asking_only_for_titles(
    transport: _Transport,
) -> None:
    _search("Flake quarantine proposal: x")
    assert transport.calls == [
        (
            [
                "gh", "issue", "list", "--repo", "acme/widget", "--label", "triage",
                "--state", "open", "--search", "Flake quarantine proposal: x",
                "--json", "title",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_search_open_issues_returns_every_candidate_object_unfiltered(
    transport: _Transport,
) -> None:
    transport.stdout = json.dumps([{"title": "near miss"}, {"title": "exact"}])
    assert _search("exact") == ({"title": "near miss"}, {"title": "exact"})


def test_search_open_issues_returns_empty_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stdout = json.dumps([{"title": "exact"}])
    assert _search() == ()


@pytest.mark.parametrize("error", _FAULTS)
def test_search_open_issues_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _search() == ()


def test_search_open_issues_returns_empty_on_unparseable_json(transport: _Transport) -> None:
    transport.stdout = "not json"
    assert _search() == ()


def test_search_open_issues_returns_empty_on_non_list_json(transport: _Transport) -> None:
    transport.stdout = json.dumps({"title": "exact"})
    assert _search() == ()


def test_search_open_issues_drops_non_object_items(transport: _Transport) -> None:
    transport.stdout = json.dumps(["exact", 3, None, {"title": "exact"}])
    assert _search() == ({"title": "exact"},)


def test_search_open_issues_treats_missing_output_as_no_candidates(
    transport: _Transport,
) -> None:
    transport.stdout = None
    assert _search() == ()


def test_create_label_builds_the_forced_create_argv(transport: _Transport) -> None:
    assert _label() is True
    assert transport.calls == [
        (
            [
                "gh", "label", "create", "triage", "--repo", "acme/widget",
                "--color", "d4c5f9", "--description", "Needs a look", "--force",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_create_label_reports_false_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    assert _label() is False


@pytest.mark.parametrize("error", _FAULTS)
def test_create_label_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _label() is False


def test_create_issue_builds_the_labelled_create_argv(transport: _Transport) -> None:
    assert _issue() is True
    assert transport.calls == [
        (
            [
                "gh", "issue", "create", "--repo", "acme/widget",
                "--title", "Title", "--body", "Body", "--label", "triage",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_create_issue_reports_false_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    assert _issue() is False


@pytest.mark.parametrize("error", _FAULTS)
def test_create_issue_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _issue() is False


def test_open_prs_by_author_builds_the_open_list_argv_with_joined_fields_and_limit(
    transport: _Transport,
) -> None:
    _open_prs()
    assert transport.calls == [
        (
            [
                "gh", "pr", "list", "--repo", "acme/widget", "--author", "app/dependabot",
                "--state", "open", "--json", "number,mergeable,title", "--limit", "100",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_open_prs_by_author_honours_the_requested_fields_and_limit(
    transport: _Transport,
) -> None:
    _FORGE.open_prs_by_author("octocat", ("number",), forge_target=_TARGET, limit=7)
    argv = transport.calls[0][0]
    assert argv[argv.index("--author") + 1] == "octocat"
    assert argv[argv.index("--json") + 1] == "number"
    assert argv[argv.index("--limit") + 1] == "7"


def test_open_prs_by_author_returns_every_object_unfiltered(transport: _Transport) -> None:
    listing = [{"number": 1, "title": "Bump a"}, {"number": 2, "title": "Bump b"}]
    transport.stdout = json.dumps(listing)
    assert _open_prs() == tuple(listing)


def test_open_prs_by_author_returns_empty_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stdout = json.dumps([{"number": 1}])
    assert _open_prs() == ()


@pytest.mark.parametrize("error", _FAULTS)
def test_open_prs_by_author_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _open_prs() == ()


def test_open_prs_by_author_returns_empty_on_unparseable_json(transport: _Transport) -> None:
    transport.stdout = "not json"
    assert _open_prs() == ()


def test_open_prs_by_author_returns_empty_on_non_list_json(transport: _Transport) -> None:
    transport.stdout = json.dumps({"number": 1})
    assert _open_prs() == ()


def test_open_prs_by_author_drops_non_object_items(transport: _Transport) -> None:
    transport.stdout = json.dumps([1, "x", None, {"number": 2}])
    assert _open_prs() == ({"number": 2},)


def test_open_prs_by_author_treats_missing_output_as_no_proposals(
    transport: _Transport,
) -> None:
    transport.stdout = None
    assert _open_prs() == ()


def test_transport_credential_operations_resolve_the_transport_purpose_for_the_target(
    transport: _Transport,
) -> None:
    _open_prs()
    assert transport.resolved == [
        (
            ("acme/widget", secrets_store.TRANSPORT_PURPOSE),
            {"env_fallback": "ACME_GH_TOKEN"},
        ),
    ]


@pytest.mark.parametrize(
    "operation", [_open_prs, _search, _label, _issue],
    ids=["open_prs_by_author", "search_open_issues", "create_label", "create_issue"],
)
def test_transport_credential_operations_run_as_the_transport_identity(
    operation: Callable[[], object], transport: _Transport,
) -> None:
    operation()
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": _TOKEN}


@pytest.mark.parametrize(
    "operation", [_open_prs, _search, _label, _issue],
    ids=["open_prs_by_author", "search_open_issues", "create_label", "create_issue"],
)
def test_transport_credential_operations_fall_back_to_ambient_when_no_token_resolves(
    operation: Callable[[], object], transport: _Transport,
) -> None:
    transport.token = None
    operation()
    assert "env" not in transport.calls[0][1]


def test_pr_patch_stays_ambient_even_when_a_transport_token_resolves(
    transport: _Transport,
) -> None:
    assert transport.token == _TOKEN
    _FORGE.pr_patch(42, forge_target=_TARGET)
    assert transport.calls == [
        (["gh", "pr", "diff", "42", "--repo", "acme/widget"], _CALL_KWARGS),
    ]
    assert transport.resolved == []


def test_open_issues_by_title_builds_the_labelled_title_search_argv(
    transport: _Transport,
) -> None:
    _issue_search("Unblocker: a blocked merge (acme/widget)")
    assert transport.calls == [
        (
            [
                "gh", "issue", "list", "--repo", "acme/widget", "--label", "unblocker",
                "--state", "open", "--search", "in:title Unblocker: a blocked merge (acme/widget)",
                "--json", "number,title",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_open_issues_by_title_passes_a_spaced_quoted_unicode_title_as_one_argv_element(
    transport: _Transport,
) -> None:
    title = 'Unblocker: "quoted" café --state closed'
    _issue_search(title)
    argv = transport.calls[0][0]
    assert argv[argv.index("--search") + 1] == f"in:title {title}"
    assert argv.count("--state") == 1
    assert argv[-2:] == ["--json", "number,title"]


def test_open_issues_by_title_returns_every_object_row(transport: _Transport) -> None:
    listing = [{"number": 3, "title": "other"}, {"number": 9, "title": "exact"}]
    transport.stdout = json.dumps(listing)
    assert _issue_search() == RowsRead(
        outcome="ok", rows=tuple(listing), returncode=0, stderr="denied",
    )


def test_open_issues_by_title_reports_a_failed_listing_on_nonzero_exit(
    transport: _Transport,
) -> None:
    transport.returncode = 4
    transport.stdout = json.dumps([{"number": 3, "title": "exact"}])
    assert _issue_search() == RowsRead(
        outcome="failed", rows=None, returncode=4, stderr="denied",
    )


@pytest.mark.parametrize("error", _RAISED_FAULTS)
def test_open_issues_by_title_reports_raised_on_a_transport_fault(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _issue_search()
    assert result.outcome == "raised"
    assert result.rows is None
    assert str(error) in result.stderr


@pytest.mark.parametrize("body", ["not json", '{"number": 1}', "", None])
def test_open_issues_by_title_reports_an_unparseable_listing_as_ok_with_unknown_rows(
    body: str | None, transport: _Transport,
) -> None:
    transport.stdout = body
    result = _issue_search()
    assert result.outcome == "ok"
    assert result.rows is None


def test_open_issues_by_title_drops_non_object_items(transport: _Transport) -> None:
    transport.stdout = json.dumps(["exact", 3, None, {"number": 2, "title": "exact"}])
    assert _issue_search().rows == ({"number": 2, "title": "exact"},)


def test_create_numbered_issue_builds_the_labelled_create_argv_and_returns_the_output(
    transport: _Transport,
) -> None:
    transport.stdout = "https://github.com/acme/widget/issues/41\n"
    assert _numbered_issue() == ApiResult(
        ok=True, stdout="https://github.com/acme/widget/issues/41\n",
        returncode=0, stderr="denied",
    )
    assert transport.calls == [
        (
            [
                "gh", "issue", "create", "--repo", "acme/widget",
                "--title", "Title", "--body", "Body", "--label", "unblocker",
            ],
            _TRANSPORT_KWARGS,
        ),
    ]


def test_create_numbered_issue_reports_not_ok_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 2
    assert _numbered_issue() == ApiResult(ok=False, stdout="", returncode=2, stderr="denied")


def test_create_numbered_issue_treats_missing_output_as_empty(transport: _Transport) -> None:
    transport.stdout = None
    assert _numbered_issue().stdout == ""


@pytest.mark.parametrize("error", _RAISED_FAULTS)
def test_create_numbered_issue_propagates_transport_faults(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    with pytest.raises(type(error)):
        _numbered_issue()


def test_comment_on_issue_posts_the_body_through_a_stdin_input_file(
    transport: _Transport,
) -> None:
    assert _issue_comment() == ApiResult(ok=True, stdout="", returncode=0, stderr="denied")
    argv, kwargs = transport.calls[0]
    assert argv[:5] == ["gh", "api", "--method", "POST", "repos/acme/widget/issues/42/comments"]
    assert argv[5] == "--input"
    assert len(argv) == 7
    assert transport.payloads == [{"body": "hello"}]
    assert kwargs == _TRANSPORT_KWARGS


def test_comment_on_issue_removes_the_temporary_payload_file(transport: _Transport) -> None:
    _issue_comment()
    assert not Path(transport.calls[0][0][-1]).exists()


def test_comment_on_issue_reports_not_ok_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    assert _issue_comment() == ApiResult(ok=False, stdout="", returncode=1, stderr="denied")


@pytest.mark.parametrize("error", _RAISED_FAULTS)
def test_comment_on_issue_propagates_transport_faults(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    with pytest.raises(type(error)):
        _issue_comment()


_ISSUE_OPERATIONS = [_issue_search, _numbered_issue, _issue_comment]
_ISSUE_OPERATION_IDS = ["open_issues_by_title", "create_numbered_issue", "comment_on_issue"]


@pytest.mark.parametrize("operation", _ISSUE_OPERATIONS, ids=_ISSUE_OPERATION_IDS)
def test_issue_operations_resolve_the_transport_purpose_for_the_target(
    operation: Callable[[], object], transport: _Transport,
) -> None:
    operation()
    assert transport.resolved == [
        (
            ("acme/widget", secrets_store.TRANSPORT_PURPOSE),
            {"env_fallback": "ACME_GH_TOKEN"},
        ),
    ]


@pytest.mark.parametrize("operation", _ISSUE_OPERATIONS, ids=_ISSUE_OPERATION_IDS)
def test_issue_operations_send_a_padded_token_stripped_as_the_transport_identity(
    operation: Callable[[], object], transport: _Transport,
) -> None:
    transport.token = _PADDED_TOKEN
    operation()
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "tok"}


@pytest.mark.parametrize("token", [None, "", "   \t"])
@pytest.mark.parametrize("operation", _ISSUE_OPERATIONS, ids=_ISSUE_OPERATION_IDS)
def test_issue_operations_send_no_environment_override_when_the_token_is_blank(
    operation: Callable[[], object], token: str | None, transport: _Transport,
) -> None:
    transport.token = token
    operation()
    assert transport.calls[0][1]["env"] is None

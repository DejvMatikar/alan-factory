from __future__ import annotations

from pathlib import Path

import pytest

from alan import secrets_store
from alan.branch_protection_compat import Finding
from alan.default_profile import Source
from alan.engine_preflight import (
    _check_reviewer_distinctness,  # pyright: ignore[reportPrivateUsage]
)
from alan.forge_gh import GhForge
from tests.fakes.forge import installed_fake_forge
from tests.forge_identity_table import (
    FALLBACK_VALUE,
    REVIEWER_VALUE,
    ROWS,
    TARGET,
    TRANSPORT_VALUE,
    Recorder,
    Row,
    install_source,
    prepare,
    rows_named,
    rows_with,
)

_PR_READS = (
    "read_pr", "read_checks", "compare_base", "head_sha", "changed_files",
    "pr_head_branch", "pr_open_state", "pr_patch", "feedback", "open_pr_titles",
)
_PR_WRITES = (
    "merge_pr", "request_branch_update", "update_pr_body", "comment_on_pr", "reply_to_thread",
)
_REVIEW_AND_THREADS = (
    "reviews", "dismiss_review", "post_review", "unresolved_threads", "resolve_thread",
)
_CI_READS_AND_RERUNS = (
    "commit_check_runs_page", "check_run_annotations", "transport_check_run_annotations",
    "rerequest_check_suite", "rerun_failed_jobs", "status_check_rollup", "failed_job_log",
    "workflow_runs", "commit_check_runs", "latest_workflow_run", "workflows",
    "latest_branch_run",
)
_ISSUES_LABELS_AND_LISTINGS = (
    "open_prs_by_author", "search_open_issues", "create_label", "create_issue", "open_prs",
    "prs_by_head", "prs_by_author", "prs_by_head_prefix", "open_issues_by_title",
    "create_numbered_issue", "comment_on_issue", "repo_file", "default_branch",
    "repo_permission", "pr_state",
)
_PR_CREATION = ("create_pr", "create_pr_from_checkout")
_FAMILIES = (
    _PR_READS, _PR_WRITES, _REVIEW_AND_THREADS, _CI_READS_AND_RERUNS,
    _ISSUES_LABELS_AND_LISTINGS, _PR_CREATION,
)
_REVIEWER_INTENT_ROWS = rows_with("reviewer")
_STRICT_REFUSING_ROWS = tuple(
    row for row in rows_with("strict") if row.id != "viewer_login[author]"
)


def _rows(methods: tuple[str, ...]) -> tuple[Row, ...]:
    return rows_named(*methods)


def _ids(rows: tuple[Row, ...]) -> list[str]:
    return [row.id for row in rows]


def _store_transport() -> None:
    secrets_store.write_secret(TARGET.repo.slug, "transport", TRANSPORT_VALUE)


def _store_reviewer() -> None:
    secrets_store.write_secret(TARGET.repo.slug, "reviewer", REVIEWER_VALUE)


def _assert_carries(row: Row, recorder: Recorder, value: str) -> None:
    row.call(GhForge())
    assert recorder.calls == [(row.argv, row.kwargs_with(value))]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Recorder:
    return prepare(monkeypatch, tmp_path, Source.profile)


@pytest.mark.parametrize("row", _rows(_PR_READS), ids=_ids(_rows(_PR_READS)))
def test_copy_pr_reads_carry_the_transport_credential(row: Row, recorder: Recorder) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize("row", _rows(_PR_WRITES), ids=_ids(_rows(_PR_WRITES)))
def test_copy_pr_writes_carry_the_transport_credential(row: Row, recorder: Recorder) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize(
    "row", _rows(_REVIEW_AND_THREADS), ids=_ids(_rows(_REVIEW_AND_THREADS)),
)
def test_copy_review_and_thread_ops_carry_the_transport_credential(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize(
    "row", _rows(_CI_READS_AND_RERUNS), ids=_ids(_rows(_CI_READS_AND_RERUNS)),
)
def test_copy_ci_reads_and_reruns_carry_the_transport_credential(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize(
    "row", _rows(_ISSUES_LABELS_AND_LISTINGS), ids=_ids(_rows(_ISSUES_LABELS_AND_LISTINGS)),
)
def test_copy_issue_label_and_listing_ops_carry_the_transport_credential(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize("row", _rows(_PR_CREATION), ids=_ids(_rows(_PR_CREATION)))
def test_copy_create_pr_from_checkout_carries_the_transport_credential_with_unchanged_argv(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


def test_copy_families_and_strict_operations_partition_every_row() -> None:
    families = [method for family in _FAMILIES for method in family]
    strict = {row.method for row in rows_with("strict")}
    assert len(families) == len(set(families))
    assert set(families) | strict == {row.method for row in ROWS}
    assert set(families).isdisjoint(strict)


def test_copy_reviewer_intent_rows_are_exactly_the_five_reviewer_operations() -> None:
    assert sorted(row.id for row in _REVIEWER_INTENT_ROWS) == [
        "dismiss_review", "post_review[reviewer]", "resolve_thread[reviewer]",
        "reviews", "unresolved_threads[reviewer]",
    ]


@pytest.mark.parametrize("row", _REVIEWER_INTENT_ROWS, ids=_ids(_REVIEWER_INTENT_ROWS))
def test_copy_reviewer_intent_ops_carry_the_reviewer_credential_when_stored(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _store_reviewer()
    _assert_carries(row, recorder, REVIEWER_VALUE)


@pytest.mark.parametrize("row", _REVIEWER_INTENT_ROWS, ids=_ids(_REVIEWER_INTENT_ROWS))
def test_copy_reviewer_intent_ops_fall_back_to_the_transport_credential(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize("row", _REVIEWER_INTENT_ROWS, ids=_ids(_REVIEWER_INTENT_ROWS))
def test_copy_reviewer_intent_ops_use_the_fallback_variable_credential(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_transport()
    monkeypatch.setenv(TARGET.credential_env_name, FALLBACK_VALUE)
    _assert_carries(row, recorder, FALLBACK_VALUE)


@pytest.mark.parametrize("row", _REVIEWER_INTENT_ROWS, ids=_ids(_REVIEWER_INTENT_ROWS))
def test_copy_reviewer_intent_ops_ignore_a_blank_reviewer_credential(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_transport()
    monkeypatch.setenv(TARGET.credential_env_name, "   ")
    _assert_carries(row, recorder, TRANSPORT_VALUE)


@pytest.mark.parametrize(
    "row", (*_REVIEWER_INTENT_ROWS, *rows_named("read_pr", "comment_on_pr")),
    ids=_ids((*_REVIEWER_INTENT_ROWS, *rows_named("read_pr", "comment_on_pr"))),
)
def test_copy_with_only_the_fallback_variable_uses_one_token_for_both_identities(
    row: Row, recorder: Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TARGET.credential_env_name, FALLBACK_VALUE)
    _assert_carries(row, recorder, FALLBACK_VALUE)


@pytest.mark.parametrize("row", _STRICT_REFUSING_ROWS, ids=_ids(_STRICT_REFUSING_ROWS))
def test_copy_without_reviewer_keeps_strict_operations_refusing(
    row: Row, recorder: Recorder,
) -> None:
    _store_transport()
    assert row.call(GhForge()) == row.refusal
    assert recorder.calls == []


def test_copy_strict_operations_run_as_before_when_their_credential_is_stored(
    recorder: Recorder,
) -> None:
    _store_transport()
    _store_reviewer()
    for row in rows_with("strict"):
        recorder.calls.clear()
        expected = TRANSPORT_VALUE if row.id == "viewer_login[author]" else REVIEWER_VALUE
        row.call(GhForge())
        assert recorder.calls == [(row.argv, row.kwargs_with(expected))]


def _distinctness_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Source,
) -> Finding:
    prepare(monkeypatch, tmp_path, source, stdout="author-login")
    _store_transport()
    with installed_fake_forge() as fake:
        fake.stub("viewer_login", GhForge().viewer_login)
        return _check_reviewer_distinctness(TARGET)


def test_copy_without_reviewer_keeps_preflight_reviewer_distinctness_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    owner_finding = _distinctness_finding(monkeypatch, tmp_path, Source.owner)
    copy_finding = _distinctness_finding(monkeypatch, tmp_path, Source.profile)
    assert copy_finding == owner_finding
    assert copy_finding.status == "unknown"


def test_copy_gate_is_evaluated_per_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    recorder = prepare(monkeypatch, tmp_path, Source.owner)
    _store_transport()
    [row] = rows_named("read_pr")
    forge = GhForge()
    row.call(forge)
    install_source(monkeypatch, Source.profile)
    row.call(forge)
    install_source(monkeypatch, Source.owner)
    row.call(forge)
    assert recorder.calls == [
        (row.argv, row.kwargs),
        (row.argv, row.kwargs_with(TRANSPORT_VALUE)),
        (row.argv, row.kwargs),
    ]

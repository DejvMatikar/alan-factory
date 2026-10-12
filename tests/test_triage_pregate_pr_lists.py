from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, CiConfig, EngineConfig, RepoIdentity, register
from alan.forge import JsonObject, RowsOutcome, RowsRead
from alan.queue import Queue
from alan.teams import triage_pregate
from alan.teams.triage_pregate import (
    _OPEN_ROLLUP_LIMIT,
    _PR_REF_FIELDS,
    _PR_ROLLUP_FIELDS,
    _all_linked_prs_merged,
    _fetch_open_pr_rollups,
    _fetch_pr_by_number,
    _fetch_raw_prs,
    _merge_open_rollups,
    gather_build_tasks,
    verify_pr_merged,
)
from alan.workspace import create as create_workspace
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, pr_read, rows_read

_DEFAULT_REPO = RepoIdentity(owner="DejvMatikar", repo="alan")
_FETCHES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "raw": _fetch_raw_prs,
    "rollups": _fetch_open_pr_rollups,
}


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


def _raise_on_call(*a: Any, **kw: Any) -> Any:
    raise OSError("gh must not be spawned")


def _by_state(ref_prs: list[JsonObject], rollups: list[JsonObject]) -> Callable[..., RowsRead]:
    def _list(*_a: object, state: str, **_k: object) -> RowsRead:
        return rows_read(rollups if state == "open" else ref_prs)

    return _list


def _calls(forge: FakeForge, name: str) -> list[tuple[tuple[object, ...], dict[str, Any]]]:
    return [(args, kwargs) for called, args, kwargs in forge.calls if called == name]


def test_fetch_raw_prs_lists_the_teams_prefix_over_all_states_from_the_default_repo(
    forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", rows_read([{"number": 1}]))
    assert _fetch_raw_prs() == [{"number": 1}]
    ((args, kwargs),) = _calls(forge, "prs_by_head_prefix")
    assert args == ("teams/", ("number", "state", "headRefName"))
    fields = cast("tuple[str, ...]", args[1])
    assert fields == tuple(_PR_REF_FIELDS.split(","))
    assert ",".join(fields) == _PR_REF_FIELDS
    assert "statusCheckRollup" not in fields
    assert kwargs["state"] == "all"
    assert kwargs["limit"] == 100
    assert kwargs["timeout"] == 30
    assert kwargs["forge_target"].repo == _DEFAULT_REPO
    assert kwargs["forge_target"].credential_env_name == (
        DEFAULT_FORGE_TARGET.credential_env_name
    )


def test_fetch_open_pr_rollups_lists_the_teams_prefix_open_only_with_limit_30(
    forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", rows_read([{"number": 2}]))
    assert _fetch_open_pr_rollups() == [{"number": 2}]
    ((args, kwargs),) = _calls(forge, "prs_by_head_prefix")
    assert args == (
        "teams/", ("number", "reviewDecision", "mergeStateStatus", "statusCheckRollup"),
    )
    fields = cast("tuple[str, ...]", args[1])
    assert fields == tuple(_PR_ROLLUP_FIELDS.split(","))
    assert ",".join(fields) == _PR_ROLLUP_FIELDS
    assert kwargs["state"] == "open"
    assert kwargs["limit"] == _OPEN_ROLLUP_LIMIT == 30
    assert kwargs["timeout"] == 30
    assert kwargs["forge_target"].repo == _DEFAULT_REPO


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
@pytest.mark.parametrize("outcome", ["failed", "raised"])
def test_pr_list_fetch_returns_empty_when_the_listing_did_not_complete(
    fetch: Callable[[], list[dict[str, Any]]], outcome: RowsOutcome, forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", rows_read(None, outcome=outcome, returncode=1))
    assert fetch() == []


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
def test_pr_list_fetch_returns_empty_for_an_unparseable_successful_listing(
    fetch: Callable[[], list[dict[str, Any]]], forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", rows_read(None))
    assert fetch() == []


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
def test_pr_list_fetch_returns_empty_for_an_empty_listing(
    fetch: Callable[[], list[dict[str, Any]]], forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", rows_read([]))
    assert fetch() == []


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
@pytest.mark.parametrize("error", [OSError("no gh"), RuntimeError("boom")])
def test_pr_list_fetch_returns_empty_when_the_forge_call_raises(
    fetch: Callable[[], list[dict[str, Any]]], error: BaseException, forge: FakeForge,
) -> None:
    always(forge, "prs_by_head_prefix", error)
    assert fetch() == []


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
def test_pr_list_fetch_returns_independent_copies_of_the_rows(
    fetch: Callable[[], list[dict[str, Any]]], forge: FakeForge,
) -> None:
    row: JsonObject = {"number": 1}
    always(forge, "prs_by_head_prefix", rows_read([row]))
    (fetched,) = fetch()
    fetched["number"] = 2
    assert row == {"number": 1}


@pytest.mark.parametrize("fetch", _FETCHES.values(), ids=list(_FETCHES))
def test_pr_list_fetch_keeps_targeting_the_default_repo_when_another_engine_is_registered(
    fetch: Callable[[], list[dict[str, Any]]], forge: FakeForge,
) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket-two"),
            ci=CiConfig(main_ci_workflow="tests.yml"),
        ),
        validate_env=False,
    )
    always(forge, "prs_by_head_prefix", rows_read([]))
    fetch()
    ((_args, kwargs),) = _calls(forge, "prs_by_head_prefix")
    assert kwargs["forge_target"].repo == _DEFAULT_REPO


def test_fetch_pr_by_number_reads_number_and_state_from_the_default_repo(
    forge: FakeForge,
) -> None:
    always(forge, "read_pr", pr_read({"number": 42, "state": "MERGED"}))
    assert _fetch_pr_by_number(42) == {"number": 42, "state": "MERGED"}
    ((args, kwargs),) = _calls(forge, "read_pr")
    assert args == (42, ("number", "state"))
    assert kwargs["timeout"] == 30
    assert kwargs["forge_target"].repo == _DEFAULT_REPO


def test_fetch_pr_by_number_keeps_targeting_the_default_repo_when_another_engine_is_registered(
    forge: FakeForge,
) -> None:
    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket-two"),
            ci=CiConfig(main_ci_workflow="tests.yml"),
        ),
        validate_env=False,
    )
    always(forge, "read_pr", pr_read({"number": 1}))
    _fetch_pr_by_number(1)
    ((_args, kwargs),) = _calls(forge, "read_pr")
    assert kwargs["forge_target"].repo == _DEFAULT_REPO


def test_fetch_pr_by_number_reads_a_successful_empty_payload_as_an_empty_dict(
    forge: FakeForge,
) -> None:
    always(forge, "read_pr", pr_read({}))
    assert _fetch_pr_by_number(42) == {}


def test_fetch_pr_by_number_is_none_when_the_view_failed(forge: FakeForge) -> None:
    always(forge, "read_pr", pr_read({"state": "MERGED"}, returncode=1, stderr="err"))
    assert _fetch_pr_by_number(99) is None


@pytest.mark.parametrize("error", [OSError("no gh"), RuntimeError("boom")])
def test_fetch_pr_by_number_is_none_when_the_forge_call_raises(
    error: BaseException, forge: FakeForge,
) -> None:
    always(forge, "read_pr", error)
    assert _fetch_pr_by_number(99) is None


def test_fetch_pr_by_number_returns_an_independent_copy_of_the_fields(forge: FakeForge) -> None:
    fields: JsonObject = {"number": 42, "state": "OPEN"}
    always(forge, "read_pr", pr_read(fields))
    fetched = _fetch_pr_by_number(42)
    assert fetched is not None
    fetched["state"] = "MERGED"
    assert fields["state"] == "OPEN"


@pytest.mark.parametrize(
    ("state", "merged"), [("MERGED", True), ("OPEN", False), ("CLOSED", False)],
)
def test_verify_pr_merged_reads_the_state(state: str, merged: bool, forge: FakeForge) -> None:
    always(forge, "read_pr", pr_read({"number": 42, "state": state}))
    assert verify_pr_merged(42) is merged


def test_verify_pr_merged_is_false_when_the_view_failed(forge: FakeForge) -> None:
    always(forge, "read_pr", pr_read(returncode=1, stderr="err"))
    assert verify_pr_merged(42) is False


def test_verify_pr_merged_is_false_when_the_forge_call_raises(forge: FakeForge) -> None:
    always(forge, "read_pr", OSError("no gh"))
    assert verify_pr_merged(42) is False


def test_verify_pr_merged_is_false_for_a_payload_without_a_state(forge: FakeForge) -> None:
    always(forge, "read_pr", pr_read({}))
    assert verify_pr_merged(42) is False


@pytest.mark.parametrize(
    ("state", "merged"), [("MERGED", True), ("OPEN", False)],
)
def test_all_linked_prs_merged_fetches_a_pr_missing_from_the_index(
    state: str, merged: bool, forge: FakeForge,
) -> None:
    always(forge, "read_pr", pr_read({"number": 99, "state": state}))
    assert _all_linked_prs_merged({99}, {}) is merged


def test_all_linked_prs_merged_is_false_when_the_fetch_for_a_missing_pr_raises(
    forge: FakeForge,
) -> None:
    always(forge, "read_pr", OSError("no gh"))
    assert _all_linked_prs_merged({99}, {}) is False


def test_merge_open_rollups_skips_the_fetch_when_no_matched_pr_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triage_pregate, "_fetch_open_pr_rollups", _raise_on_call)
    pr_map = {
        "t1": {"number": 1, "state": "MERGED", "headRefName": "teams/t1"},
        "t2": {"number": 2, "state": "CLOSED", "headRefName": "teams/t2"},
    }
    assert _merge_open_rollups(pr_map) is pr_map


def test_merge_open_rollups_skips_the_fetch_for_an_empty_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triage_pregate, "_fetch_open_pr_rollups", _raise_on_call)
    assert _merge_open_rollups({}) == {}


def test_merge_open_rollups_enriches_only_open_entries_by_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollups = [
        {"number": 5, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
         "statusCheckRollup": []},
        {"number": 6, "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
         "statusCheckRollup": []},
        {"reviewDecision": "orphan-without-number"},
    ]
    monkeypatch.setattr(triage_pregate, "_fetch_open_pr_rollups", lambda: rollups)
    pr_map = {
        "open-task": {"number": 5, "state": "OPEN", "headRefName": "teams/open-task"},
        "merged-task": {"number": 6, "state": "MERGED", "headRefName": "teams/merged-task"},
    }
    merged = _merge_open_rollups(pr_map)
    assert merged["open-task"] == {
        "number": 5, "state": "OPEN", "headRefName": "teams/open-task",
        "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
    }
    assert merged["merged-task"] == pr_map["merged-task"]
    assert "reviewDecision" not in merged["merged-task"]


def test_merge_open_rollups_leaves_open_pr_missing_from_rollup_list_unenriched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        triage_pregate, "_fetch_open_pr_rollups", lambda: [{"number": 99, "reviewDecision": "X"}]
    )
    pr_map = {"t": {"number": 5, "state": "OPEN", "headRefName": "teams/t"}}
    assert _merge_open_rollups(pr_map) == pr_map


def _rollup(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "number": number, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [], **extra,
    }


def _states_listed(forge: FakeForge) -> list[str]:
    return [kwargs["state"] for _args, kwargs in _calls(forge, "prs_by_head_prefix")]


def test_gather_build_tasks_flags_pr_approved_unmerged_from_the_two_lists(
    queue: Queue, ws_root: Path, forge: FakeForge
) -> None:
    ws = create_workspace("impl-two-lists", "d", root=ws_root, team="implementation")
    ref_prs = [{"number": 5, "state": "OPEN", "headRefName": f"teams/{ws.task_id}"}]
    forge.stub("prs_by_head_prefix", _by_state(ref_prs, [_rollup(5)]))
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is True
    assert "pr_approved_unmerged" in result[0].blocker_reason
    assert result[0].obs["pr_review"] == "APPROVED"
    assert _states_listed(forge) == ["all", "open"]


def test_gather_build_tasks_flags_pr_checks_failing_from_the_two_lists(
    queue: Queue, ws_root: Path, forge: FakeForge
) -> None:
    ws = create_workspace("impl-red-checks", "d", root=ws_root, team="implementation")
    ref_prs = [{"number": 8, "state": "OPEN", "headRefName": f"teams/{ws.task_id}"}]
    rollups = [
        _rollup(8, reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED",
                statusCheckRollup=[{"conclusion": "FAILURE"}]),
    ]
    forge.stub("prs_by_head_prefix", _by_state(ref_prs, rollups))
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is True
    assert "pr_checks_failing" in result[0].blocker_reason
    assert result[0].obs["pr_failing_checks"] is True


def test_gather_build_tasks_lists_prs_once_when_no_matched_pr_is_open(
    queue: Queue, ws_root: Path, forge: FakeForge
) -> None:
    ws = create_workspace("impl-merged-only", "d", root=ws_root, team="implementation")
    ref_prs = [{"number": 3, "state": "MERGED", "headRefName": f"teams/{ws.task_id}"}]
    forge.stub("prs_by_head_prefix", _by_state(ref_prs, [_rollup(3)]))
    result = gather_build_tasks(ws_root, queue)
    assert len(result) == 1
    assert result[0].is_blocked is False
    assert result[0].obs["pr_state"] == "MERGED"
    assert _states_listed(forge) == ["all"]

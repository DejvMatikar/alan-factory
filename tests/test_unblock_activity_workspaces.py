from __future__ import annotations

from pathlib import Path

import pytest

import alan.engine_config as engine_config_mod
import alan.unblock_activity as activity
import alan.unblock_ledger as ledger
from alan.queue import Queue
from alan.rca_store import save_rca_draft
from alan.workspace import Status
from tests.test_unblock_activity import (
    IN_WINDOW,
    OUT_OF_WINDOW,
    _draft,
    _fleet,
    _record,
    _repo,
    _workspace,
)


@pytest.fixture
def ready_queue(queue: Queue) -> Queue:
    ledger.ensure_schema(queue)
    return queue


@pytest.fixture(autouse=True)
def _registered_repos() -> None:
    for owner, repo in (("o", "r"), ("o", "other")):
        engine_config_mod.register(
            engine_config_mod.EngineConfig(
                repo=engine_config_mod.RepoIdentity(owner=owner, repo=repo)
            ),
            validate_env=False,
        )


def _report(queue: Queue, **overrides: object) -> int:
    fields: dict[str, object] = {
        "kind": ledger.KIND_REPORT,
        "shape": "needs_human",
        "outcome": ledger.OUTCOME_NEEDS_HUMAN,
        "action": None,
    }
    fields.update(overrides)
    return _record(queue, **fields)


def _rows(
    queue: Queue, ws_root: Path, *, slug: str = "o/r", limit: int = activity.EVENT_LIMIT
) -> dict[str, activity.WorkspaceActivity]:
    result = _repo(queue, ws_root, slug=slug, limit=limit)
    return {w.workspace_key: w for w in result.workspaces}


def test_still_stuck_count_covers_only_workspaces_that_are_currently_stuck(
    ready_queue: Queue, ws_root: Path
) -> None:
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    cleared = _workspace(ws_root, "Cleared", status=Status.COMPLETED)
    _record(ready_queue, workspace_key=stuck.task_id, fingerprint="a")
    _record(ready_queue, workspace_key=cleared.task_id, fingerprint="b")
    _record(ready_queue, workspace_key="vanished", fingerprint="c")

    summary = _repo(ready_queue, ws_root).summary

    assert summary.workspaces_touched == 3
    assert summary.still_stuck_workspaces == 1


def test_still_stuck_count_is_deduplicated_across_events_and_park_kinds(
    ready_queue: Queue, ws_root: Path
) -> None:
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=stuck.task_id, shape="stale_review", ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key=stuck.task_id, shape="crash_park", ts=IN_WINDOW - 10.0)

    summary = _repo(ready_queue, ws_root).summary

    assert sum(a.still_stuck_workspaces for a in summary.aggregates) == 2
    assert summary.still_stuck_workspaces == 1


def test_gave_up_count_is_deduplicated_and_needs_a_report_row_in_the_window(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-a", fingerprint="a")
    _report(ready_queue, workspace_key="ws-b", fingerprint="b", ts=IN_WINDOW - 20.0)
    _report(ready_queue, workspace_key="ws-b", fingerprint="b", ts=IN_WINDOW - 10.0)
    _report(ready_queue, workspace_key="ws-c", fingerprint="c", ts=OUT_OF_WINDOW)
    _record(ready_queue, workspace_key="ws-c", fingerprint="c")

    summary = _repo(ready_queue, ws_root).summary

    assert summary.workspaces_touched == 3
    assert summary.gave_up_workspaces == 1


def test_wait_rows_never_count_as_stuck_or_gave_up(ready_queue: Queue, ws_root: Path) -> None:
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=stuck.task_id, kind=ledger.KIND_WAIT, action=None)

    summary = _repo(ready_queue, ws_root).summary

    assert summary.still_stuck_workspaces == 0
    assert summary.gave_up_workspaces == 0


def test_summary_counts_are_zero_for_an_empty_ledger(ready_queue: Queue, ws_root: Path) -> None:
    repo_summary = _repo(ready_queue, ws_root).summary
    fleet_summary = _fleet(ready_queue, ws_root).summary

    assert (repo_summary.still_stuck_workspaces, repo_summary.gave_up_workspaces) == (0, 0)
    assert (fleet_summary.still_stuck_workspaces, fleet_summary.gave_up_workspaces) == (0, 0)


def test_fleet_counts_are_deduplicated_and_skip_hidden_repositories(
    ready_queue: Queue, ws_root: Path
) -> None:
    stuck = _workspace(ws_root, "Stuck", engine_key="o/r", status=Status.FAILED)
    hidden = _workspace(ws_root, "Hidden", engine_key="o/other", status=Status.FAILED)
    _record(ready_queue, workspace_key=stuck.task_id, shape="a", ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key=stuck.task_id, shape="b", ts=IN_WINDOW - 10.0)
    _report(ready_queue, workspace_key=stuck.task_id, ts=IN_WINDOW)
    _record(ready_queue, workspace_key=hidden.task_id, engine_key="o/other")
    _report(ready_queue, workspace_key=hidden.task_id, engine_key="o/other")

    summary = _fleet(ready_queue, ws_root, visible=("o/r",)).summary

    assert summary.workspaces_touched == 1
    assert summary.still_stuck_workspaces == 1
    assert summary.gave_up_workspaces == 1


def test_fleet_activity_and_its_summary_never_carry_a_workspaces_list(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)

    fleet = _fleet(ready_queue, ws_root)

    assert not hasattr(fleet, "workspaces")
    assert not hasattr(fleet.summary, "workspaces")


def test_repo_view_of_an_empty_window_has_no_workspace_rows(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)
    _record(ready_queue, workspace_key="ws-w", kind=ledger.KIND_WAIT, action=None)

    assert _repo(ready_queue, ws_root).workspaces == ()


def test_one_row_per_touched_workspace_even_when_the_event_cap_cuts_events(
    ready_queue: Queue, ws_root: Path
) -> None:
    for i in range(5):
        _record(ready_queue, workspace_key=f"ws-{i}", fingerprint=f"fp-{i}", ts=IN_WINDOW - i)

    result = _repo(ready_queue, ws_root, limit=2)

    assert len(result.events) == 2
    assert len(result.workspaces) == result.summary.workspaces_touched == 5


def test_workspace_rows_are_not_capped_at_the_event_limit(
    ready_queue: Queue, ws_root: Path
) -> None:
    total = activity.EVENT_LIMIT + 7
    for i in range(total):
        _record(ready_queue, workspace_key=f"ws-{i}", fingerprint=f"fp-{i}", ts=IN_WINDOW - i)

    result = _repo(ready_queue, ws_root)

    assert len(result.events) == activity.EVENT_LIMIT
    assert len(result.workspaces) == total
    assert result.summary.workspaces_touched == total


def test_workspace_rows_only_cover_the_named_repository(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-r", engine_key="o/r")
    _record(ready_queue, workspace_key="ws-o", engine_key="o/other", fingerprint="fp-2")

    assert set(_rows(ready_queue, ws_root, slug="o/other")) == {"ws-o"}


def test_workspace_rows_are_ordered_by_latest_activity_newest_first(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-old", fingerprint="a", ts=IN_WINDOW - 30.0)
    _record(ready_queue, workspace_key="ws-new", fingerprint="b", ts=IN_WINDOW - 10.0)
    _record(ready_queue, workspace_key="ws-mid", fingerprint="c", ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key="ws-old", fingerprint="a", ts=IN_WINDOW - 25.0)

    keys = [w.workspace_key for w in _repo(ready_queue, ws_root).workspaces]

    assert keys == ["ws-new", "ws-mid", "ws-old"]


def test_workspaces_with_equal_timestamps_order_by_the_newer_ledger_row_first(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-first", fingerprint="a")
    _record(ready_queue, workspace_key="ws-second", fingerprint="b")

    keys = [w.workspace_key for w in _repo(ready_queue, ws_root).workspaces]

    assert keys == ["ws-second", "ws-first"]


def test_workspace_row_carries_the_latest_activity_time_and_park_kind(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, shape="crash_park", ts=IN_WINDOW - 30.0)
    _record(ready_queue, shape="stale_review", ts=IN_WINDOW - 10.0)

    row = _rows(ready_queue, ws_root)["ws-1"]

    assert row.last_at == IN_WINDOW - 10.0
    assert row.shape == "stale_review"


def test_workspace_row_counts_its_events_and_reports_how_many_were_listed(
    ready_queue: Queue, ws_root: Path
) -> None:
    for offset in (40.0, 30.0, 20.0):
        _record(ready_queue, workspace_key="ws-many", ts=IN_WINDOW - offset)
    _record(ready_queue, workspace_key="ws-one", fingerprint="b", ts=IN_WINDOW - 10.0)

    complete = _rows(ready_queue, ws_root)
    cut = _rows(ready_queue, ws_root, limit=2)

    assert (complete["ws-many"].events_total, complete["ws-many"].events_listed) == (3, 3)
    assert (cut["ws-one"].events_total, cut["ws-one"].events_listed) == (1, 1)
    assert (cut["ws-many"].events_total, cut["ws-many"].events_listed) == (3, 1)


def test_a_workspace_whose_events_were_all_cut_is_still_a_row_listing_none(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-cut", fingerprint="a", ts=IN_WINDOW - 30.0)
    _record(ready_queue, workspace_key="ws-shown", fingerprint="b", ts=IN_WINDOW - 10.0)

    rows = _rows(ready_queue, ws_root, limit=1)

    assert (rows["ws-cut"].events_total, rows["ws-cut"].events_listed) == (1, 0)
    assert rows["ws-shown"].events_listed == 1


def test_attempts_total_is_the_lifetime_count_and_is_never_clamped_to_the_ceiling(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)
    for offset in (50.0, 40.0, 30.0, 20.0, 10.0):
        _record(ready_queue, ts=IN_WINDOW - offset)

    row = _rows(ready_queue, ws_root)["ws-1"]

    assert row.events_total == 5
    assert row.attempts_total == 6
    assert row.attempts_total > row.attempts_ceiling


def test_attempts_ceiling_is_the_per_workspace_ledger_ceiling(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)

    assert _rows(ready_queue, ws_root)["ws-1"].attempts_ceiling == (
        ledger.ATTEMPT_CEILING_PER_WORKSPACE
    )


def test_a_report_only_workspace_still_shows_its_lifetime_attempts(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW - 10.0)
    _record(ready_queue, ts=OUT_OF_WINDOW)
    _report(ready_queue)

    row = _rows(ready_queue, ws_root)["ws-1"]

    assert row.events_total == 1
    assert row.attempts_total == 2


def test_a_workspace_with_no_attempts_at_all_reports_zero_attempts(
    ready_queue: Queue, ws_root: Path
) -> None:
    _report(ready_queue)

    assert _rows(ready_queue, ws_root)["ws-1"].attempts_total == 0


def test_attempts_total_is_scoped_to_each_workspace(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-a", ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key="ws-a", ts=IN_WINDOW - 10.0)
    _record(ready_queue, workspace_key="ws-b", fingerprint="b")

    rows = _rows(ready_queue, ws_root)

    assert (rows["ws-a"].attempts_total, rows["ws-b"].attempts_total) == (2, 1)


def test_workspace_row_clearance_is_three_valued(ready_queue: Queue, ws_root: Path) -> None:
    cleared = _workspace(ws_root, "Cleared", status=Status.COMPLETED)
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=cleared.task_id, fingerprint="a")
    _record(ready_queue, workspace_key=stuck.task_id, fingerprint="b")
    _record(ready_queue, workspace_key="vanished", fingerprint="c")

    rows = _rows(ready_queue, ws_root)

    assert rows[cleared.task_id].clearance is activity.Clearance.CLEARED
    assert rows[stuck.task_id].clearance is activity.Clearance.STILL_STUCK
    assert rows["vanished"].clearance is activity.Clearance.UNKNOWN


def test_gave_up_is_true_only_for_workspaces_with_a_report_row_in_the_window(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-acted", fingerprint="a")
    _report(ready_queue, workspace_key="ws-reported", fingerprint="b")
    _report(ready_queue, workspace_key="ws-stale-report", fingerprint="c", ts=OUT_OF_WINDOW)
    _record(ready_queue, workspace_key="ws-stale-report", fingerprint="c")

    rows = _rows(ready_queue, ws_root)

    assert rows["ws-acted"].gave_up is False
    assert rows["ws-reported"].gave_up is True
    assert rows["ws-stale-report"].gave_up is False


def test_gave_up_holds_even_when_the_report_is_not_the_latest_event(
    ready_queue: Queue, ws_root: Path
) -> None:
    _report(ready_queue, ts=IN_WINDOW - 20.0)
    _record(ready_queue, ts=IN_WINDOW - 10.0)

    row = _rows(ready_queue, ws_root)["ws-1"]

    assert row.gave_up is True
    assert row.shape == "stale_review"


def test_workspace_rows_agree_with_the_summary_counts(ready_queue: Queue, ws_root: Path) -> None:
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=stuck.task_id, fingerprint="a")
    _report(ready_queue, workspace_key=stuck.task_id, fingerprint="a")
    _record(ready_queue, workspace_key="ws-b", fingerprint="b")
    _report(ready_queue, workspace_key="ws-c", fingerprint="c")

    result = _repo(ready_queue, ws_root)

    still_stuck = [w for w in result.workspaces if w.clearance is activity.Clearance.STILL_STUCK]
    gave_up = [w for w in result.workspaces if w.gave_up]
    assert len(still_stuck) == result.summary.still_stuck_workspaces == 1
    assert len(gave_up) == result.summary.gave_up_workspaces == 2


def test_workspace_row_carries_the_filed_issue_even_when_its_events_were_cut(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-cut", fingerprint="a", ts=IN_WINDOW - 30.0)
    _record(ready_queue, workspace_key="ws-shown", fingerprint="b", ts=IN_WINDOW - 10.0)
    _record(
        ready_queue,
        workspace_key="ws-cut",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/4"},
    )

    result = _repo(ready_queue, ws_root, limit=1)
    rows = {w.workspace_key: w for w in result.workspaces}

    assert rows["ws-cut"].issue_url == "https://github.com/o/r/issues/4"
    assert rows["ws-shown"].issue_url is None
    assert [e.issue_url for e in result.events] == [None]


def test_issue_lookup_is_chunked_and_every_workspace_row_still_gets_its_issue_url(
    ready_queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    total = activity.ISSUE_URL_LOOKUP_CHUNK + 1
    for i in range(total):
        _record(ready_queue, workspace_key=f"ws-{i}", fingerprint=f"fp-{i}", ts=IN_WINDOW - i)
        _record(
            ready_queue,
            workspace_key=f"ws-{i}",
            kind=ledger.KIND_ISSUE_DELIVERED,
            detail={"issue_url": f"https://github.com/o/r/issues/{i}"},
        )
    batch_sizes: list[int] = []
    real_lookup = ledger.issue_urls_for

    def spy(queue: Queue, workspace_keys: list[str]) -> dict[str, str | None]:
        batch_sizes.append(len(workspace_keys))
        return real_lookup(queue, workspace_keys)

    monkeypatch.setattr(ledger, "issue_urls_for", spy)

    rows = _rows(ready_queue, ws_root)

    assert batch_sizes == [activity.ISSUE_URL_LOOKUP_CHUNK, 1]
    assert len(rows) == total
    assert all(
        rows[f"ws-{i}"].issue_url == f"https://github.com/o/r/issues/{i}" for i in range(total)
    )


def test_issue_lookup_makes_no_query_when_the_window_has_no_events(
    ready_queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def spy(queue: Queue, workspace_keys: list[str]) -> dict[str, str | None]:
        calls.append(workspace_keys)
        return {}

    monkeypatch.setattr(ledger, "issue_urls_for", spy)

    assert _repo(ready_queue, ws_root).workspaces == ()
    assert calls == []


def test_workspace_row_carries_the_latest_delivered_issue_url(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)
    for number in (1, 2):
        _record(
            ready_queue,
            kind=ledger.KIND_ISSUE_DELIVERED,
            detail={"issue_url": f"https://github.com/o/r/issues/{number}"},
        )

    assert _rows(ready_queue, ws_root)["ws-1"].issue_url == "https://github.com/o/r/issues/2"


def test_workspace_row_carries_the_latest_diagnosis_where_one_exists(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1", fingerprint="a")
    _record(ready_queue, workspace_key="ws-2", fingerprint="b")
    save_rca_draft(
        ready_queue,
        "fp-old",
        _draft(workspace_key="ws-1", recurrence_signature="old", created_at=1.0),
    )
    save_rca_draft(
        ready_queue,
        "fp-new",
        _draft(workspace_key="ws-1", recurrence_signature="new", created_at=2.0),
    )

    rows = _rows(ready_queue, ws_root)

    assert rows["ws-1"].diagnosis == activity.DiagnosisSummary(
        signature="new", proposed_disposition="root_caused", verified=True
    )
    assert rows["ws-2"].diagnosis is None


def test_collecting_workspace_rows_does_not_write_to_the_ledger(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)
    before = [tuple(r) for r in ready_queue.run("SELECT * FROM unblock_ledger")]

    _repo(ready_queue, ws_root)

    assert [tuple(r) for r in ready_queue.run("SELECT * FROM unblock_ledger")] == before

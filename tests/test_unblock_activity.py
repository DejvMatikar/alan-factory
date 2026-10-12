from __future__ import annotations

from pathlib import Path

import pytest

import alan.engine_config as engine_config_mod
import alan.unblock_activity as activity
import alan.unblock_ledger as ledger
from alan.queue import Queue
from alan.rca_store import RcaDraft, save_rca_draft
from alan.workspace import Status, Workspace, list_workspaces
from alan.workspace import create as create_ws
from alan.workspace import transition as update_ws_status

NOW = 10_000_000.0
WINDOW_START = NOW - ledger.RESCUE_WINDOW_DAYS * ledger.DAY_S
IN_WINDOW = NOW - 3600.0
OUT_OF_WINDOW = WINDOW_START - 3600.0


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


def _record(queue: Queue, **overrides: object) -> int:
    fields: dict[str, object] = {
        "engine_key": "o/r",
        "workspace_key": "ws-1",
        "scope_ref": "pr:1",
        "shape": "stale_review",
        "fingerprint": "fp-1",
        "kind": ledger.KIND_ATTEMPT,
        "action": "dispatch:pr_merge",
        "outcome": ledger.OUTCOME_ACTED,
        "ts": IN_WINDOW,
    }
    fields.update(overrides)
    return ledger.record(queue, ledger.Entry(**fields))  # type: ignore[arg-type]


def _workspace(
    ws_root: Path, title: str, *, engine_key: str | None = "o/r", status: Status | None = None
) -> Workspace:
    ws = create_ws(title, "d", root=ws_root, engine_key=engine_key)
    if status is not None:
        update_ws_status(ws, status)
    return ws


def _draft(**overrides: object) -> RcaDraft:
    fields: dict[str, object] = {
        "source": "investigator",
        "proposed_disposition": "root_caused",
        "timeline": (),
        "falsification_test": "rerun",
        "recurrence_signature": "sig",
        "recipe": "",
        "recurrence_signal": "signal",
        "verified": True,
    }
    fields.update(overrides)
    return RcaDraft(**fields)  # type: ignore[arg-type]


def _repo(
    queue: Queue, ws_root: Path, *, slug: str = "o/r", limit: int = activity.EVENT_LIMIT
) -> activity.RepoActivity:
    return activity.collect_repo(
        queue, list_workspaces(root=ws_root), now=NOW, slug=slug, limit=limit
    )


def _fleet(
    queue: Queue, ws_root: Path, *, visible: tuple[str, ...] = ("o/r", "o/other")
) -> activity.FleetActivity:
    return activity.collect_fleet(
        queue, list_workspaces(root=ws_root), now=NOW, visible_slugs=visible
    )


def test_ever_recorded_is_false_for_an_empty_ledger(queue: Queue) -> None:
    assert activity.ever_recorded(queue) is False


def test_ever_recorded_is_true_when_only_out_of_window_rows_exist(ready_queue: Queue) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)
    assert activity.ever_recorded(ready_queue) is True


@pytest.mark.parametrize(
    "kind", [ledger.KIND_ATTEMPT, ledger.KIND_WAIT, ledger.KIND_REPORT]
)
def test_ever_recorded_counts_every_activity_kind(ready_queue: Queue, kind: str) -> None:
    _record(ready_queue, kind=kind)
    assert activity.ever_recorded(ready_queue) is True


def test_ever_recorded_ignores_issue_bookkeeping_rows(ready_queue: Queue) -> None:
    _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT)
    _record(ready_queue, kind=ledger.KIND_ISSUE_DELIVERED)
    assert activity.ever_recorded(ready_queue) is False


def test_repo_scope_matches_the_slug_case_insensitively(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)
    _record(ready_queue, workspace_key="ws-wait", kind=ledger.KIND_WAIT, action=None)

    scoped = _repo(ready_queue, ws_root, slug="O/R")

    assert scoped.summary.events_total == 1
    assert scoped.summary.waited_workspaces == 1
    assert len(scoped.events) == 1


def test_window_includes_a_row_exactly_at_the_rescue_window_start(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=WINDOW_START)
    assert _repo(ready_queue, ws_root).summary.events_total == 1


def test_window_excludes_a_row_just_before_the_rescue_window_start(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=WINDOW_START - 1.0)
    assert _repo(ready_queue, ws_root).summary.events_total == 0


def test_attempt_index_counts_lifetime_attempts_not_just_the_window(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)
    _record(ready_queue, ts=IN_WINDOW - 20.0)
    _record(ready_queue, ts=IN_WINDOW - 10.0)

    events = _repo(ready_queue, ws_root).events

    indexes = [
        (e.attempt.fingerprint_index, e.attempt.fingerprint_ceiling) for e in events if e.attempt
    ]
    assert indexes == [(3, 2), (2, 2)]
    assert len(events) == 2


def test_attempt_index_exposes_an_over_ceiling_repeat(ready_queue: Queue, ws_root: Path) -> None:
    for offset in (30.0, 20.0, 10.0):
        _record(ready_queue, ts=IN_WINDOW - offset)

    newest = _repo(ready_queue, ws_root).events[0]

    assert newest.attempt is not None
    assert (newest.attempt.fingerprint_index, newest.attempt.fingerprint_ceiling) == (3, 2)


def test_workspace_index_runs_across_fingerprints_while_fingerprint_index_restarts(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, fingerprint="fp-a", ts=IN_WINDOW - 30.0)
    _record(ready_queue, fingerprint="fp-b", ts=IN_WINDOW - 20.0)
    _record(ready_queue, fingerprint="fp-a", ts=IN_WINDOW - 10.0)

    events = list(reversed(_repo(ready_queue, ws_root).events))

    pairs = [
        (e.attempt.fingerprint_index, e.attempt.workspace_index, e.attempt.workspace_ceiling)
        for e in events
        if e.attempt
    ]
    assert pairs == [(1, 1, 5), (1, 2, 5), (2, 3, 5)]


def test_attempt_index_is_scoped_to_each_workspace(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-1", ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key="ws-2", ts=IN_WINDOW - 10.0)

    events = _repo(ready_queue, ws_root).events

    assert all(e.attempt is not None and e.attempt.workspace_index == 1 for e in events)


def test_report_events_carry_the_workspace_attempt_total_and_no_attempt_index(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)
    _record(ready_queue, ts=IN_WINDOW - 20.0)
    _record(
        ready_queue,
        kind=ledger.KIND_REPORT,
        shape="needs_human",
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
        ts=IN_WINDOW,
        detail={"reason": "no recipe", "authority_holder": "nobody"},
    )

    report = _repo(ready_queue, ws_root).events[0]

    assert report.kind == ledger.KIND_REPORT
    assert report.attempt is None
    assert report.attempts_total == 2
    assert report.reason == "no recipe"
    assert report.authority_holder == "nobody"


def test_attempt_events_carry_no_attempt_total(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue)
    assert _repo(ready_queue, ws_root).events[0].attempts_total is None


def test_clearance_is_cleared_for_a_workspace_that_is_no_longer_stuck(
    ready_queue: Queue, ws_root: Path
) -> None:
    ws = _workspace(ws_root, "Done", status=Status.COMPLETED)
    _record(ready_queue, workspace_key=ws.task_id)

    assert _repo(ready_queue, ws_root).events[0].clearance is activity.Clearance.CLEARED


def test_clearance_is_still_stuck_for_a_workspace_in_a_stuck_status(
    ready_queue: Queue, ws_root: Path
) -> None:
    ws = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=ws.task_id)

    assert _repo(ready_queue, ws_root).events[0].clearance is activity.Clearance.STILL_STUCK


def test_clearance_is_unknown_for_a_workspace_the_host_can_no_longer_find(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="vanished")

    assert _repo(ready_queue, ws_root).events[0].clearance is activity.Clearance.UNKNOWN


def test_wait_rows_are_not_events_and_are_counted_as_waited_workspaces(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(
        ready_queue,
        workspace_key="ws-waiting",
        kind=ledger.KIND_WAIT,
        outcome=ledger.OUTCOME_NOT_YET,
        action=None,
    )

    result = _repo(ready_queue, ws_root)

    assert result.events == ()
    assert result.summary.events_total == 0
    assert result.summary.waited_workspaces == 1
    assert result.summary.aggregates == ()


def test_waited_count_is_distinct_and_excludes_workspaces_that_also_acted(
    ready_queue: Queue, ws_root: Path
) -> None:
    for key in ("ws-a", "ws-a", "ws-b"):
        _record(ready_queue, workspace_key=key, kind=ledger.KIND_WAIT, action=None)
    _record(ready_queue, workspace_key="ws-b")

    assert _repo(ready_queue, ws_root).summary.waited_workspaces == 1


def test_waited_rows_outside_the_window_are_not_counted(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, kind=ledger.KIND_WAIT, action=None, ts=OUT_OF_WINDOW)

    assert _repo(ready_queue, ws_root).summary.waited_workspaces == 0


def test_attempt_and_report_outcomes_are_tallied_separately(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1", fingerprint="fp-1")
    _record(ready_queue, workspace_key="ws-2", fingerprint="fp-2")
    _record(ready_queue, workspace_key="ws-3", fingerprint="fp-3", outcome="skipped_moved")
    _record(
        ready_queue,
        workspace_key="ws-4",
        kind=ledger.KIND_REPORT,
        shape="stale_review",
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
    )

    (aggregate,) = _repo(ready_queue, ws_root).summary.aggregates

    assert aggregate.attempt_outcomes == (
        activity.OutcomeCount("acted", 2),
        activity.OutcomeCount("skipped_moved", 1),
    )
    assert aggregate.report_outcomes == (activity.OutcomeCount("needs_human", 1),)
    tallied = sum(o.count for o in aggregate.attempt_outcomes + aggregate.report_outcomes)
    assert tallied == aggregate.total == 4


def test_failed_attempt_outcomes_are_grouped_without_the_exception_detail(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1", fingerprint="a", outcome="failed: KeyError")
    _record(ready_queue, workspace_key="ws-2", fingerprint="b", outcome="failed: ValueError")

    (aggregate,) = _repo(ready_queue, ws_root).summary.aggregates

    assert aggregate.attempt_outcomes == (activity.OutcomeCount("failed", 2),)


def test_a_row_without_an_outcome_falls_back_to_its_kind(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, outcome=None)

    assert _repo(ready_queue, ws_root).events[0].outcome == ledger.KIND_ATTEMPT


def test_aggregates_are_grouped_by_shape_and_ordered_by_volume(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1", shape="crash_park", fingerprint="a")
    _record(ready_queue, workspace_key="ws-2", shape="stale_review", fingerprint="b")
    _record(ready_queue, workspace_key="ws-3", shape="stale_review", fingerprint="c")

    aggregates = _repo(ready_queue, ws_root).summary.aggregates

    assert [(a.shape, a.total) for a in aggregates] == [("stale_review", 2), ("crash_park", 1)]


def test_aggregate_splits_workspaces_by_clearance_and_counts_retries(
    ready_queue: Queue, ws_root: Path
) -> None:
    cleared = _workspace(ws_root, "Cleared", status=Status.COMPLETED)
    stuck = _workspace(ws_root, "Stuck", status=Status.FAILED)
    _record(ready_queue, workspace_key=cleared.task_id, ts=IN_WINDOW - 5.0)
    _record(ready_queue, workspace_key=stuck.task_id, ts=IN_WINDOW - 20.0)
    _record(ready_queue, workspace_key=stuck.task_id, ts=IN_WINDOW - 10.0)
    _record(ready_queue, workspace_key="vanished", ts=IN_WINDOW - 15.0)

    (aggregate,) = _repo(ready_queue, ws_root).summary.aggregates

    assert aggregate.total == 4
    assert aggregate.workspaces == 3
    assert aggregate.cleared_workspaces == 1
    assert aggregate.still_stuck_workspaces == 1
    assert aggregate.gone_workspaces == 1
    assert aggregate.retried_workspaces == 1


def test_events_are_newest_first_and_capped_with_the_total_reported(
    ready_queue: Queue, ws_root: Path
) -> None:
    for i in range(5):
        _record(ready_queue, workspace_key=f"ws-{i}", fingerprint=f"fp-{i}", ts=IN_WINDOW - i)

    result = _repo(ready_queue, ws_root, limit=3)

    assert [e.workspace_key for e in result.events] == ["ws-0", "ws-1", "ws-2"]
    assert result.summary.events_total == 5
    assert result.summary.workspaces_touched == 5
    (aggregate,) = result.summary.aggregates
    assert aggregate.total == 5


def test_default_event_limit_is_fifty() -> None:
    assert activity.EVENT_LIMIT == 50


def test_repo_view_only_holds_that_repositorys_events(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-r", engine_key="o/r")
    _record(ready_queue, workspace_key="ws-o", engine_key="o/other", fingerprint="fp-2")

    result = _repo(ready_queue, ws_root, slug="o/other")

    assert [e.workspace_key for e in result.events] == ["ws-o"]
    assert result.slug == "o/other"
    assert result.summary.events_total == 1


def test_repo_view_for_an_unknown_slug_is_empty_but_reports_fleet_history(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue)

    result = _repo(ready_queue, ws_root, slug="nobody/nothing")

    assert result.events == ()
    assert result.summary.events_total == 0
    assert result.fleet_ever_recorded is True


def test_repo_attribution_falls_back_to_the_live_workspace_when_the_row_has_no_engine_key(
    ready_queue: Queue, ws_root: Path
) -> None:
    ws = _workspace(ws_root, "Attributed", engine_key="o/other")
    _record(ready_queue, workspace_key=ws.task_id, engine_key=None)

    assert _repo(ready_queue, ws_root, slug="o/other").summary.events_total == 1
    assert _repo(ready_queue, ws_root, slug="o/r").summary.events_total == 0


def test_rows_with_no_engine_key_and_no_live_workspace_are_unattributed(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="vanished", engine_key=None)

    fleet = _fleet(ready_queue, ws_root)

    assert fleet.unattributed_events == 1
    assert fleet.summary.events_total == 1
    assert _repo(ready_queue, ws_root).summary.events_total == 0


def test_unattributed_waits_count_toward_the_fleet_but_not_a_repository(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="vanished", engine_key=None, kind=ledger.KIND_WAIT)

    assert _fleet(ready_queue, ws_root).summary.waited_workspaces == 1
    assert _repo(ready_queue, ws_root).summary.waited_workspaces == 0


def test_fleet_view_excludes_hidden_repositories(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-r", engine_key="o/r")
    _record(ready_queue, workspace_key="ws-h", engine_key="o/other", fingerprint="fp-2")
    _record(
        ready_queue, workspace_key="ws-hw", engine_key="o/other", kind=ledger.KIND_WAIT
    )

    fleet = _fleet(ready_queue, ws_root, visible=("o/r",))

    assert fleet.summary.events_total == 1
    assert fleet.summary.waited_workspaces == 0
    assert fleet.unattributed_events == 0


def test_fleet_view_with_only_hidden_history_reports_never_recorded(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-h", engine_key="o/other", ts=OUT_OF_WINDOW)

    fleet = _fleet(ready_queue, ws_root, visible=("o/r",))

    assert fleet.ever_recorded is False
    assert fleet.summary.events_total == 0


def test_fleet_view_attributes_old_history_through_the_live_workspace(
    ready_queue: Queue, ws_root: Path
) -> None:
    hidden = _workspace(ws_root, "hidden", engine_key="o/other")
    _record(ready_queue, workspace_key=hidden.task_id, engine_key=None, ts=OUT_OF_WINDOW)

    assert _fleet(ready_queue, ws_root, visible=("o/r",)).ever_recorded is False
    assert _fleet(ready_queue, ws_root, visible=("o/other",)).ever_recorded is True


def test_fleet_view_counts_unattributed_old_history_as_recorded(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="vanished", engine_key=None, ts=OUT_OF_WINDOW)

    assert _fleet(ready_queue, ws_root, visible=("o/r",)).ever_recorded is True


def test_fleet_view_aggregates_across_repositories(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-r", engine_key="o/r")
    _record(ready_queue, workspace_key="ws-o", engine_key="o/other", fingerprint="fp-2")

    fleet = _fleet(ready_queue, ws_root)

    assert fleet.ever_recorded is True
    assert fleet.summary.events_total == 2
    assert fleet.summary.workspaces_touched == 2
    assert [a.total for a in fleet.summary.aggregates] == [2]


def test_fleet_view_of_an_empty_ledger_is_all_zero(queue: Queue, ws_root: Path) -> None:
    fleet = _fleet(queue, ws_root)

    assert fleet.ever_recorded is False
    assert fleet.summary.events_total == 0
    assert fleet.summary.waited_workspaces == 0
    assert fleet.summary.aggregates == ()


def test_fleet_view_with_only_old_history_reports_recorded_but_empty_window(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, ts=OUT_OF_WINDOW)

    fleet = _fleet(ready_queue, ws_root)

    assert fleet.ever_recorded is True
    assert fleet.summary.events_total == 0


def test_repo_events_carry_the_latest_delivered_issue_url(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1")
    _record(ready_queue, workspace_key="ws-2", fingerprint="fp-2")
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/9"},
    )

    events = {e.workspace_key: e for e in _repo(ready_queue, ws_root).events}

    assert events["ws-1"].issue_url == "https://github.com/o/r/issues/9"
    assert events["ws-2"].issue_url is None


def test_repo_view_with_no_events_makes_no_issue_lookup_and_returns_empty(
    ready_queue: Queue, ws_root: Path
) -> None:
    assert _repo(ready_queue, ws_root).events == ()


def test_blank_or_non_string_reason_is_dropped(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-1", fingerprint="a", detail={"reason": ""})
    _record(ready_queue, workspace_key="ws-2", fingerprint="b", detail={"reason": 5})

    assert [e.reason for e in _repo(ready_queue, ws_root).events] == [None, None]


def test_diagnosis_attaches_to_events_of_the_keyed_workspace_only(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1", fingerprint="a", ts=IN_WINDOW - 5.0)
    _record(ready_queue, workspace_key="ws-2", fingerprint="b", ts=IN_WINDOW - 10.0)
    save_rca_draft(
        ready_queue,
        "fp-x",
        _draft(workspace_key="ws-1", recurrence_signature="fd-exhaustion", created_at=1.0),
    )

    events = {e.workspace_key: e for e in _repo(ready_queue, ws_root).events}

    assert events["ws-1"].diagnosis == activity.DiagnosisSummary(
        signature="fd-exhaustion", proposed_disposition="root_caused", verified=True
    )
    assert events["ws-2"].diagnosis is None


def test_drafts_without_a_workspace_key_never_join(ready_queue: Queue, ws_root: Path) -> None:
    _record(ready_queue, workspace_key="ws-1")
    save_rca_draft(ready_queue, "fp-legacy", _draft(workspace_key=None))

    result = _repo(ready_queue, ws_root)

    assert result.events[0].diagnosis is None
    assert result.summary.diagnosed_workspaces == 0
    assert result.summary.diagnosable_workspaces == 1


def test_the_latest_draft_for_a_workspace_wins_by_created_at(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1")
    newer = _draft(workspace_key="ws-1", recurrence_signature="new", created_at=200.0)
    older = _draft(workspace_key="ws-1", recurrence_signature="old", created_at=100.0)
    save_rca_draft(ready_queue, "fp-new", newer)
    save_rca_draft(ready_queue, "fp-old", older)

    diagnosis = _repo(ready_queue, ws_root).events[0].diagnosis

    assert diagnosis is not None
    assert diagnosis.signature == "new"


def test_a_draft_without_created_at_loses_to_a_stamped_one(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-1")
    stamped = _draft(workspace_key="ws-1", recurrence_signature="stamped", created_at=5.0)
    bare = _draft(workspace_key="ws-1", recurrence_signature="bare")
    save_rca_draft(ready_queue, "fp-stamped", stamped)
    save_rca_draft(ready_queue, "fp-bare", bare)

    diagnosis = _repo(ready_queue, ws_root).events[0].diagnosis

    assert diagnosis is not None
    assert diagnosis.signature == "stamped"


def test_diagnosis_coverage_counts_distinct_workspaces_not_events(
    ready_queue: Queue, ws_root: Path
) -> None:
    for offset in (30.0, 20.0, 10.0):
        _record(ready_queue, workspace_key="ws-1", ts=IN_WINDOW - offset)
    _record(ready_queue, workspace_key="ws-2", fingerprint="b")
    save_rca_draft(ready_queue, "fp-x", _draft(workspace_key="ws-1", created_at=1.0))

    summary = _repo(ready_queue, ws_root).summary

    assert summary.events_total == 4
    assert summary.diagnosable_workspaces == 2
    assert summary.diagnosed_workspaces == 1


def test_fleet_coverage_counts_only_visible_workspaces(
    ready_queue: Queue, ws_root: Path
) -> None:
    _record(ready_queue, workspace_key="ws-r", engine_key="o/r")
    _record(ready_queue, workspace_key="ws-h", engine_key="o/other", fingerprint="b")
    save_rca_draft(ready_queue, "fp-x", _draft(workspace_key="ws-r", created_at=1.0))
    save_rca_draft(ready_queue, "fp-y", _draft(workspace_key="ws-h", created_at=1.0))

    summary = _fleet(ready_queue, ws_root, visible=("o/r",)).summary

    assert (summary.diagnosed_workspaces, summary.diagnosable_workspaces) == (1, 1)

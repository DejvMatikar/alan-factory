from __future__ import annotations

import json

import pytest

from alan import unblock_ledger as ledger
from alan.queue import Queue


@pytest.fixture
def ready_queue(queue: Queue) -> Queue:
    ledger.ensure_schema(queue)
    return queue


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
        "ts": 1_000_000.0,
    }
    fields.update(overrides)
    return ledger.record(queue, ledger.Entry(**fields))  # type: ignore[arg-type]


def test_ensure_schema_is_idempotent(queue: Queue) -> None:
    ledger.ensure_schema(queue)
    ledger.ensure_schema(queue)
    assert queue.run("SELECT COUNT(*) AS n FROM unblock_ledger")[0]["n"] == 0


def test_queue_run_returns_rows_for_parameterised_sql(queue: Queue) -> None:
    queue.set_meta("k", "v")
    rows = queue.run("SELECT value FROM queue_meta WHERE key = ?", ("k",))
    assert [row["value"] for row in rows] == ["v"]


def test_record_returns_increasing_ids(ready_queue: Queue) -> None:
    first = _record(ready_queue)
    second = _record(ready_queue)
    assert second == first + 1


def test_attempts_for_fingerprint_counts_only_attempt_rows(ready_queue: Queue) -> None:
    _record(ready_queue)
    _record(ready_queue, kind=ledger.KIND_REPORT)
    _record(ready_queue, fingerprint="fp-2")
    assert ledger.attempts_for_fingerprint(ready_queue, "ws-1", "fp-1") == 1


def test_attempts_for_workspace_counts_across_fingerprints(ready_queue: Queue) -> None:
    _record(ready_queue)
    _record(ready_queue, fingerprint="fp-2")
    _record(ready_queue, workspace_key="ws-2")
    assert ledger.attempts_for_workspace(ready_queue, "ws-1") == 2


def test_bar_lowering_count_counts_only_flagged_attempts(ready_queue: Queue) -> None:
    _record(ready_queue)
    _record(ready_queue, bar_lowering=True)
    _record(ready_queue, bar_lowering=True, kind=ledger.KIND_REPORT)
    assert ledger.bar_lowering_count(ready_queue, "ws-1") == 1


def test_pending_issue_intents_lists_undelivered_only(ready_queue: Queue) -> None:
    pending = _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT, detail={"slug": "o/r"})
    delivered = _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT)
    _record(ready_queue, kind=ledger.KIND_ISSUE_DELIVERED, ref_id=delivered)
    rows = ledger.pending_issue_intents(ready_queue, 10)
    assert [row.id for row in rows] == [pending]
    assert rows[0].detail == {"slug": "o/r"}


def test_pending_issue_intents_drops_intents_past_the_attempt_cap(ready_queue: Queue) -> None:
    intent = _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT)
    for _ in range(ledger.ISSUE_INTENT_MAX_ATTEMPTS):
        _record(ready_queue, kind=ledger.KIND_ISSUE_ATTEMPT, ref_id=intent)
    assert ledger.pending_issue_intents(ready_queue, 10) == []


def test_pending_issue_intents_honours_limit(ready_queue: Queue) -> None:
    for _ in range(3):
        _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT)
    assert len(ledger.pending_issue_intents(ready_queue, 2)) == 2


def test_counters_24h_windows_by_timestamp_and_outcome(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, ts=now - 10)
    _record(ready_queue, ts=now - 10, outcome="forge_write_failed")
    _record(ready_queue, ts=now - ledger.DAY_S - 1)
    _record(ready_queue, ts=now - 10, kind=ledger.KIND_REPORT)
    assert ledger.counters_24h(ready_queue, now) == {
        "unblock_attempts_24h": 2,
        "unblock_acted_24h": 1,
        "unblock_reports_24h": 1,
    }


def test_last_record_is_none_without_rows(queue: Queue) -> None:
    assert ledger.last_record(queue, "ws-1") is None


def test_last_record_ignores_issue_rows(ready_queue: Queue) -> None:
    _record(ready_queue, kind=ledger.KIND_ISSUE_INTENT)
    assert ledger.last_record(ready_queue, "ws-1") is None


def test_last_record_summarises_an_attempt(ready_queue: Queue) -> None:
    _record(ready_queue, detail={"reason": "stale"})
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.shape == "stale_review"
    assert record.attempts == 1
    assert record.ceiling == ledger.ATTEMPT_CEILING_PER_WORKSPACE
    assert record.outcome == ledger.OUTCOME_ACTED
    assert record.summary == (
        "attempt 1 of 5 · stale_review · dispatch:pr_merge · acted · 1970-01-12T13:46Z"
    )
    assert record.issue_url is None
    assert record.at == 1_000_000.0


def test_last_record_summarises_a_report_with_its_reason(ready_queue: Queue) -> None:
    _record(
        ready_queue,
        kind=ledger.KIND_REPORT,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
        detail={"reason": "no recipe"},
    )
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.summary == "needs human · stale_review · no recipe · 1970-01-12T13:46Z"
    assert record.outcome == ledger.OUTCOME_NEEDS_HUMAN


def test_last_record_summarises_a_wait_and_falls_back_to_the_kind(ready_queue: Queue) -> None:
    _record(ready_queue, kind=ledger.KIND_WAIT, outcome=None, detail={"reason": "pending"})
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.summary == "waiting · stale_review · pending · 1970-01-12T13:46Z"
    assert record.outcome == ledger.KIND_WAIT


def test_last_record_carries_the_latest_delivered_issue_url(ready_queue: Queue) -> None:
    _record(ready_queue)
    _record(
        ready_queue,
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/7"},
    )
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.issue_url == "https://github.com/o/r/issues/7"


def test_last_record_ignores_a_non_string_issue_url(ready_queue: Queue) -> None:
    _record(ready_queue)
    _record(ready_queue, kind=ledger.KIND_ISSUE_DELIVERED, detail={"issue_url": 7})
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.issue_url is None


def test_last_record_prefers_the_newest_row(ready_queue: Queue) -> None:
    _record(ready_queue, shape="first")
    _record(ready_queue, shape="second", ts=1_000_500.0)
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.shape == "second"


def test_last_record_without_action_or_outcome_renders_blank_slots(ready_queue: Queue) -> None:
    _record(ready_queue, action=None, outcome=None)
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.summary == "attempt 1 of 5 · stale_review ·  ·  · 1970-01-12T13:46Z"


@pytest.mark.parametrize("raw", [None, "", "{not json", json.dumps([1, 2])])
def test_parse_detail_falls_back_to_empty_dict(raw: object) -> None:
    assert ledger._parse_detail(raw) == {}  # noqa: SLF001


def test_parse_detail_returns_a_mapping(ready_queue: Queue) -> None:
    assert ledger._parse_detail(json.dumps({"a": 1})) == {"a": 1}  # noqa: SLF001


def test_row_from_sql_preserves_null_columns(ready_queue: Queue) -> None:
    _record(
        ready_queue,
        engine_key=None,
        action=None,
        outcome=None,
        kind=ledger.KIND_ISSUE_INTENT,
    )
    row = ledger.pending_issue_intents(ready_queue, 1)[0]
    assert row.engine_key is None
    assert row.action is None
    assert row.outcome is None
    assert row.head_sha is None
    assert row.handover_id is None
    assert row.bar_lowering is False
    assert row.cost_usd == 0.0
    assert row.ref_id is None


def test_record_defaults_timestamp_to_now(ready_queue: Queue) -> None:
    _record(ready_queue, ts=None, kind=ledger.KIND_ISSUE_INTENT)
    row = ledger.pending_issue_intents(ready_queue, 1)[0]
    assert row.ts > 1_000_000.0


def test_last_record_carries_action_and_reason_as_pure_reads(ready_queue: Queue) -> None:
    _record(ready_queue, action="dispatch:pr_merge", detail={"reason": "stale"})
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.action == "dispatch:pr_merge"
    assert record.reason == "stale"
    assert record.authority_holder is None


def test_last_record_carries_authority_holder_on_a_report(ready_queue: Queue) -> None:
    _record(
        ready_queue,
        kind=ledger.KIND_REPORT,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
        detail={"reason": "no recipe", "authority_holder": "human_operator"},
    )
    record = ledger.last_record(ready_queue, "ws-1")
    assert record is not None
    assert record.authority_holder == "human_operator"
    assert record.action is None


def test_records_for_returns_empty_for_no_keys(queue: Queue) -> None:
    assert ledger.records_for(queue, []) == {}


def test_records_for_batches_multiple_workspaces(ready_queue: Queue) -> None:
    _record(ready_queue, workspace_key="ws-1", detail={"reason": "stale"})
    _record(ready_queue, workspace_key="ws-2", fingerprint="fp-2", shape="crash_park")
    records = ledger.records_for(ready_queue, ["ws-1", "ws-2", "ws-3"])
    assert set(records) == {"ws-1", "ws-2"}
    assert records["ws-1"].shape == "stale_review"
    assert records["ws-2"].shape == "crash_park"


def test_records_for_matches_last_record_per_key(ready_queue: Queue) -> None:
    _record(ready_queue, workspace_key="ws-1", detail={"reason": "stale"})
    _record(ready_queue, workspace_key="ws-1", ts=1_000_500.0, shape="crash_park")
    batched = ledger.records_for(ready_queue, ["ws-1"])["ws-1"]
    single = ledger.last_record(ready_queue, "ws-1")
    assert single is not None
    assert batched == single


def test_records_for_deduplicates_repeated_keys(ready_queue: Queue) -> None:
    _record(ready_queue, workspace_key="ws-1")
    assert set(ledger.records_for(ready_queue, ["ws-1", "ws-1"])) == {"ws-1"}


def test_records_for_carries_the_delivered_issue_url(ready_queue: Queue) -> None:
    _record(ready_queue, workspace_key="ws-1")
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/9"},
    )
    record = ledger.records_for(ready_queue, ["ws-1"])["ws-1"]
    assert record.issue_url == "https://github.com/o/r/issues/9"


def test_records_for_ignores_a_non_string_issue_url(ready_queue: Queue) -> None:
    _record(ready_queue, workspace_key="ws-1")
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": 7},
    )
    record = ledger.records_for(ready_queue, ["ws-1"])["ws-1"]
    assert record.issue_url is None


def test_records_for_query_count_does_not_scale_with_key_count(
    ready_queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(5):
        _record(ready_queue, workspace_key=f"ws-{i}", fingerprint=f"fp-{i}")
    original_run = ready_queue.run

    def _count_calls(keys: list[str]) -> int:
        calls = {"n": 0}

        def _counting_run(sql: str, params: tuple[object, ...] = ()) -> object:
            calls["n"] += 1
            return original_run(sql, params)

        monkeypatch.setattr(ready_queue, "run", _counting_run)
        ledger.records_for(ready_queue, keys)
        monkeypatch.setattr(ready_queue, "run", original_run)
        return calls["n"]

    one_key_calls = _count_calls(["ws-0"])
    five_key_calls = _count_calls([f"ws-{i}" for i in range(5)])
    assert one_key_calls == five_key_calls


def test_rescue_window_start_matches_the_thirty_day_window_used_by_outcome_rate_30d() -> None:
    from alan import health_repo_metrics as health_repo_metrics_mod

    assert ledger.RESCUE_WINDOW_DAYS == health_repo_metrics_mod._WINDOW_DAYS  # noqa: SLF001
    now = 2_000_000.0
    assert ledger.rescue_window_start(now) == now - ledger.RESCUE_WINDOW_DAYS * ledger.DAY_S


def test_rescue_stats_with_no_ledger_rows_is_zero_and_low_data(queue: Queue) -> None:
    stats = ledger.rescue_stats(queue, ["ws-1", "ws-2"], set(), now=2_000_000.0)
    assert stats.success_rate is None
    assert stats.success_sample == 0
    assert stats.success_low_data is True
    assert stats.load_rate == pytest.approx(0.0)
    assert stats.load_sample == 2
    assert stats.load_low_data is True


def test_rescue_stats_counts_a_cleared_engaged_workspace_as_success(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 10)
    stats = ledger.rescue_stats(ready_queue, ["ws-1"], set(), now)
    assert stats.success_rate == pytest.approx(1.0)
    assert stats.success_sample == 1
    assert stats.load_rate == pytest.approx(1.0)
    assert stats.load_sample == 1


def test_rescue_stats_excludes_engaged_still_stuck_unreported_from_both_num_and_denom(
    ready_queue: Queue,
) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", kind=ledger.KIND_WAIT, outcome=None, ts=now - 10)
    stats = ledger.rescue_stats(ready_queue, ["ws-1"], {"ws-1"}, now)
    assert stats.success_rate is None
    assert stats.success_sample == 0
    assert stats.load_rate == pytest.approx(1.0)
    assert stats.load_sample == 1


def test_rescue_stats_counts_a_reported_still_stuck_workspace_as_a_non_clearance(
    ready_queue: Queue,
) -> None:
    now = 2_000_000.0
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_REPORT,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        ts=now - 10,
    )
    stats = ledger.rescue_stats(ready_queue, ["ws-1"], {"ws-1"}, now)
    assert stats.success_rate == pytest.approx(0.0)
    assert stats.success_sample == 1
    assert stats.load_rate == pytest.approx(1.0)


def test_rescue_stats_ignores_rows_outside_the_window(ready_queue: Queue) -> None:
    now = 2_000_000.0
    stale_ts = now - (ledger.RESCUE_WINDOW_DAYS + 1) * ledger.DAY_S
    _record(ready_queue, workspace_key="ws-1", ts=stale_ts)
    stats = ledger.rescue_stats(ready_queue, ["ws-1"], set(), now)
    assert stats.success_sample == 0
    assert stats.load_sample == 1
    assert stats.load_rate == pytest.approx(0.0)


def test_rescue_stats_ignores_workspaces_outside_the_given_population(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-outside", ts=now - 10)
    stats = ledger.rescue_stats(ready_queue, ["ws-1"], set(), now)
    assert stats.load_sample == 1
    assert stats.load_rate == pytest.approx(0.0)
    assert stats.success_sample == 0


def test_rescue_window_start_takes_a_window_in_days() -> None:
    now = 2_000_000.0
    assert ledger.rescue_window_start(now, 7) == now - 7 * ledger.DAY_S
    assert ledger.rescue_window_start(now) == ledger.rescue_window_start(
        now, ledger.RESCUE_WINDOW_DAYS
    )


def test_rescue_stats_default_window_is_the_thirty_day_rescue_window(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 20 * ledger.DAY_S)
    assert ledger.rescue_stats(ready_queue, ["ws-1"], set(), now) == ledger.rescue_stats(
        ready_queue, ["ws-1"], set(), now, window_days=ledger.RESCUE_WINDOW_DAYS
    )


def test_rescue_stats_short_window_drops_engagement_older_than_the_window(
    ready_queue: Queue,
) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 20 * ledger.DAY_S)
    _record(ready_queue, workspace_key="ws-2", ts=now - 2 * ledger.DAY_S)
    week = ledger.rescue_stats(ready_queue, ["ws-1", "ws-2"], set(), now, window_days=7)
    month = ledger.rescue_stats(ready_queue, ["ws-1", "ws-2"], set(), now, window_days=30)
    assert week.success_sample == 1
    assert week.load_rate == pytest.approx(0.5)
    assert month.success_sample == 2
    assert month.load_rate == pytest.approx(1.0)


def test_rescue_stats_window_edge_is_inclusive(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 7 * ledger.DAY_S)
    _record(ready_queue, workspace_key="ws-2", ts=now - 7 * ledger.DAY_S - 1)
    stats = ledger.rescue_stats(ready_queue, ["ws-1", "ws-2"], set(), now, window_days=7)
    assert stats.success_sample == 1
    assert stats.load_rate == pytest.approx(0.5)


def test_rescue_stats_windows_only_engagement_never_the_population(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 20 * ledger.DAY_S)
    keys = ["ws-1", "ws-2", "ws-3"]
    week = ledger.rescue_stats(ready_queue, keys, {"ws-3"}, now, window_days=7)
    month = ledger.rescue_stats(ready_queue, keys, {"ws-3"}, now, window_days=30)
    assert week.load_sample == month.load_sample == 3
    assert week.load_rate == pytest.approx(0.0)
    assert month.load_rate == pytest.approx(1 / 3)


def test_rescue_stats_windows_a_report_by_its_own_row_timestamp(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 2 * ledger.DAY_S)
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_REPORT,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        ts=now - 20 * ledger.DAY_S,
    )
    week = ledger.rescue_stats(ready_queue, ["ws-1"], {"ws-1"}, now, window_days=7)
    month = ledger.rescue_stats(ready_queue, ["ws-1"], {"ws-1"}, now, window_days=30)
    assert week.success_sample == 0
    assert week.load_rate == pytest.approx(1.0)
    assert month.success_sample == 1
    assert month.success_rate == pytest.approx(0.0)


def test_rescue_stats_wider_window_reaches_older_engagement(ready_queue: Queue) -> None:
    now = 2_000_000.0
    _record(ready_queue, workspace_key="ws-1", ts=now - 40 * ledger.DAY_S)
    default = ledger.rescue_stats(ready_queue, ["ws-1"], set(), now)
    wider = ledger.rescue_stats(ready_queue, ["ws-1"], set(), now, window_days=60)
    assert default.success_sample == 0
    assert wider.success_sample == 1


def test_issue_urls_for_returns_the_latest_delivered_url_per_workspace(
    ready_queue: Queue,
) -> None:
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/1"},
    )
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": "https://github.com/o/r/issues/2"},
    )
    _record(ready_queue, workspace_key="ws-2")

    urls = ledger.issue_urls_for(ready_queue, ["ws-1", "ws-2"])

    assert urls == {"ws-1": "https://github.com/o/r/issues/2"}


def test_issue_urls_for_maps_a_non_string_url_to_none(ready_queue: Queue) -> None:
    _record(
        ready_queue,
        workspace_key="ws-1",
        kind=ledger.KIND_ISSUE_DELIVERED,
        detail={"issue_url": 7},
    )

    assert ledger.issue_urls_for(ready_queue, ["ws-1"]) == {"ws-1": None}


def test_issue_urls_for_no_keys_is_empty(ready_queue: Queue) -> None:
    assert ledger.issue_urls_for(ready_queue, []) == {}


def test_activity_rows_since_returns_attempt_wait_report_newest_first(
    ready_queue: Queue,
) -> None:
    _record(ready_queue, ts=1_000_100.0, kind=ledger.KIND_ATTEMPT)
    _record(ready_queue, ts=1_000_300.0, kind=ledger.KIND_REPORT)
    _record(ready_queue, ts=1_000_200.0, kind=ledger.KIND_WAIT)
    _record(ready_queue, ts=1_000_400.0, kind=ledger.KIND_ISSUE_INTENT)

    rows = ledger.activity_rows_since(ready_queue, 1_000_000.0)

    assert [row.kind for row in rows] == [
        ledger.KIND_REPORT,
        ledger.KIND_WAIT,
        ledger.KIND_ATTEMPT,
    ]


def test_activity_rows_since_is_inclusive_of_the_cutoff(ready_queue: Queue) -> None:
    _record(ready_queue, ts=1_000_000.0)
    _record(ready_queue, ts=999_999.0)

    assert len(ledger.activity_rows_since(ready_queue, 1_000_000.0)) == 1


def test_activity_rows_since_creates_the_schema_on_a_fresh_queue(queue: Queue) -> None:
    assert ledger.activity_rows_since(queue, 0.0) == []


def test_stamp_formats_a_utc_minute_timestamp() -> None:
    assert ledger.stamp(0.0) == "1970-01-01T00:00Z"


@pytest.mark.parametrize(
    ("question_id", "invocation_id", "blocker", "expected"),
    [
        ("non_actionable_feedback_abcdef12", "abcdef12-0000", None, "non_actionable_feedback"),
        ("declared_blocker_abcdef12", "abcdef12xyz", "ignored", "declared_blocker"),
        ("verif-capability-boundary-abcdef12", "abcdef12", None, "verif-capability-boundary"),
        ("abcdef12", "abcdef12", None, ""),
        ("later_prefix-abcdef12", "abcdef12", None, "later_prefix"),
        ("pr-merge-timeout", "inv-seed", None, "pr-merge-timeout"),
        ("verif-judge-parse", "abcdef12", None, "verif-judge-parse"),
        ("pr-merge-timeout", "", None, "pr-merge-timeout"),
        (None, "abcdef12", "pr-merge-timeout", "pr-merge-timeout"),
        (None, "abcdef12", None, ""),
    ],
)
def test_park_class_strips_the_parking_invocation_token(
    question_id: str | None, invocation_id: str, blocker: str | None, expected: str
) -> None:
    assert ledger.park_class(question_id, invocation_id, blocker) == expected


def test_park_class_is_stable_across_reinvocations() -> None:
    first = ledger.park_class("non_actionable_feedback_11111111", "11111111-a", None)
    second = ledger.park_class("non_actionable_feedback_22222222", "22222222-b", None)
    assert first == second


def test_park_key_combines_class_and_head() -> None:
    assert ledger.park_key("cls", "abc123") == "cls@abc123"
    assert ledger.park_key("cls", None) == "cls@"
    assert ledger.park_key("cls", "") == "cls@"
    assert ledger.park_key("cls", "a") != ledger.park_key("other", "a")


def test_report_history_counts_report_rows_of_one_workspace_and_fingerprint(
    ready_queue: Queue,
) -> None:
    _record(ready_queue, kind=ledger.KIND_REPORT, ts=300.0)
    _record(ready_queue, kind=ledger.KIND_REPORT, ts=100.0)
    _record(ready_queue, kind=ledger.KIND_ATTEMPT, ts=50.0)
    _record(ready_queue, kind=ledger.KIND_REPORT, fingerprint="fp-2", ts=10.0)
    _record(ready_queue, kind=ledger.KIND_REPORT, workspace_key="ws-2", ts=5.0)
    assert ledger.report_history(ready_queue, "ws-1", "fp-1") == ledger.ReportHistory(2, 100.0)


def test_report_history_of_an_unseen_park_is_empty(ready_queue: Queue) -> None:
    assert ledger.report_history(ready_queue, "ws-1", "fp-1") == ledger.ReportHistory(0, 0.0)


def test_repeat_reason_is_unchanged_for_a_first_report() -> None:
    assert ledger.repeat_reason("why", ledger.ReportHistory(0, 0.0), "abcdef1234") == "why"


def test_repeat_reason_names_the_ordinal_the_short_head_and_the_first_report() -> None:
    reason = ledger.repeat_reason("why", ledger.ReportHistory(2, 1_000_000.0), "abcdef1234567")
    assert reason == (
        f"why · report #3 of this park class at head abcdef12"
        f", first reported {ledger.stamp(1_000_000.0)}"
    )


def test_repeat_reason_omits_the_head_when_none_was_read() -> None:
    reason = ledger.repeat_reason("why", ledger.ReportHistory(1, 1_000_000.0), None)
    assert "at head" not in reason
    assert reason.startswith("why · report #2 of this park class, first reported ")

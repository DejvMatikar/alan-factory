from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from alan import unblock_ledger as ledger
from alan import unblock_sweep as sweep_mod
from alan.queue import Queue

IDENTITY = "handover_seed@2026-09-19T17:00:00+00:00"
REPORT_AT = 1_000_000.0
NEXT_AT = 2_000_000.0
REASON = "no recipe recognises this park"
WATCH_FIELDS = ("watch_attempts", "watch_cap", "watch_next_at", "watch_exhausted")


@pytest.fixture
def ready_queue(queue: Queue) -> Queue:
    ledger.ensure_schema(queue)
    return queue


def _seed_row(
    queue: Queue,
    *,
    workspace_key: str = "ws-1",
    handover_id: str | None = IDENTITY,
    kind: str = ledger.KIND_REPORT,
) -> int:
    return ledger.record(
        queue,
        ledger.Entry(
            workspace_key=workspace_key,
            scope_ref="pr:1",
            shape="needs_human",
            fingerprint="fp-1",
            kind=kind,
            outcome=ledger.OUTCOME_NEEDS_HUMAN,
            handover_id=handover_id,
            detail={"reason": REASON},
            ts=REPORT_AT,
        ),
    )


def _entry(
    *,
    attempts: int = 7,
    terminal: bool = True,
    next_at: float = NEXT_AT,
    identity: str = IDENTITY,
) -> ledger.WatchEntry:
    return ledger.WatchEntry(
        next_at=next_at, attempts=attempts, identity=identity, terminal=terminal
    )


def _seed_watch(queue: Queue, watch: dict[str, ledger.WatchEntry]) -> None:
    payload = {key: asdict(entry) for key, entry in watch.items()}
    queue.set_meta(ledger.UNBLOCK_WATCH_META_KEY, json.dumps(payload))


def _served(queue: Queue, workspace_key: str = "ws-1") -> ledger.UnblockRecord:
    record = ledger.last_record(queue, workspace_key)
    assert record is not None
    return record


def _watch_values(record: ledger.UnblockRecord) -> tuple[object, ...]:
    return tuple(getattr(record, name) for name in WATCH_FIELDS)


def _ledger_row_count(queue: Queue) -> int:
    return int(queue.run("SELECT COUNT(*) AS n FROM unblock_ledger")[0]["n"])


def _count_meta_reads(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    original = queue.get_meta

    def _counting(key: str) -> str | None:
        reads.append(key)
        return original(key)

    monkeypatch.setattr(queue, "get_meta", _counting)
    return reads


def test_a_reported_park_serves_the_watch_beside_the_report_timestamp(
    ready_queue: Queue,
) -> None:
    _seed_row(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry(attempts=7)})
    record = _served(ready_queue)
    assert _watch_values(record) == (7, ledger.REPROBE_CAP, NEXT_AT, False)
    assert record.at == REPORT_AT
    assert record.outcome == ledger.OUTCOME_NEEDS_HUMAN


def test_a_declining_reprobe_advances_the_attempt_count_and_leaves_the_timestamp(
    ready_queue: Queue,
) -> None:
    _seed_row(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry(attempts=7)})
    before = _served(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry(attempts=8, next_at=NEXT_AT + 1200.0)})
    after = _served(ready_queue)
    assert (before.watch_attempts, after.watch_attempts) == (7, 8)
    assert after.watch_next_at == NEXT_AT + 1200.0
    assert after.at == before.at == REPORT_AT
    assert _ledger_row_count(ready_queue) == 1


def test_a_park_with_ledger_history_and_no_watch_serves_todays_record(
    ready_queue: Queue,
) -> None:
    _seed_row(ready_queue)
    record = _served(ready_queue)
    assert _watch_values(record) == (None, None, None, None)
    assert record == ledger.UnblockRecord(
        shape="needs_human",
        attempts=0,
        ceiling=ledger.ATTEMPT_CEILING_PER_WORKSPACE,
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        summary=f"needs human · needs_human · {REASON} · {ledger.stamp(REPORT_AT)}",
        issue_url=None,
        at=REPORT_AT,
        reason=REASON,
    )


@pytest.mark.parametrize("handover_id", ["handover_seed@2026-09-19T18:00:00+00:00", None])
def test_a_watch_for_another_park_serves_the_fields_absent(
    ready_queue: Queue, handover_id: str | None
) -> None:
    _seed_row(ready_queue, handover_id=handover_id)
    _seed_watch(ready_queue, {"ws-1": _entry()})
    record = _served(ready_queue)
    assert _watch_values(record) == (None, None, None, None)
    assert record.outcome == ledger.OUTCOME_NEEDS_HUMAN


@pytest.mark.parametrize(
    ("terminal", "attempts", "expected"),
    [(True, 72, True), (True, 71, False), (True, 90, True), (False, 80, False)],
)
def test_the_exhausted_flag_mirrors_the_hosts_predicate(
    ready_queue: Queue, terminal: bool, attempts: int, expected: bool
) -> None:
    _seed_row(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry(terminal=terminal, attempts=attempts)})
    record = _served(ready_queue)
    assert record.watch_exhausted is expected
    assert record.watch_attempts == attempts
    assert record.watch_cap == ledger.REPROBE_CAP


def test_a_past_next_probe_time_is_served_verbatim(ready_queue: Queue) -> None:
    _seed_row(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry(next_at=1.0)})
    assert _served(ready_queue).watch_next_at == 1.0


def test_a_watched_park_with_no_ledger_row_serves_no_record(ready_queue: Queue) -> None:
    _seed_watch(ready_queue, {"ws-1": _entry()})
    assert ledger.last_record(ready_queue, "ws-1") is None
    assert ledger.records_for(ready_queue, ["ws-1"]) == {}


def test_records_for_and_last_record_serve_the_same_values_for_a_workspace(
    ready_queue: Queue,
) -> None:
    _seed_row(ready_queue, workspace_key="ws-1")
    _seed_row(ready_queue, workspace_key="ws-2")
    _seed_watch(ready_queue, {"ws-1": _entry(attempts=3), "ws-2": _entry(attempts=72)})
    batched = ledger.records_for(ready_queue, ["ws-1", "ws-2"])
    assert batched["ws-1"] == _served(ready_queue, "ws-1")
    assert batched["ws-2"] == _served(ready_queue, "ws-2")
    assert (batched["ws-1"].watch_attempts, batched["ws-2"].watch_attempts) == (3, 72)


def test_records_for_leaves_an_unwatched_workspace_absent_beside_a_watched_one(
    ready_queue: Queue,
) -> None:
    _seed_row(ready_queue, workspace_key="ws-1")
    _seed_row(ready_queue, workspace_key="ws-2")
    _seed_watch(ready_queue, {"ws-1": _entry()})
    batched = ledger.records_for(ready_queue, ["ws-1", "ws-2"])
    assert _watch_values(batched["ws-1"])[0] == 7
    assert _watch_values(batched["ws-2"]) == (None, None, None, None)


@pytest.mark.parametrize("count", [1, 5])
def test_records_for_reads_the_watch_once_however_many_workspaces_it_serves(
    ready_queue: Queue, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    keys = [f"ws-{index}" for index in range(count)]
    for key in keys:
        _seed_row(ready_queue, workspace_key=key)
    _seed_watch(ready_queue, {key: _entry() for key in keys})
    reads = _count_meta_reads(ready_queue, monkeypatch)
    assert len(ledger.records_for(ready_queue, keys)) == count
    assert reads == [ledger.UNBLOCK_WATCH_META_KEY]


def test_last_record_reads_the_watch_once(
    ready_queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_row(ready_queue)
    _seed_watch(ready_queue, {"ws-1": _entry()})
    reads = _count_meta_reads(ready_queue, monkeypatch)
    _served(ready_queue)
    assert reads == [ledger.UNBLOCK_WATCH_META_KEY]


@pytest.mark.parametrize("raw", ["{not json", json.dumps([1, 2]), json.dumps("watch")])
def test_a_malformed_watch_blob_serves_the_fields_absent_without_raising(
    ready_queue: Queue, raw: str
) -> None:
    _seed_row(ready_queue)
    ready_queue.set_meta(ledger.UNBLOCK_WATCH_META_KEY, raw)
    assert _watch_values(_served(ready_queue)) == (None, None, None, None)
    assert _watch_values(ledger.records_for(ready_queue, ["ws-1"])["ws-1"]) == (
        None,
        None,
        None,
        None,
    )


def test_an_invalid_entry_does_not_hide_a_valid_sibling(ready_queue: Queue) -> None:
    _seed_row(ready_queue, workspace_key="ws-1")
    _seed_row(ready_queue, workspace_key="ws-2")
    payload = {
        "ws-1": {"next_at": "soon", "attempts": 1, "identity": IDENTITY},
        "ws-2": asdict(_entry(attempts=4)),
    }
    ready_queue.set_meta(ledger.UNBLOCK_WATCH_META_KEY, json.dumps(payload))
    batched = ledger.records_for(ready_queue, ["ws-1", "ws-2"])
    assert _watch_values(batched["ws-1"]) == (None, None, None, None)
    assert batched["ws-2"].watch_attempts == 4


def test_the_watch_fields_are_optional_in_the_served_schema() -> None:
    schema = ledger.UnblockRecord.model_json_schema()
    assert not set(WATCH_FIELDS) & set(schema["required"])
    assert set(WATCH_FIELDS) <= set(schema["properties"])


def test_the_sweep_shares_the_ledgers_single_watch_definition() -> None:
    assert sweep_mod.WatchEntry is ledger.WatchEntry
    assert sweep_mod.load_watch is ledger.load_watch
    assert sweep_mod.REPROBE_CAP == ledger.REPROBE_CAP == 72
    assert sweep_mod.UNBLOCK_WATCH_META_KEY == ledger.UNBLOCK_WATCH_META_KEY


def test_the_sweep_carries_no_second_parser_or_exhaustion_predicate() -> None:
    assert not hasattr(sweep_mod, "_parse_entry")
    assert not hasattr(sweep_mod, "_load_watch")
    assert not hasattr(sweep_mod, "_exhausted")


def test_the_stored_watch_blob_does_not_carry_the_exhausted_flag(queue: Queue) -> None:
    sweep_mod._save_watch(queue, {"ws-1": _entry()})  # noqa: SLF001
    stored = json.loads(queue.get_meta(ledger.UNBLOCK_WATCH_META_KEY) or "{}")
    assert "exhausted" not in stored["ws-1"]
    assert ledger.load_watch(queue) == {"ws-1": _entry()}


@pytest.mark.parametrize(
    "raw",
    [
        "nope",
        {"next_at": "soon", "attempts": 1, "identity": "i"},
        {"next_at": True, "attempts": 1, "identity": "i"},
        {"next_at": -1, "attempts": 1, "identity": "i"},
        {"next_at": 1, "attempts": 1.5, "identity": "i"},
        {"next_at": 1, "attempts": False, "identity": "i"},
        {"next_at": 1, "attempts": -1, "identity": "i"},
        {"next_at": 1, "attempts": 1},
    ],
)
def test_parse_entry_rejects_malformed_entries(raw: object) -> None:
    assert ledger._parse_entry(raw) is None  # noqa: SLF001


def test_parse_entry_reads_flags_strictly() -> None:
    entry = ledger._parse_entry(  # noqa: SLF001
        {"next_at": 5, "attempts": 2, "identity": "i", "acted": 1, "retried": True}
    )
    assert entry == ledger.WatchEntry(
        next_at=5.0, attempts=2, identity="i", acted=False, retried=True, terminal=False
    )


@pytest.mark.parametrize(
    ("raw_head", "expected"),
    [("abc", "abc"), ("", None), (5, None), (None, None), (["abc"], None)],
)
def test_parse_entry_reads_the_park_head_tolerantly(raw_head: object, expected: str | None) -> None:
    entry = ledger._parse_entry(  # noqa: SLF001
        {"next_at": 5, "attempts": 2, "identity": "i", "park_head": raw_head}
    )
    assert entry is not None and entry.park_head == expected


def test_parse_entry_loads_a_record_written_without_a_park_head() -> None:
    entry = ledger._parse_entry({"next_at": 5, "attempts": 2, "identity": "i"})  # noqa: SLF001
    assert entry == ledger.WatchEntry(next_at=5.0, attempts=2, identity="i")
    assert entry is not None and entry.park_head is None


def test_parse_entry_still_requires_only_the_original_triple() -> None:
    assert ledger._parse_entry({"next_at": 5, "attempts": 2, "park_head": "abc"}) is None  # noqa: SLF001


@pytest.mark.parametrize("raw", [None, "{not json", json.dumps([1, 2])])
def test_load_watch_tolerates_a_bad_blob(queue: Queue, raw: str | None) -> None:
    if raw is not None:
        queue.set_meta(ledger.UNBLOCK_WATCH_META_KEY, raw)
    assert ledger.load_watch(queue) == {}

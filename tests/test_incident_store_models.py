from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import pytest

from alan.incident import Disposition, Suppression
from alan.incident_store import (
    DISPOSITIONS_META_KEY,
    DispositionRecord,
    ack,
    load_dispositions,
    set_disposition,
    set_suppression,
)
from alan.queue import Queue

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()

_RECORD_KEYS = {
    "class",
    "disposition",
    "disposition_detail",
    "suppression",
    "ack",
    "opened_at",
    "closed_at",
    "fingerprint_at_close",
    "occurrence_at_close",
    "occurrence_count",
    "last_seen",
    "recurrence_counter",
    "diagnosis_falsified",
    "auto_disposed",
}

_SIBLING: dict[str, Any] = {
    "class": "job_failure",
    "disposition": "root_caused",
    "disposition_detail": "fixed upstream",
    "suppression": {"reason": "flaky", "ttl_epoch": 1234.5, "recurrence_threshold": 3},
    "ack": {"at": "2026-01-01T00:00:00+00:00"},
    "opened_at": "2026-01-01T00:00:00+00:00",
    "closed_at": "2026-01-02T00:00:00+00:00",
    "fingerprint_at_close": "fp_sibling",
    "occurrence_at_close": 2,
    "occurrence_count": 4,
    "last_seen": "2026-01-03T00:00:00+00:00",
    "recurrence_counter": 1,
    "diagnosis_falsified": True,
    "auto_disposed": True,
}

_MALFORMED_STORED: dict[str, Any] = {
    "class": None,
    "disposition": "bogus",
    "disposition_detail": None,
    "suppression": "nope",
    "ack": 7,
    "opened_at": 123,
    "closed_at": None,
    "fingerprint_at_close": None,
    "occurrence_at_close": None,
    "occurrence_count": "x",
    "last_seen": [1],
    "recurrence_counter": 7.9,
    "diagnosis_falsified": "no",
    "auto_disposed": 1,
    "zzz": "unknown",
}

_MALFORMED_WRITTEN: dict[str, Any] = {
    "class": "None",
    "disposition": None,
    "disposition_detail": None,
    "suppression": None,
    "ack": None,
    "opened_at": "123",
    "closed_at": None,
    "fingerprint_at_close": None,
    "occurrence_at_close": None,
    "occurrence_count": 0,
    "last_seen": "[1]",
    "recurrence_counter": 7,
    "diagnosis_falsified": True,
    "auto_disposed": False,
}

_EMPTY_WRITTEN: dict[str, Any] = {
    "class": "",
    "disposition": None,
    "disposition_detail": None,
    "suppression": None,
    "ack": None,
    "opened_at": "",
    "closed_at": None,
    "fingerprint_at_close": None,
    "occurrence_at_close": None,
    "occurrence_count": 0,
    "last_seen": "",
    "recurrence_counter": 0,
    "diagnosis_falsified": False,
    "auto_disposed": False,
}


def _store(queue: Queue, blob: object) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps(blob))


def _stored(queue: Queue) -> dict[str, dict[str, Any]]:
    raw = queue.get_meta(DISPOSITIONS_META_KEY)
    assert raw is not None
    stored: dict[str, dict[str, Any]] = json.loads(raw)
    return stored


def _suppression_blob(**overrides: Any) -> dict[str, Any]:
    suppression: dict[str, Any] = {
        "reason": "flaky",
        "ttl_epoch": 1234.5,
        "recurrence_threshold": 3,
    }
    suppression.update(overrides)
    return {"fp1": {"class": "job_failure", "suppression": suppression}}


@pytest.fixture
def parity_queue(queue: Queue) -> Queue:
    _store(
        queue,
        {
            "fp_sibling": _SIBLING,
            "fp_malformed": _MALFORMED_STORED,
            "fp_empty": {"ack": {"note": 1}},
            "fp_text": "not-an-object",
        },
    )
    return queue


@pytest.fixture
def parity_written(parity_queue: Queue) -> dict[str, dict[str, Any]]:
    set_suppression(
        parity_queue,
        "fp_new",
        Suppression(reason="r", ttl_epoch=99.0, recurrence_threshold=2),
        now=_NOW,
    )
    return _stored(parity_queue)


def test_sibling_of_malformed_record_survives_load_and_save(
    parity_written: dict[str, dict[str, Any]],
) -> None:
    assert parity_written["fp_sibling"] == _SIBLING


def test_malformed_record_is_written_with_head_fallbacks(
    parity_written: dict[str, dict[str, Any]],
) -> None:
    assert parity_written["fp_malformed"] == _MALFORMED_WRITTEN


def test_record_with_every_key_missing_is_written_with_defaults(
    parity_written: dict[str, dict[str, Any]],
) -> None:
    assert parity_written["fp_empty"] == _EMPTY_WRITTEN


def test_non_object_record_is_not_written_back(
    parity_written: dict[str, dict[str, Any]],
) -> None:
    assert set(parity_written) == {"fp_sibling", "fp_malformed", "fp_empty", "fp_new"}


def test_unknown_key_is_never_written_back(parity_written: dict[str, dict[str, Any]]) -> None:
    assert "zzz" not in parity_written["fp_malformed"]
    assert set(parity_written["fp_malformed"]) == _RECORD_KEYS


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("incident_class", "None"),
        ("disposition", None),
        ("disposition_detail", None),
        ("suppression", None),
        ("ack_at", None),
        ("opened_at", "123"),
        ("closed_at", None),
        ("fingerprint_at_close", None),
        ("occurrence_at_close", None),
        ("occurrence_count", 0),
        ("last_seen", "[1]"),
        ("recurrence_counter", 7),
        ("diagnosis_falsified", True),
        ("auto_disposed", False),
    ],
)
def test_malformed_field_loads_as_its_fallback(
    parity_queue: Queue, attribute: str, expected: object
) -> None:
    value = getattr(load_dispositions(parity_queue)["fp_malformed"], attribute)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("incident_class", ""),
        ("opened_at", ""),
        ("last_seen", ""),
        ("ack_at", None),
        ("suppression", None),
        ("occurrence_count", 0),
        ("recurrence_counter", 0),
        ("diagnosis_falsified", False),
        ("auto_disposed", False),
    ],
)
def test_missing_field_loads_as_its_default(
    parity_queue: Queue, attribute: str, expected: object
) -> None:
    assert getattr(load_dispositions(parity_queue)["fp_empty"], attribute) == expected


def test_non_object_record_is_skipped_on_load(parity_queue: Queue) -> None:
    assert "fp_text" not in load_dispositions(parity_queue)


@pytest.mark.parametrize("stored", ["text", [1], 7, None])
def test_non_object_records_of_every_kind_are_skipped(queue: Queue, stored: object) -> None:
    _store(queue, {"fp_bad": stored, "fp_ok": {"class": "x"}})
    assert set(load_dispositions(queue)) == {"fp_ok"}


def test_non_json_blob_loads_as_empty(queue: Queue) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, "{not json")
    assert load_dispositions(queue) == {}


@pytest.mark.parametrize("root", [[1, 2], "text", 7, None])
def test_non_object_root_loads_as_empty(queue: Queue, root: object) -> None:
    _store(queue, root)
    assert load_dispositions(queue) == {}


def test_infinite_occurrence_count_surfaces_as_overflow_error(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        '{"fp_ok": {"class": "x"}, "fp_inf": {"class": "y", "occurrence_count": Infinity}}',
    )
    with pytest.raises(OverflowError):
        load_dispositions(queue)


def test_infinite_recurrence_counter_surfaces_as_overflow_error(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        '{"fp_inf": {"class": "y", "recurrence_counter": Infinity}}',
    )
    with pytest.raises(OverflowError):
        load_dispositions(queue)


def test_attribute_named_keys_in_stored_record_are_ignored_on_read(queue: Queue) -> None:
    _store(queue, {"fp1": {"incident_class": "LEAK", "ack_at": "LEAK"}})
    record = load_dispositions(queue)["fp1"]
    assert record.incident_class == ""
    assert record.ack_at is None


def test_attribute_named_keys_are_not_written_back(queue: Queue) -> None:
    _store(queue, {"fp1": {"incident_class": "LEAK", "ack_at": "LEAK", "zzz": "LEAK"}})
    set_suppression(
        queue, "fp2", Suppression(reason="r", ttl_epoch=1.0, recurrence_threshold=1), now=_NOW
    )
    written = _stored(queue)["fp1"]
    assert set(written) == _RECORD_KEYS
    assert "LEAK" not in json.dumps(written)


def test_full_record_is_written_in_the_stored_shape(queue: Queue) -> None:
    set_disposition(
        queue,
        "fp1",
        Disposition.ROOT_CAUSED,
        incident_class="job_failure",
        detail="fixed upstream",
        now=_NOW,
    )
    set_suppression(
        queue,
        "fp1",
        Suppression(reason="flaky", ttl_epoch=1234.5, recurrence_threshold=3),
        now=_NOW,
    )
    ack(queue, "fp1", now=_NOW)
    assert _stored(queue)["fp1"] == {
        "class": "job_failure",
        "disposition": "root_caused",
        "disposition_detail": "fixed upstream",
        "suppression": {"reason": "flaky", "ttl_epoch": 1234.5, "recurrence_threshold": 3},
        "ack": {"at": _NOW_ISO},
        "opened_at": _NOW_ISO,
        "closed_at": _NOW_ISO,
        "fingerprint_at_close": "fp1",
        "occurrence_at_close": 1,
        "occurrence_count": 1,
        "last_seen": _NOW_ISO,
        "recurrence_counter": 0,
        "diagnosis_falsified": False,
        "auto_disposed": False,
    }


def test_fresh_record_is_written_with_seven_null_keys(queue: Queue) -> None:
    set_disposition(queue, "fp1", None, incident_class="job_failure", now=_NOW)
    written = _stored(queue)["fp1"]
    assert written == {
        "class": "job_failure",
        "disposition": None,
        "disposition_detail": None,
        "suppression": None,
        "ack": None,
        "opened_at": _NOW_ISO,
        "closed_at": None,
        "fingerprint_at_close": None,
        "occurrence_at_close": None,
        "occurrence_count": 1,
        "last_seen": _NOW_ISO,
        "recurrence_counter": 0,
        "diagnosis_falsified": False,
        "auto_disposed": False,
    }
    assert sum(value is None for value in written.values()) == 7


def test_infinite_ttl_epoch_round_trips_through_a_later_mutation(queue: Queue) -> None:
    set_suppression(
        queue,
        "fp1",
        Suppression(reason="r", ttl_epoch=float("inf"), recurrence_threshold=3),
        now=_NOW,
    )
    ack(queue, "fp1", now=_NOW)
    suppression = load_dispositions(queue)["fp1"].suppression
    assert suppression is not None
    assert suppression.ttl_epoch == math.inf
    raw = queue.get_meta(DISPOSITIONS_META_KEY)
    assert raw is not None
    assert "Infinity" in raw


def test_nan_ttl_epoch_loads_as_nan(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        '{"fp1": {"class": "x", "suppression": '
        '{"reason": "r", "ttl_epoch": NaN, "recurrence_threshold": 3}}}',
    )
    suppression = load_dispositions(queue)["fp1"].suppression
    assert suppression is not None
    assert math.isnan(suppression.ttl_epoch)


def test_record_is_constructed_by_attribute_name() -> None:
    suppression = Suppression(reason="r", ttl_epoch=1.0, recurrence_threshold=2)
    record = DispositionRecord(
        incident_class="job_failure",
        disposition=Disposition.BY_DESIGN,
        suppression=suppression,
        ack_at="2026-01-01T00:00:00+00:00",
        occurrence_count=3,
    )
    assert record.incident_class == "job_failure"
    assert record.disposition == Disposition.BY_DESIGN
    assert record.suppression == suppression
    assert record.ack_at == "2026-01-01T00:00:00+00:00"
    assert record.occurrence_count == 3


def test_record_is_updated_by_attribute_name_through_the_mutators(queue: Queue) -> None:
    acked = ack(queue, "fp1", incident_class="job_failure", now=_NOW)
    closed = set_disposition(queue, "fp1", Disposition.KNOWN_FLAKE, detail="flaky", now=_NOW)
    assert acked.ack_at == _NOW_ISO
    assert closed.ack_at == _NOW_ISO
    assert closed.disposition == Disposition.KNOWN_FLAKE
    assert closed.disposition_detail == "flaky"


def test_equal_records_hash_the_same() -> None:
    suppression = Suppression(reason="r", ttl_epoch=1.0, recurrence_threshold=2)
    first = DispositionRecord(incident_class="x", suppression=suppression, occurrence_count=2)
    second = DispositionRecord(incident_class="x", suppression=suppression, occurrence_count=2)
    assert first == second
    assert hash(first) == hash(second)


def test_suppression_missing_a_key_loads_as_none(queue: Queue) -> None:
    _store(queue, {"fp1": {"class": "x", "suppression": {"reason": "r", "ttl_epoch": 1.0}}})
    assert load_dispositions(queue)["fp1"].suppression is None


@pytest.mark.parametrize("threshold", [5.0, "5"])
def test_suppression_threshold_convertible_to_int_loads_as_int(
    queue: Queue, threshold: object
) -> None:
    _store(queue, _suppression_blob(recurrence_threshold=threshold))
    suppression = load_dispositions(queue)["fp1"].suppression
    assert suppression is not None
    assert suppression.recurrence_threshold == 5
    assert type(suppression.recurrence_threshold) is int


def test_suppression_with_an_extra_key_loads_without_it(queue: Queue) -> None:
    _store(queue, _suppression_blob(extra="ignored"))
    set_disposition(queue, "fp2", None, now=_NOW)
    assert _stored(queue)["fp1"]["suppression"] == {
        "reason": "flaky",
        "ttl_epoch": 1234.5,
        "recurrence_threshold": 3,
    }


def test_suppression_with_a_null_ttl_epoch_loads_as_none(queue: Queue) -> None:
    _store(queue, _suppression_blob(ttl_epoch=None))
    assert load_dispositions(queue)["fp1"].suppression is None

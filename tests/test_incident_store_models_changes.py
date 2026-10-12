from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from alan.digest import AuditWindowCounts, DigestDoc, render_digest
from alan.incident_store import (
    DISPOSITIONS_META_KEY,
    DispositionRecord,
    is_acked,
    load_dispositions,
)
from alan.queue import Queue

_SINCE = "2026-07-04T09:00:00+00:00"
_NOW = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)
_IN_WINDOW = "2026-07-04T10:00:00+00:00"

_RECORD_FIELDS = {
    "incident_class",
    "disposition",
    "disposition_detail",
    "suppression",
    "ack_at",
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


def _store(queue: Queue, record: dict[str, Any]) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps({"fp1": {"class": "job_failure", **record}}))


def _load(queue: Queue, record: dict[str, Any]) -> DispositionRecord:
    _store(queue, record)
    return load_dispositions(queue)["fp1"]


def _digest(queue: Queue, record: dict[str, Any]) -> DigestDoc:
    _store(queue, {"opened_at": _IN_WINDOW, "last_seen": _IN_WINDOW, **record})
    return render_digest(
        load_dispositions(queue), [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW
    )


def _suppression(**overrides: Any) -> dict[str, Any]:
    suppression: dict[str, Any] = {
        "reason": "flaky",
        "ttl_epoch": 1234.5,
        "recurrence_threshold": 3,
    }
    suppression.update(overrides)
    return {"suppression": suppression}


def test_non_string_ack_time_is_carried_as_its_string_form(queue: Queue) -> None:
    assert _load(queue, {"ack": {"at": 123}}).ack_at == "123"


def test_non_string_closed_at_is_carried_as_its_string_form(queue: Queue) -> None:
    assert _load(queue, {"closed_at": 5}).closed_at == "5"


def test_non_string_disposition_detail_is_carried_as_its_string_form(queue: Queue) -> None:
    assert _load(queue, {"disposition_detail": 5}).disposition_detail == "5"


def test_non_string_fingerprint_at_close_is_carried_as_its_string_form(queue: Queue) -> None:
    assert _load(queue, {"fingerprint_at_close": [1]}).fingerprint_at_close == "[1]"


def test_digest_no_longer_errors_on_a_non_string_closed_at(queue: Queue) -> None:
    doc = _digest(queue, {"disposition": "root_caused", "closed_at": 5})
    assert doc.closed == ()
    assert doc.fingerprints == frozenset()


def test_falsy_non_string_closed_at_becomes_a_truthy_string_in_the_digest(queue: Queue) -> None:
    doc = _digest(queue, {"closed_at": False})
    assert doc.fingerprints == frozenset()


def test_ack_time_whose_string_form_parses_as_a_date_compares_as_a_date(queue: Queue) -> None:
    record = _load(queue, {"ack": {"at": 20260101}})
    assert is_acked(record, fresh_last_seen="2026-01-02T00:00:00") is False


@pytest.mark.parametrize(("stored", "expected"), [(3.0, 3), ("3", 3), (True, 1)])
def test_integer_like_occurrence_at_close_is_normalised_to_an_int(
    queue: Queue, stored: object, expected: int
) -> None:
    value = _load(queue, {"occurrence_at_close": stored}).occurrence_at_close
    assert value == expected
    assert type(value) is int


@pytest.mark.parametrize("stored", [3.5, "x", [1]])
def test_non_integer_occurrence_at_close_becomes_none(queue: Queue, stored: object) -> None:
    assert _load(queue, {"occurrence_at_close": stored}).occurrence_at_close is None


def _assert_kept_without_suppression(record: DispositionRecord) -> None:
    assert record.incident_class == "job_failure"
    assert record.suppression is None


def test_numeric_suppression_reason_drops_the_suppression_and_keeps_the_record(
    queue: Queue,
) -> None:
    _assert_kept_without_suppression(_load(queue, _suppression(reason=5)))


def test_null_suppression_reason_drops_the_suppression_and_keeps_the_record(
    queue: Queue,
) -> None:
    _assert_kept_without_suppression(_load(queue, _suppression(reason=None)))


def test_fractional_suppression_threshold_drops_the_suppression_and_keeps_the_record(
    queue: Queue,
) -> None:
    _assert_kept_without_suppression(_load(queue, _suppression(recurrence_threshold=3.5)))


def test_non_finite_suppression_threshold_drops_the_suppression_and_keeps_the_record(
    queue: Queue,
) -> None:
    _assert_kept_without_suppression(
        _load(queue, _suppression(recurrence_threshold=float("inf")))
    )


def test_string_suppression_threshold_with_a_zero_fraction_is_now_read_as_an_int(
    queue: Queue,
) -> None:
    suppression = _load(queue, _suppression(recurrence_threshold="5.0")).suppression
    assert suppression is not None
    assert suppression.recurrence_threshold == 5


def test_float_suppression_threshold_beyond_the_integer_range_drops_the_suppression(
    queue: Queue,
) -> None:
    _assert_kept_without_suppression(_load(queue, _suppression(recurrence_threshold=1e30)))


def test_record_is_a_pydantic_model() -> None:
    assert isinstance(DispositionRecord(incident_class="x"), BaseModel)


def test_assigning_an_attribute_raises_a_validation_error() -> None:
    record = DispositionRecord(incident_class="x")
    with pytest.raises(ValidationError):
        record.opened_at = "changed"


def test_record_declares_exactly_the_fourteen_fields() -> None:
    assert set(DispositionRecord.model_fields) == _RECORD_FIELDS

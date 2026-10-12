from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.teams import _forge_quota


def test_quota_sample_is_frozen() -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site="x", limit=1, cost=1, remaining=1,
        reset_at=1.0, outcome="ok",
    )
    with pytest.raises(FrozenInstanceError):
        sample.limit = 2  # type: ignore[misc]


def test_record_quota_sample_appends_jsonl(tmp_path: Path) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site="test_record", limit=100, cost=1,
        remaining=99, reset_at=123.0, outcome="ok",
    )
    _forge_quota.record_quota_sample(sample)
    path = tmp_path / "logs" / "gh_quota.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["call_site"] == "test_record"
    assert row["limit"] == 100
    assert row["outcome"] == "ok"


def test_record_quota_sample_updates_last_sample() -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site="test_last", limit=100, cost=1,
        remaining=50, reset_at=None, outcome="ok",
    )
    _forge_quota.record_quota_sample(sample)
    assert _forge_quota.last_sample("test_last") == sample


def test_last_sample_missing_call_site_returns_none() -> None:
    assert _forge_quota.last_sample("never_recorded_xyz") is None


def test_record_quota_sample_rotates_oversized_log(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "gh_quota.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * (_forge_quota._QUOTA_LOG_MAX_BYTES + 1))  # noqa: SLF001
    sample = _forge_quota.QuotaSample(
        resource="core", call_site="test_rotate", limit=None, cost=None,
        remaining=None, reset_at=None, outcome="error",
    )
    _forge_quota.record_quota_sample(sample)
    assert (tmp_path / "logs" / "gh_quota.jsonl.1").exists()
    assert len(path.read_text().splitlines()) == 1


def test_record_quota_sample_does_not_rotate_under_cap(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "gh_quota.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("existing\n")
    sample = _forge_quota.QuotaSample(
        resource="core", call_site="test_no_rotate", limit=None, cost=None,
        remaining=None, reset_at=None, outcome="ok",
    )
    _forge_quota.record_quota_sample(sample)
    assert not (tmp_path / "logs" / "gh_quota.jsonl.1").exists()
    lines = path.read_text().splitlines()
    assert lines[0] == "existing"
    assert len(lines) == 2


def test_rotate_if_oversized_missing_file_is_noop(tmp_path: Path) -> None:
    _forge_quota._rotate_if_oversized(tmp_path / "missing.jsonl")  # noqa: SLF001


def test_rotate_if_oversized_replace_failure_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "gh_quota.jsonl"
    path.write_bytes(b"x" * (_forge_quota._QUOTA_LOG_MAX_BYTES + 1))  # noqa: SLF001

    def boom(self: Path, target: Path) -> Path:
        del self, target
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom)
    _forge_quota._rotate_if_oversized(path)  # noqa: SLF001
    assert not (tmp_path / "gh_quota.jsonl.1").exists()


def test_reset_at_from_text_none_returns_none() -> None:
    assert _forge_quota.reset_at_from_text(None) is None


def test_reset_at_from_text_empty_returns_none() -> None:
    assert _forge_quota.reset_at_from_text("") is None


def test_reset_at_from_text_no_match_returns_none() -> None:
    assert _forge_quota.reset_at_from_text("nothing relevant here") is None


def test_reset_at_from_text_parses_iso_resetat() -> None:
    result = _forge_quota.reset_at_from_text('{"resetAt":"2026-08-19T15:04:05Z"}')
    expected = datetime(2026, 8, 19, 15, 4, 5, tzinfo=UTC).timestamp()
    assert result == pytest.approx(expected)


def test_reset_at_from_text_invalid_iso_falls_back_to_epoch() -> None:
    text = '{"resetAt":"2026-13-40T99:99:99Z","reset":1700000000}'
    assert _forge_quota.reset_at_from_text(text) == 1700000000.0


def test_reset_at_from_text_parses_epoch_seconds() -> None:
    assert _forge_quota.reset_at_from_text('"reset":1700000000') == 1700000000.0


def test_reset_at_from_text_parses_epoch_milliseconds() -> None:
    assert _forge_quota.reset_at_from_text('"resetAt":1700000000123') == 1700000000.0


def test_reset_at_from_text_iso_epoch_start_is_not_discarded() -> None:
    assert _forge_quota.reset_at_from_text('{"resetAt":"1970-01-01T00:00:00Z"}') == 0.0


def test_low_headroom_true_when_below_threshold() -> None:
    assert _forge_quota.low_headroom(50, 5000) is True


def test_low_headroom_false_when_above_threshold() -> None:
    assert _forge_quota.low_headroom(500, 5000) is False


def test_low_headroom_false_when_limit_none() -> None:
    assert _forge_quota.low_headroom(10, None) is False


def test_low_headroom_false_when_limit_zero() -> None:
    assert _forge_quota.low_headroom(10, 0) is False


def test_low_headroom_false_when_remaining_none() -> None:
    assert _forge_quota.low_headroom(None, 5000) is False


def test_parse_graphql_rate_limit_missing_data_returns_null_sample() -> None:
    sample = _forge_quota.parse_graphql_rate_limit("not json", call_site="cs", outcome="error")
    assert sample == _forge_quota.QuotaSample(
        resource="graphql", call_site="cs", limit=None, cost=None,
        remaining=None, reset_at=None, outcome="error",
    )


def test_parse_graphql_rate_limit_happy_path() -> None:
    body = json.dumps({"data": {"rateLimit": {
        "limit": 5000, "cost": 1, "remaining": 4999, "resetAt": "2026-08-19T15:04:05Z",
    }}})
    sample = _forge_quota.parse_graphql_rate_limit(body, call_site="cs")
    assert sample.limit == 5000
    assert sample.cost == 1
    assert sample.remaining == 4999
    expected = datetime(2026, 8, 19, 15, 4, 5, tzinfo=UTC).timestamp()
    assert sample.reset_at == pytest.approx(expected)
    assert sample.outcome == "ok"


def test_parse_graphql_rate_limit_non_string_reset_at_is_none() -> None:
    body = json.dumps({"data": {"rateLimit": {
        "limit": 5000, "cost": 1, "remaining": 4999, "resetAt": None,
    }}})
    sample = _forge_quota.parse_graphql_rate_limit(body, call_site="cs")
    assert sample.reset_at is None


def test_parse_graphql_rate_limit_unparseable_reset_at_string_is_none() -> None:
    body = json.dumps({"data": {"rateLimit": {
        "limit": 5000, "cost": 1, "remaining": 4999, "resetAt": "not-a-date",
    }}})
    sample = _forge_quota.parse_graphql_rate_limit(body, call_site="cs")
    assert sample.reset_at is None


def test_parse_graphql_rate_limit_non_int_fields_fall_back_to_none() -> None:
    body = json.dumps({"data": {"rateLimit": {
        "limit": "not-int", "cost": None, "remaining": 4999.5, "resetAt": None,
    }}})
    sample = _forge_quota.parse_graphql_rate_limit(body, call_site="cs")
    assert sample.limit is None
    assert sample.cost is None
    assert sample.remaining is None

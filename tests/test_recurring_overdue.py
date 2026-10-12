from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from alan import recurring_status


def test_cron_interval_seconds_wrong_field_count() -> None:
    assert recurring_status.cron_interval_seconds("* * *") is None


def test_cron_interval_seconds_rejects_non_wildcard_day_of_month() -> None:
    assert recurring_status.cron_interval_seconds("0 2 15 * *") is None


def test_cron_interval_seconds_rejects_non_wildcard_month() -> None:
    assert recurring_status.cron_interval_seconds("0 2 * 6 *") is None


def test_cron_interval_seconds_minute_step() -> None:
    assert recurring_status.cron_interval_seconds("*/30 * * * *") == pytest.approx(1800.0)


def test_cron_interval_seconds_minute_step_zero_is_rejected() -> None:
    assert recurring_status.cron_interval_seconds("*/0 * * * *") is None


def test_cron_interval_seconds_minute_step_non_numeric_is_rejected() -> None:
    assert recurring_status.cron_interval_seconds("*/x * * * *") is None


def test_cron_interval_seconds_minute_step_requires_wildcard_hour() -> None:
    assert recurring_status.cron_interval_seconds("*/30 5 * * *") is None


def test_cron_interval_seconds_hour_step() -> None:
    assert recurring_status.cron_interval_seconds("0 */4 * * *") == pytest.approx(4 * 3600.0)


def test_cron_interval_seconds_hour_step_requires_fixed_minute() -> None:
    assert recurring_status.cron_interval_seconds("* */4 * * *") is None


def test_cron_interval_seconds_hour_step_requires_wildcard_dow() -> None:
    assert recurring_status.cron_interval_seconds("0 */4 * * 1") is None


def test_cron_interval_seconds_daily() -> None:
    assert recurring_status.cron_interval_seconds("0 2 * * *") == pytest.approx(86400.0)


def test_cron_interval_seconds_weekly() -> None:
    assert recurring_status.cron_interval_seconds("0 2 * * 1") == pytest.approx(604800.0)


def test_cron_interval_seconds_unrecognized_named_day_of_week() -> None:
    assert recurring_status.cron_interval_seconds("0 2 * * MON") is None


def test_cron_interval_seconds_unrecognized_non_fixed_hour_and_minute() -> None:
    assert recurring_status.cron_interval_seconds("*,15 2 * * *") is None


_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compute_overdue_manual_cadence_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="manual", detail="x")
    overdue = recurring_status.compute_overdue(cadence, "2025-12-01T00:00:00+00:00", now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_rides_on_cadence_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="rides_on", rides_on="x", detail="x")
    overdue = recurring_status.compute_overdue(cadence, None, now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_unparseable_cadence_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="cron", interval_seconds=None, detail="x")
    overdue = recurring_status.compute_overdue(cadence, "2025-12-01T00:00:00+00:00", now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_no_last_run_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    overdue = recurring_status.compute_overdue(cadence, None, now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_unparseable_timestamp_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    overdue = recurring_status.compute_overdue(cadence, "not-a-timestamp", now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_naive_timestamp_is_unknown() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    overdue = recurring_status.compute_overdue(cadence, "2025-12-31T23:00:00", now=_NOW)
    assert overdue.state == "unknown"


def test_compute_overdue_on_time_within_grace() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    last_run_at = (_NOW - timedelta(seconds=1800)).isoformat()
    overdue = recurring_status.compute_overdue(cadence, last_run_at, now=_NOW)
    assert overdue.state == "on_time"


def test_compute_overdue_overdue_past_grace() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    last_run_at = (_NOW - timedelta(seconds=1800 * 2)).isoformat()
    overdue = recurring_status.compute_overdue(cadence, last_run_at, now=_NOW)
    assert overdue.state == "overdue"
    assert "last ran" in overdue.detail


def test_compute_overdue_on_time_for_a_run_started_inside_the_window_while_in_progress() -> None:
    cadence = recurring_status.Cadence(kind="interval_seconds", interval_seconds=1800.0, detail="x")
    started_at = (_NOW - timedelta(seconds=60)).isoformat()
    assert (  # noqa: SLF001
        recurring_status._forge_run_state("in_progress", None, confirmed_absent=False)
        == "in_progress"
    )
    overdue = recurring_status.compute_overdue(cadence, started_at, now=_NOW)
    assert overdue.state == "on_time"

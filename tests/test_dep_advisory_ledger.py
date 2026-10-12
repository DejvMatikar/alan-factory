from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dep_sweep import Vulnerability
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import dep_advisory
from alan.teams._pr_state_paths import _engine_state_dir  # noqa: SLF001
from alan.teams.dep_advisory import (
    COVERED_WINDOW_S,
    DEFER_WINDOW_S,
    Ledger,
    LedgerEntry,
    LedgerKind,
    fix_in_flight,
    is_covered,
    load_ledger,
    save_ledger,
)
from alan.workspace import Status, Workspace
from tests.dep_advisory_support import isolate_state

_FIXABLE = Vulnerability("A-1", "urllib3", "2.7.0", ("2.8.0",))
_UNFIXABLE = Vulnerability("A-1", "urllib3", "2.7.0", ())


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_state(monkeypatch, tmp_path)


def _ws(ws_root: Path, status: Status = Status.ACTIVE) -> Workspace:
    ws = workspace_mod.create("sec/urllib3: A-1", "d", team="dependency_sweep", root=ws_root)
    return ws if status == Status.ACTIVE else workspace_mod.transition(ws, status)


def _entry(ws: Workspace, kind: LedgerKind = "fix", *, age_s: float = 0.0) -> LedgerEntry:
    return LedgerEntry(
        advisory_id="A-1",
        task_id=ws.task_id,
        kind=kind,
        recorded_at=datetime.now(UTC).timestamp() - age_s,
    )


def _covered(
    entry: LedgerEntry,
    ws_root: Path,
    vulns: list[Vulnerability] | None = None,
    *,
    later: timedelta = timedelta(0),
) -> bool:
    return is_covered(entry, ws_root, datetime.now(UTC) + later, vulns or [_FIXABLE])


def test_a_missing_ledger_loads_empty(tmp_path: Path) -> None:
    assert load_ledger(DEFAULT_FORGE_TARGET) == Ledger()


def test_a_saved_ledger_round_trips() -> None:
    ledger = Ledger(entries={"A-1": LedgerEntry(
        advisory_id="A-1", task_id="t1", kind="fix", recorded_at=12.5,
    )})

    save_ledger(DEFAULT_FORGE_TARGET, ledger)

    assert load_ledger(DEFAULT_FORGE_TARGET) == ledger


def test_saving_creates_the_per_engine_state_directory() -> None:
    save_ledger(DEFAULT_FORGE_TARGET, Ledger())

    assert (_engine_state_dir(DEFAULT_FORGE_TARGET) / "dep_advisory_ledger.json").is_file()


@pytest.mark.parametrize("text", ["not json", "[]", '{"entries": {"A": {"advisory_id": "A"}}}'])
def test_a_corrupt_ledger_loads_empty_and_heals_on_the_next_save(
    text: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = _engine_state_dir(DEFAULT_FORGE_TARGET) / "dep_advisory_ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    assert load_ledger(DEFAULT_FORGE_TARGET) == Ledger()
    assert "corrupt" in caplog.text

    save_ledger(DEFAULT_FORGE_TARGET, Ledger())
    assert load_ledger(DEFAULT_FORGE_TARGET) == Ledger()


@pytest.mark.parametrize(
    "status",
    [
        Status.ACTIVE,
        Status.AWAITING_INPUT,
        Status.ESCALATED,
        Status.FAILED,
        Status.PAUSED,
        Status.PARTIAL,
    ],
)
def test_an_unfinished_fix_workspace_covers_the_advisory_however_long_ago(
    ws_root: Path, status: Status
) -> None:
    ws = _ws(ws_root, status)

    assert _covered(_entry(ws, age_s=30 * 24 * 3600), ws_root, later=timedelta(days=30))


def test_a_merged_fix_covers_the_advisory_inside_the_window(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.COMPLETED)

    assert _covered(_entry(ws), ws_root, later=timedelta(seconds=COVERED_WINDOW_S - 60))


def test_a_merged_fix_stops_covering_the_advisory_after_the_window(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.COMPLETED)

    assert not _covered(_entry(ws), ws_root, later=timedelta(seconds=COVERED_WINDOW_S + 60))


def test_an_archived_fix_workspace_does_not_cover(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.ARCHIVED)

    assert not _covered(_entry(ws), ws_root)


def test_a_deleted_workspace_does_not_cover(ws_root: Path) -> None:
    ws = _ws(ws_root)
    entry = _entry(ws)
    workspace_mod.delete(ws.task_id, root=ws_root)

    assert not _covered(entry, ws_root)


def test_a_posture_workspace_covers_while_it_is_unfinished(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.ESCALATED)

    assert _covered(_entry(ws, "posture"), ws_root)


def test_a_no_fix_entry_covers_while_the_advisory_still_has_no_fix(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.ESCALATED)

    assert _covered(_entry(ws, "no_fix"), ws_root, [_UNFIXABLE])


def test_a_no_fix_entry_stops_covering_once_the_advisory_has_become_fixable(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, Status.ESCALATED)

    assert not _covered(_entry(ws, "no_fix"), ws_root, [_FIXABLE])


def test_a_no_fix_entry_for_an_advisory_missing_from_the_audit_still_covers(
    ws_root: Path,
) -> None:
    ws = _ws(ws_root, Status.ESCALATED)

    assert _covered(_entry(ws, "no_fix"), ws_root, [Vulnerability("B-2", "x", "1", ("2",))])


def test_a_no_fix_entry_whose_workspace_was_archived_does_not_cover(ws_root: Path) -> None:
    ws = _ws(ws_root, Status.ARCHIVED)

    assert not _covered(_entry(ws, "no_fix"), ws_root, [_UNFIXABLE])


def _save(*entries: LedgerEntry) -> None:
    save_ledger(DEFAULT_FORGE_TARGET, Ledger(entries={e.task_id: e for e in entries}))


def test_an_active_young_fix_workspace_is_in_flight(ws_root: Path) -> None:
    _save(_entry(_ws(ws_root)))

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is True


def test_an_active_fix_workspace_older_than_the_defer_window_is_not_in_flight(
    ws_root: Path,
) -> None:
    _save(_entry(_ws(ws_root), age_s=DEFER_WINDOW_S + 60))

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is False


def test_the_defer_window_is_measured_against_the_supplied_clock(ws_root: Path) -> None:
    _save(_entry(_ws(ws_root)))
    later = datetime.now(UTC) + timedelta(seconds=DEFER_WINDOW_S + 60)

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root, now=later) is False


@pytest.mark.parametrize("status", [Status.ESCALATED, Status.AWAITING_INPUT, Status.COMPLETED])
def test_a_fix_workspace_that_is_not_active_is_not_in_flight(
    ws_root: Path, status: Status
) -> None:
    _save(_entry(_ws(ws_root, status)))

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is False


def test_only_fix_kind_entries_count_as_in_flight(ws_root: Path) -> None:
    _save(_entry(_ws(ws_root), "no_fix"))

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is False


def test_a_deleted_fix_workspace_is_not_in_flight(ws_root: Path) -> None:
    ws = _ws(ws_root)
    _save(_entry(ws))
    workspace_mod.delete(ws.task_id, root=ws_root)

    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is False


def test_an_empty_ledger_has_nothing_in_flight(ws_root: Path) -> None:
    assert fix_in_flight(DEFAULT_FORGE_TARGET, ws_root) is False


def test_the_windows_are_six_hours_and_a_day() -> None:
    assert dep_advisory.COVERED_WINDOW_S == 6 * 3600
    assert dep_advisory.DEFER_WINDOW_S == 24 * 3600

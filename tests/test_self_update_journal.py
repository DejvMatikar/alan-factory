from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.self_update import _journal


@pytest.fixture(autouse=True)
def _redirect_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def sample() -> _journal.Journal:
    return _journal.Journal(_journal.Phase.APPLYING, "a" * 40, "b" * 40, "dist.a", True)


def test_read_returns_none_when_no_journal_exists() -> None:
    assert _journal.read() is None


def test_write_then_read_round_trips() -> None:
    journal = sample()
    _journal.write(journal)
    assert _journal.read() == journal


def test_with_phase_changes_only_the_phase() -> None:
    journal = sample()
    moved = journal.with_phase(_journal.Phase.ROLLED_BACK)
    assert moved.phase is _journal.Phase.ROLLED_BACK
    assert moved.target_sha == journal.target_sha
    assert moved.previous_sha == journal.previous_sha
    assert moved.previous_slot == journal.previous_slot
    assert moved.restarted == journal.restarted


def test_as_json_has_the_five_expected_keys() -> None:
    assert set(sample().as_json()) == {
        "phase", "target_sha", "previous_sha", "previous_slot", "restarted",
    }


def test_clear_removes_an_existing_journal() -> None:
    _journal.write(sample())
    _journal.clear()
    assert _journal.read() is None


def test_clear_is_a_no_op_when_nothing_exists() -> None:
    _journal.clear()
    assert _journal.read() is None


def test_a_previous_slot_of_none_round_trips() -> None:
    journal = _journal.Journal(_journal.Phase.LAST_GOOD, "a" * 40, "b" * 40, None, False)
    _journal.write(journal)
    assert _journal.read() == journal


def test_read_returns_none_for_invalid_json() -> None:
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert _journal.read() is None


def test_read_returns_none_when_the_payload_is_not_an_object() -> None:
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert _journal.read() is None


def test_read_returns_none_when_a_required_key_is_missing() -> None:
    payload = sample().as_json()
    del payload["restarted"]
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _journal.read() is None


def test_read_returns_none_when_previous_slot_is_the_wrong_type() -> None:
    payload = sample().as_json()
    payload["previous_slot"] = 7
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _journal.read() is None


def test_read_returns_none_when_the_phase_value_is_unrecognised() -> None:
    payload = sample().as_json()
    payload["phase"] = "not_a_real_phase"
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _journal.read() is None


def test_read_returns_none_when_restarted_is_a_string_instead_of_a_boolean() -> None:
    payload = sample().as_json()
    payload["restarted"] = "false"
    path = _journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _journal.read() is None


def test_leftover_phases_excludes_the_terminal_success_states() -> None:
    assert _journal.Phase.LAST_GOOD not in _journal.LEFTOVER_PHASES
    assert _journal.Phase.ROLLED_BACK not in _journal.LEFTOVER_PHASES
    assert _journal.Phase.APPLYING in _journal.LEFTOVER_PHASES

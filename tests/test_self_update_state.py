from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from alan.self_update import Reason, StatusRecord, Verdict, _journal, status_path
from alan.self_update._state import load_state
from tests.self_update_fixtures import redirect_env

_SHA_A = "a" * 40
_SHA_B = "b" * 40
_CHECKED_AT = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _isolated_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


def _write_status(record: StatusRecord) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.as_json()), encoding="utf-8")


def test_no_status_and_no_journal_yields_a_fully_empty_state() -> None:
    state = load_state()
    assert state.status is None
    assert state.journal is None


def test_a_valid_blocked_status_loads_with_no_journal_present() -> None:
    _write_status(StatusRecord(Verdict.BLOCKED, Reason.DIRTY_TREE, None, _SHA_A, _CHECKED_AT))
    state = load_state()
    assert state.status is not None
    assert state.status.verdict is Verdict.BLOCKED
    assert state.status.reason is Reason.DIRTY_TREE
    assert state.journal is None


def test_malformed_status_json_yields_no_status() -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert load_state().status is None


def test_status_missing_a_required_key_yields_no_status() -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verdict": "blocked"}), encoding="utf-8")
    assert load_state().status is None


def test_status_with_a_reason_not_allowed_for_its_verdict_yields_no_status() -> None:
    payload = StatusRecord(
        Verdict.BLOCKED, Reason.DIRTY_TREE, None, _SHA_A, _CHECKED_AT
    ).as_json()
    payload["reason"] = Reason.ALREADY_CURRENT.value
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_state().status is None


def test_status_with_a_malformed_apply_field_yields_no_status() -> None:
    payload = StatusRecord(
        Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, _SHA_A, _SHA_A, _CHECKED_AT
    ).as_json()
    payload["target_sha"] = "not-a-sha"
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_state().status is None


def test_a_non_dict_status_payload_yields_no_status() -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert load_state().status is None


def test_a_non_string_checked_at_yields_no_status() -> None:
    payload = cast(
        "dict[str, object]",
        StatusRecord(
            Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, _SHA_A, _SHA_A, _CHECKED_AT
        ).as_json(),
    )
    payload["checked_at"] = 123
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_state().status is None


def test_a_non_string_optional_field_yields_no_status_instead_of_being_dropped() -> None:
    payload = cast(
        "dict[str, object]",
        StatusRecord(
            Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, _SHA_A, _SHA_A, _CHECKED_AT
        ).as_json(),
    )
    payload["target_sha"] = 7
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_state().status is None


def test_a_valid_journal_loads_alongside_the_status() -> None:
    _write_status(
        StatusRecord(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, _SHA_A, _SHA_A, _CHECKED_AT)
    )
    _journal.write(_journal.Journal(_journal.Phase.ROLLED_BACK, _SHA_A, _SHA_B, None, True))
    state = load_state()
    assert state.status is not None
    assert state.journal is not None
    assert state.journal.phase is _journal.Phase.ROLLED_BACK


def test_a_malformed_journal_yields_no_journal_but_keeps_the_status() -> None:
    _write_status(
        StatusRecord(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, _SHA_A, _SHA_A, _CHECKED_AT)
    )
    journal_path = _journal.journal_path()
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text("not json", encoding="utf-8")
    state = load_state()
    assert state.status is not None
    assert state.journal is None


def test_load_state_never_writes_to_either_file() -> None:
    load_state()
    assert not status_path().exists()
    assert not _journal.journal_path().exists()

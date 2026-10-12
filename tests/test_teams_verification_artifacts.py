from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.teams._verdict import CriticalCountReport
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    count_record_path,
    read_count_record,
    read_review_artifact,
    review_artifact_path,
    reviewer_count_var,
)
from alan.workspace import create


@pytest.fixture
def ambient_counts() -> Iterator[CriticalCountReport]:
    counts = CriticalCountReport(reviewer=0, judge_reported=True)
    token = reviewer_count_var.set(counts)
    try:
        yield counts
    finally:
        reviewer_count_var.reset(token)


def test_review_artifact_path_matches_write_location(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    ref = _write_review_artifact(ws, "inv_001", "VERDICT: APPROVE")
    assert Path(ref.path) == review_artifact_path(ws, "inv_001")


def test_read_review_artifact_returns_written_content(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    _write_review_artifact(ws, "inv_002", "VERDICT: REQUEST_CHANGES")
    assert read_review_artifact(ws, "inv_002") == "VERDICT: REQUEST_CHANGES"


def test_read_review_artifact_missing_file_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    assert read_review_artifact(ws, "inv_missing") is None


def test_read_review_artifact_unreadable_bytes_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = review_artifact_path(ws, "inv_bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00\x01")
    assert read_review_artifact(ws, "inv_bad") is None


def test_write_review_artifact_writes_count_record_under_same_inv_id(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    counts = CriticalCountReport(reviewer=0, judge_reported=True)
    _write_review_artifact(ws, "inv_003", "VERDICT: APPROVE", counts=counts)
    assert count_record_path(ws, "inv_003").exists()
    assert read_count_record(ws, "inv_003") == counts


def test_write_review_artifact_writes_no_record_when_counts_omitted(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    _write_review_artifact(ws, "inv_004", "VERDICT: APPROVE")
    assert not count_record_path(ws, "inv_004").exists()
    assert read_count_record(ws, "inv_004") is None


def test_write_review_artifact_writes_record_from_ambient_var_when_counts_omitted(
    ws_root: Path, ambient_counts: CriticalCountReport,
) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    _write_review_artifact(ws, "inv_011", "VERDICT: REQUEST_CHANGES")
    assert read_count_record(ws, "inv_011") == ambient_counts


def test_write_review_artifact_explicit_counts_win_over_differing_ambient_var(
    ws_root: Path, ambient_counts: CriticalCountReport,
) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    explicit = CriticalCountReport(reviewer=4, judge_reported=False)
    assert explicit != ambient_counts
    _write_review_artifact(ws, "inv_012", "VERDICT: REQUEST_CHANGES", counts=explicit)
    assert read_count_record(ws, "inv_012") == explicit


def test_write_review_artifact_writes_no_record_after_ambient_var_is_reset(
    ws_root: Path,
) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    token = reviewer_count_var.set(CriticalCountReport(reviewer=0, judge_reported=True))
    reviewer_count_var.reset(token)
    _write_review_artifact(ws, "inv_013", "VERDICT: APPROVE")
    assert read_count_record(ws, "inv_013") is None


def test_read_count_record_missing_file_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    assert read_count_record(ws, "inv_missing") is None


def test_read_count_record_malformed_json_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_bad_json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{", encoding="utf-8")
    assert read_count_record(ws, "inv_bad_json") is None


def test_read_count_record_non_object_json_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_not_object")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    assert read_count_record(ws, "inv_not_object") is None


def test_read_count_record_inv_id_mismatch_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    counts = CriticalCountReport(reviewer=1, judge_reported=True)
    _write_review_artifact(ws, "inv_005", "VERDICT: REQUEST_CHANGES", counts=counts)
    path = count_record_path(ws, "inv_005")
    path.write_text(
        '{"inv_id": "inv_other", "reviewer": 1, "judge_reported": true}', encoding="utf-8"
    )
    assert read_count_record(ws, "inv_005") is None


def test_read_count_record_non_boolean_judge_reported_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_006")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"inv_id": "inv_006", "reviewer": null, "judge_reported": "false"}', encoding="utf-8"
    )
    assert read_count_record(ws, "inv_006") is None


def test_read_count_record_non_numeric_reviewer_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_007")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"inv_id": "inv_007", "reviewer": "5", "judge_reported": true}', encoding="utf-8"
    )
    assert read_count_record(ws, "inv_007") is None


def test_read_count_record_negative_reviewer_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_008")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"inv_id": "inv_008", "reviewer": -1, "judge_reported": true}', encoding="utf-8"
    )
    assert read_count_record(ws, "inv_008") is None


def test_read_count_record_boolean_reviewer_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_009")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"inv_id": "inv_009", "reviewer": true, "judge_reported": true}', encoding="utf-8"
    )
    assert read_count_record(ws, "inv_009") is None


def test_read_count_record_missing_judge_reported_returns_none(ws_root: Path) -> None:
    ws = create("Verification Artifacts", "d", root=ws_root)
    path = count_record_path(ws, "inv_010")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"inv_id": "inv_010", "reviewer": 0}', encoding="utf-8")
    assert read_count_record(ws, "inv_010") is None

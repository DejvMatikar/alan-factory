from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_audit_log, _pr_waiver_dismissal
from tests import review_forge_fake as forge_fake
from tests._pr_branch_support import set_verifier_transport_token


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    set_verifier_transport_token(monkeypatch)


def test_dismiss_for_waiver_withheld_floor_makes_no_forge_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=1, login="alan-verifier", state="CHANGES_REQUESTED")],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1",
        floor_reason="reviewer_nonzero",
    )
    assert result.outcome == "withheld_floor"
    assert result.review_ids == ()
    assert result.other_author_blocking is False
    assert fake.attempted_dismiss_ids == []


def test_dismiss_for_waiver_withheld_floor_on_missing_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake()
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1",
        floor_reason="count_not_reported",
    )
    assert result.outcome == "withheld_floor"
    assert fake.attempted_dismiss_ids == []


def test_dismiss_for_waiver_dismisses_current_head_verifier_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=101, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "dismissed"
    assert result.review_ids == (101,)
    assert result.other_author_blocking is False
    assert fake.attempted_dismiss_ids == [101]
    assert fake.reviews[0].state == "DISMISSED"


def test_dismiss_for_waiver_marks_other_author_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=201, login="alan-verifier", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=202, login="a-human", state="CHANGES_REQUESTED"),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "dismissed"
    assert result.other_author_blocking is True


def test_dismiss_for_waiver_other_author_approved_not_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=301, login="alan-verifier", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=302, login="a-human", state="APPROVED"),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.other_author_blocking is False


def test_dismiss_for_waiver_latest_other_author_review_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=401, login="alan-verifier", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=402, login="a-human", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=403, login="a-human", state="APPROVED"),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.other_author_blocking is False


def test_dismiss_for_waiver_ignores_commented_and_pending_other_author_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=501, login="alan-verifier", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=502, login="a-human", state="COMMENTED"),
            forge_fake.FakeReview(id=503, login="another-human", state="PENDING"),
        ],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.other_author_blocking is False


def test_dismiss_for_waiver_none_found_when_no_verifier_changes_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=601, login="alan-verifier", state="APPROVED")],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "none_found"
    assert result.review_ids == ()
    assert fake.attempted_dismiss_ids == []


def test_dismiss_for_waiver_ignores_non_int_review_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = json.dumps({
        "id": "not-an-int", "state": "CHANGES_REQUESTED",
        "user": {"login": "alan-verifier"},
    })
    fake = forge_fake.ReviewForgeFake(raw_stream=line)
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "none_found"
    assert result.review_ids == ()


def test_dismiss_for_waiver_ignores_other_authors_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=701, login="a-human", state="CHANGES_REQUESTED")],
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "none_found"
    assert fake.attempted_dismiss_ids == []


def test_dismiss_for_waiver_failed_when_listing_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(stream_available=False)
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "failed"


def test_dismiss_for_waiver_failed_when_dismiss_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=801, login="alan-verifier", state="CHANGES_REQUESTED")],
        dismiss_rc=1,
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "failed"
    assert result.review_ids == (801,)
    assert fake.attempted_dismiss_ids == [801]


def test_dismiss_for_waiver_attempts_every_id_even_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(id=901, login="alan-verifier", state="CHANGES_REQUESTED"),
            forge_fake.FakeReview(id=902, login="alan-verifier", state="CHANGES_REQUESTED"),
        ],
        dismiss_rc=1,
    )
    forge_fake.install(fake, monkeypatch)
    result = _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="q1", floor_reason=None,
    )
    assert result.outcome == "failed"
    assert fake.attempted_dismiss_ids == [901, 902]


def test_dismiss_for_waiver_dismissal_message_names_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = forge_fake.ReviewForgeFake(
        reviews=[forge_fake.FakeReview(id=1001, login="alan-verifier", state="CHANGES_REQUESTED")],
    )
    forge_fake.install(fake, monkeypatch)
    _pr_waiver_dismissal.dismiss_for_waiver(
        42, forge_target=DEFAULT_FORGE_TARGET, question_id="clause-stall-abc",
        floor_reason=None,
    )
    assert any("clause-stall-abc" in m for m in fake.dismiss_messages)


def test_record_waiver_dismissal_writes_audit_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    result = _pr_waiver_dismissal.WaiverDismissal("dismissed", (55,), False)
    _pr_waiver_dismissal.record_waiver_dismissal(
        task_id="task-x", pr_number=42, question_id="clause-stall-abc",
        invocation_id="inv_001", reviewer_count=0, floor_reason=None, result=result,
    )
    log = tmp_path / "logs" / _pr_audit_log.AUDIT_FILE
    entries = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    row = next(e for e in entries if e["action"] == _pr_waiver_dismissal.AUDIT_ACTION)
    assert row["task_id"] == "task-x"
    assert row["pr_number"] == 42
    assert row["question_id"] == "clause-stall-abc"
    assert row["invocation_id"] == "inv_001"
    assert row["outcome"] == "dismissed"
    assert row["review_ids"] == [55]
    assert row["reviewer_count"] == 0
    assert row["floor_reason"] is None
    assert "critical_count" not in row
    assert row["other_author_blocking"] is False

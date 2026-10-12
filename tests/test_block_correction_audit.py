from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.block_correction import Attempt, CorrectionOutcome, run_correction
from alan.teams import _block_correction_audit as bca


@pytest.fixture(autouse=True)
def _isolate_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _read_rows() -> list[dict[str, object]]:
    path = bca.audit_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_record_writes_one_row_with_all_fields() -> None:
    bca.record(
        site="decisions.yaml",
        block="decisions.yaml",
        role="judge",
        outcome="clean",
        attempts=1,
        failure_kind=None,
        task_id="t1",
    )
    rows = _read_rows()
    assert len(rows) == 1
    assert rows[0]["site"] == "decisions.yaml"
    assert rows[0]["block"] == "decisions.yaml"
    assert rows[0]["role"] == "judge"
    assert rows[0]["outcome"] == "clean"
    assert rows[0]["attempts"] == 1
    assert rows[0]["failure_kind"] is None
    assert rows[0]["task_id"] == "t1"


def test_record_from_outcome_derives_attempts_and_failure_kind() -> None:
    outcome: CorrectionOutcome[str] = CorrectionOutcome(
        outcome="exhausted",
        value=None,
        attempts=(
            Attempt(1, "a", "err1", "validation_error"),
            Attempt(2, "b", "err2", "parse_error"),
        ),
        final_error="err2",
    )
    bca.record_from_outcome(
        site="decomposition.yaml", block="decomposition.yaml", role="decomposer",
        outcome=outcome, task_id="t2",
    )
    rows = _read_rows()
    assert rows[0]["outcome"] == "exhausted"
    assert rows[0]["attempts"] == 2
    assert rows[0]["failure_kind"] == "parse_error"


def test_record_from_outcome_outcome_override_wins() -> None:
    outcome: CorrectionOutcome[str] = CorrectionOutcome(
        outcome="clean", value="x", attempts=(Attempt(1, "x", None, None),), final_error=None,
    )
    bca.record_from_outcome(
        site="decisions.yaml", block="decisions.yaml", role="judge",
        outcome=outcome, task_id="t3", outcome_override="repaired",
    )
    rows = _read_rows()
    assert rows[0]["outcome"] == "repaired"


def test_record_from_outcome_no_failure_kind_when_all_attempts_succeed() -> None:
    outcome: CorrectionOutcome[str] = CorrectionOutcome(
        outcome="clean", value="x", attempts=(Attempt(1, "x", None, None),), final_error=None,
    )
    bca.record_from_outcome(
        site="decisions.yaml", block="decisions.yaml", role="judge", outcome=outcome,
    )
    rows = _read_rows()
    assert rows[0]["failure_kind"] is None
    assert rows[0]["task_id"] is None


def test_record_unreachable_attempt_returns_value_on_success() -> None:
    result = bca.record_unreachable_attempt(
        site="RCA_DRAFT_YAML", role="investigator", validate=lambda t: t.upper(), text="ok",
    )
    assert result == "OK"
    rows = _read_rows()
    assert rows[0]["outcome"] == "clean"


def test_record_unreachable_attempt_returns_none_and_records_unreachable_author() -> None:
    def _fail(text: str) -> str:
        raise ValueError("bad")

    result = bca.record_unreachable_attempt(
        site="scenario_rebind.yaml", role="implementation_fix", validate=_fail, text="bad text",
    )
    assert result is None
    rows = _read_rows()
    assert rows[0]["outcome"] == "unreachable_author"
    assert rows[0]["attempts"] == 1


def test_record_unreachable_attempt_uses_run_correction_with_no_reask() -> None:
    calls = 0

    def _validate(text: str) -> str:
        nonlocal calls
        calls += 1
        raise ValueError("bad")

    bca.record_unreachable_attempt(
        site="INLINE_FINDINGS_YAML", role="verification", validate=_validate, text="x",
    )
    assert calls == 1


def test_run_correction_reexported_behaviour_matches_direct_import() -> None:
    outcome = run_correction("x", validate=lambda t: t, re_ask=None, max_attempts=1)
    assert outcome.outcome == "clean"

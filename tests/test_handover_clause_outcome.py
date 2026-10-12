from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from alan import handover as handover_mod
from alan.handover import (
    SCHEMA_VERSION,
    ClauseOutcome,
    Handover,
    load_handover,
    save_handover,
)
from alan.workspace import create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_handover(task_id: str = "t1", **overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "forward",
        "task_id": task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "verification done",
    }
    base.update(overrides)
    return Handover.model_validate(base)


def test_clause_outcome_valid_construction() -> None:
    outcome = ClauseOutcome(
        task_id="t1",
        actionable_ids=("req-abc",),
        clause_texts={"req-abc": "the endpoint must be idempotent"},
        evidence={"req-abc": "POST /x is not idempotent"},
        judge_rationale="the diff never guards against a repeat call",
    )
    assert outcome.task_id == "t1"
    assert outcome.actionable_ids == ("req-abc",)
    assert outcome.clause_texts == {"req-abc": "the endpoint must be idempotent"}
    assert outcome.evidence == {"req-abc": "POST /x is not idempotent"}
    assert outcome.judge_rationale == "the diff never guards against a repeat call"


def test_clause_outcome_judge_rationale_defaults_empty() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
    )
    assert outcome.judge_rationale == ""


def test_clause_outcome_judge_rationale_rejects_over_2000_chars() -> None:
    with pytest.raises(ValidationError):
        ClauseOutcome(
            task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
            judge_rationale="x" * 2001,
        )


def test_clause_outcome_judge_rationale_accepts_exactly_2000_chars() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
        judge_rationale="x" * 2000,
    )
    assert len(outcome.judge_rationale) == 2000


def test_clause_outcome_operator_only_ids_defaults_empty() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
    )
    assert outcome.operator_only_ids == ()


def test_clause_outcome_operator_only_ids_populated() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
        operator_only_ids=("req-op",),
    )
    assert outcome.operator_only_ids == ("req-op",)


def test_clause_outcome_unactionable_ids_defaults_empty() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
    )
    assert outcome.unactionable_ids == ()


def test_clause_outcome_unactionable_ids_populated() -> None:
    outcome = ClauseOutcome(
        task_id="t1", actionable_ids=(), clause_texts={}, evidence={},
        unactionable_ids=("req-pr",),
    )
    assert outcome.unactionable_ids == ("req-pr",)


def test_handover_clause_outcome_unactionable_ids_round_trip(ws_root: Path) -> None:
    ws = create("clause-outcome-unactionable-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        clause_outcome=ClauseOutcome(
            task_id=ws.task_id,
            actionable_ids=(),
            clause_texts={"req-pr": "PR state must be draft"},
            evidence={"req-pr": "PR_STATE: isDraft is false"},
            unactionable_ids=("req-pr",),
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.clause_outcome is not None
    assert loaded.clause_outcome.unactionable_ids == ("req-pr",)


def test_handover_v123_document_without_unactionable_ids_still_loads(ws_root: Path) -> None:
    ws = create("clause-outcome-pre-unactionable", "d", root=ws_root)
    payload = {
        "schema_version": "1.23",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "pre unactionable_ids payload",
        "clause_outcome": {
            "task_id": ws.task_id, "actionable_ids": ["req-a"],
            "clause_texts": {"req-a": "x"}, "evidence": {"req-a": "y"},
        },
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.clause_outcome is not None
    assert loaded.clause_outcome.unactionable_ids == ()


def test_handover_clause_outcome_defaults_none() -> None:
    assert _make_handover().clause_outcome is None


def test_handover_clause_outcome_round_trip(ws_root: Path) -> None:
    ws = create("clause-outcome-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        clause_outcome=ClauseOutcome(
            task_id=ws.task_id,
            actionable_ids=("req-abc",),
            clause_texts={"req-abc": "the endpoint must be idempotent"},
            evidence={"req-abc": "POST /x is not idempotent"},
            judge_rationale="the diff never guards against a repeat call",
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.clause_outcome is not None
    assert loaded.clause_outcome.task_id == ws.task_id
    assert loaded.clause_outcome.actionable_ids == ("req-abc",)
    assert loaded.clause_outcome.clause_texts == {
        "req-abc": "the endpoint must be idempotent"
    }
    assert loaded.clause_outcome.evidence == {"req-abc": "POST /x is not idempotent"}


def test_handover_clause_outcome_operator_only_ids_round_trip(ws_root: Path) -> None:
    ws = create("clause-outcome-op-rt", "d", root=ws_root)
    h = _make_handover(
        task_id=ws.task_id,
        clause_outcome=ClauseOutcome(
            task_id=ws.task_id,
            actionable_ids=(),
            clause_texts={"req-op": "needs a live measurement"},
            evidence={"req-op": "OPERATOR_ONLY: needs a live traffic measurement"},
            operator_only_ids=("req-op",),
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.clause_outcome is not None
    assert loaded.clause_outcome.operator_only_ids == ("req-op",)


def test_handover_v123_document_without_operator_only_ids_still_loads(ws_root: Path) -> None:
    ws = create("clause-outcome-pre-op", "d", root=ws_root)
    payload = {
        "schema_version": "1.23",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "pre operator_only_ids payload",
        "clause_outcome": {
            "task_id": ws.task_id, "actionable_ids": ["req-a"],
            "clause_texts": {"req-a": "x"}, "evidence": {"req-a": "y"},
        },
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.clause_outcome is not None
    assert loaded.clause_outcome.operator_only_ids == ()


def test_handover_v122_document_without_clause_outcome_still_loads(
    ws_root: Path,
) -> None:
    ws = create("v122-no-clause-outcome", "d", root=ws_root)
    payload = {
        "schema_version": "1.22",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "legacy v1.22 payload without clause_outcome",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.22"
    assert loaded.clause_outcome is None


def test_schema_version_bumped_to_123() -> None:
    assert SCHEMA_VERSION == "1.23"


def test_123_present_in_supported_schema_versions() -> None:
    assert "1.23" in handover_mod._SUPPORTED_SCHEMA_VERSIONS  # noqa: SLF001


def test_123_present_in_schema_version_literal() -> None:
    assert "1.23" in get_args(handover_mod._SchemaVersion)  # noqa: SLF001

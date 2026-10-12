from __future__ import annotations

import pytest
from pydantic import ValidationError

from alan.scope_audit import CounterCase, ScopeGapFinding


def test_scope_gap_finding_round_trip_minimal() -> None:
    finding = ScopeGapFinding(
        kind="sibling_code",
        summary="WorkspaceList.tsx shares the rename pattern",
        references=[],
        suggested_action="expand_scope",
    )
    dumped = finding.model_dump()
    restored = ScopeGapFinding.model_validate(dumped)
    assert restored == finding


def test_scope_gap_finding_round_trip_with_references() -> None:
    finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="PR #144 touches the same file",
        references=["https://github.com/x/y/pull/144", "alan/server.py"],
        suggested_action="needs_user_call",
    )
    restored = ScopeGapFinding.model_validate(finding.model_dump())
    assert restored.references == ["https://github.com/x/y/pull/144", "alan/server.py"]


def test_scope_gap_finding_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="other",  # type: ignore[arg-type]
            summary="x",
            references=[],
            suggested_action="expand_scope",
        )


def test_scope_gap_finding_rejects_unknown_suggested_action() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="sibling_code",
            summary="x",
            references=[],
            suggested_action="ignore",  # type: ignore[arg-type]
        )


def test_scope_gap_finding_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="sibling_code",
            summary="",
            references=[],
            suggested_action="expand_scope",
        )


def test_scope_gap_finding_rejects_overlong_summary() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="sibling_code",
            summary="x" * 2001,
            references=[],
            suggested_action="expand_scope",
        )


def test_scope_gap_finding_accepts_1500_char_summary() -> None:
    finding = ScopeGapFinding(
        kind="sibling_code",
        summary="x" * 1500,
        references=[],
        suggested_action="expand_scope",
    )
    assert len(finding.summary) == 1500


def test_scope_gap_finding_rejects_too_many_references() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="sibling_code",
            summary="x",
            references=[f"ref{i}" for i in range(11)],
            suggested_action="expand_scope",
        )


def test_scope_gap_finding_rejects_dod_untestable_kind() -> None:
    with pytest.raises(ValidationError):
        ScopeGapFinding(
            kind="dod_untestable",  # type: ignore[arg-type]
            summary="DoD has no testable success criterion",
            suggested_action="needs_user_call",
        )


def test_scope_gap_finding_default_references_empty() -> None:
    finding = ScopeGapFinding(
        kind="invariant_risk",
        summary="invariant may be violated",
        suggested_action="needs_user_call",
    )
    assert finding.references == []


def test_scope_gap_finding_accepts_anti_scope_kind() -> None:
    finding = ScopeGapFinding(
        kind="anti_scope",
        summary="Dispatch may violate project anti-scope: 'do not touch billing'",
        suggested_action="out_of_scope",
    )
    restored = ScopeGapFinding.model_validate(finding.model_dump())
    assert restored.kind == "anti_scope"
    assert restored.suggested_action == "out_of_scope"


def test_counter_case_round_trip() -> None:
    counter_case = CounterCase(
        against="This locks in scope before the shop has capacity to review it.",
        cheapest_80="Ship the read-only view first; defer the editable form.",
        cost_estimate="insufficient data",
    )
    restored = CounterCase.model_validate(counter_case.model_dump())
    assert restored == counter_case


def test_counter_case_rejects_empty_against() -> None:
    with pytest.raises(ValidationError):
        CounterCase(against="", cheapest_80="cut something", cost_estimate="insufficient data")


def test_counter_case_rejects_empty_cheapest_80() -> None:
    with pytest.raises(ValidationError):
        CounterCase(against="strongest argument", cheapest_80="", cost_estimate="insufficient data")


def test_counter_case_rejects_empty_cost_estimate() -> None:
    with pytest.raises(ValidationError):
        CounterCase(against="strongest argument", cheapest_80="cut something", cost_estimate="")


def test_counter_case_rejects_overlong_field() -> None:
    with pytest.raises(ValidationError):
        CounterCase(
            against="x" * 2001,
            cheapest_80="cut something",
            cost_estimate="insufficient data",
        )


def test_counter_case_accepts_2000_char_field() -> None:
    counter_case = CounterCase(
        against="x" * 2000,
        cheapest_80="cut something",
        cost_estimate="insufficient data",
    )
    assert len(counter_case.against) == 2000


def test_counter_case_cites_measured_shop_stats() -> None:
    counter_case = CounterCase(
        against="Fix-cycle rate is trending up; another change adds risk this sprint.",
        cheapest_80="Ship the smaller sibling change and defer the rest.",
        cost_estimate="First-pass approval rate is 62.0%; merge-gate recovery 80.0%.",
    )
    assert "62.0%" in counter_case.cost_estimate

from __future__ import annotations

import pytest

from alan.teams import _verdict as verd_mod
from alan.teams._verdict import (
    effective_critical_count,
    effective_verdict,
    parse_critical_count,
    parse_in_scope_critical_count,
)


def test_effective_verdict_approve_no_critical_returns_approve() -> None:
    assert effective_verdict("VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n") == "APPROVE"


def test_effective_verdict_approve_with_critical_downgrades() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 1\n"
    assert effective_verdict(judge) == "REQUEST_CHANGES"


def test_effective_verdict_request_changes_passes_through() -> None:
    assert effective_verdict("VERDICT: REQUEST_CHANGES\n") == "REQUEST_CHANGES"


def test_effective_verdict_missing_returns_none() -> None:
    assert effective_verdict("no verdict here") is None


def test_effective_verdict_downgrades_with_decorated_critical_count() -> None:
    judge = "VERDICT: APPROVE\n`DIFF_FINDINGS_CRITICAL: 2`\n"
    assert effective_verdict(judge) == "REQUEST_CHANGES"


def test_parse_in_scope_critical_count_present() -> None:
    assert parse_in_scope_critical_count(
        "DIFF_FINDINGS_CRITICAL: 2\nDIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    ) == 0


def test_parse_in_scope_critical_count_missing_returns_none() -> None:
    assert parse_in_scope_critical_count("DIFF_FINDINGS_CRITICAL: 2\n") is None


def test_parse_in_scope_does_not_match_the_raw_critical_line() -> None:
    assert parse_in_scope_critical_count("DIFF_FINDINGS_CRITICAL: 3\n") is None
    assert parse_critical_count(
        "DIFF_FINDINGS_CRITICAL: 3\nDIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    ) == 3


def test_effective_critical_count_prefers_in_scope() -> None:
    judge = "DIFF_FINDINGS_CRITICAL: 2\nDIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    assert effective_critical_count(judge) == 0


def test_effective_critical_count_falls_back_to_raw_when_absent() -> None:
    assert effective_critical_count("DIFF_FINDINGS_CRITICAL: 2\n") == 2


def test_effective_verdict_in_scope_zero_overrides_raw_criticals() -> None:
    judge = (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    assert effective_verdict(judge) == "APPROVE"


def test_effective_verdict_in_scope_nonzero_requests_changes() -> None:
    judge = (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 1\n"
        "VERDICT: APPROVE\n"
    )
    assert effective_verdict(judge) == "REQUEST_CHANGES"


def test_strip_in_scope_critical_line_removes_only_that_line() -> None:
    judge = (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    stripped = verd_mod._strip_in_scope_critical_line(judge)  # noqa: SLF001
    assert "DIFF_FINDINGS_CRITICAL_IN_SCOPE:" not in stripped
    assert "DIFF_FINDINGS_CRITICAL: 2" in stripped
    assert "VERDICT: APPROVE" in stripped


def test_strip_in_scope_critical_line_noop_when_absent() -> None:
    judge = "DIFF_FINDINGS_CRITICAL: 2\nVERDICT: APPROVE\n"
    assert verd_mod._strip_in_scope_critical_line(judge) == judge  # noqa: SLF001


def test_apply_reviewer_count_fallback_injects_when_missing() -> None:
    judge = "VERDICT: APPROVE\n"
    reviewer = "## Critical findings\n1. bug\n\nDIFF_FINDINGS_CRITICAL: 2\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert parse_critical_count(out) == 2
    assert "VERDICT: APPROVE" in out


def test_apply_reviewer_count_fallback_noop_when_present() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    reviewer = "DIFF_FINDINGS_CRITICAL: 5\n"
    assert verd_mod._apply_reviewer_count_fallback(judge, reviewer) == judge  # noqa: SLF001


def test_apply_reviewer_count_fallback_noop_when_present_in_verdict_section() -> None:
    judge = (
        "## Verdict\n\n"
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 0\n\n"
        "## Rationale\nLooks good.\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 5\n"
    assert verd_mod._apply_reviewer_count_fallback(judge, reviewer) == judge  # noqa: SLF001


def test_apply_reviewer_count_fallback_ignores_stray_mention_outside_verdict_section() -> None:
    judge = (
        "## Verdict\n\n"
        "VERDICT: APPROVE\n\n"
        "## Rationale\n"
        "Example of a well-formed line:\n"
        "`DIFF_FINDINGS_CRITICAL: 9`\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 3\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert parse_critical_count(out) == 3


def test_apply_reviewer_count_fallback_injects_when_verdict_section_open_ended() -> None:
    judge = "## Verdict\n\nVERDICT: APPROVE\n"
    reviewer = "DIFF_FINDINGS_CRITICAL: 4\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert parse_critical_count(out) == 4


def test_apply_reviewer_count_fallback_anchors_before_in_scope_line() -> None:
    judge = (
        "## Verdict\n\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 2\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert (
        out.index("DIFF_FINDINGS_CRITICAL: 2")
        < out.index("DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0")
        < out.index("VERDICT: APPROVE")
    )


def test_apply_reviewer_count_fallback_appends_when_no_anchor_found() -> None:
    judge = "## Verdict\n\nNo machine-readable lines here.\n"
    reviewer = "DIFF_FINDINGS_CRITICAL: 7\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert out == "## Verdict\n\nNo machine-readable lines here.\nDIFF_FINDINGS_CRITICAL: 7\n"


def test_maybe_flag_count_flip_noop_when_verdict_matches() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert verd_mod._maybe_flag_count_flip(judge) == judge  # noqa: SLF001


def test_maybe_flag_count_flip_noop_when_already_request_changes() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 3\n"
    assert verd_mod._maybe_flag_count_flip(judge) == judge  # noqa: SLF001


def test_maybe_flag_count_flip_rewrites_and_banners() -> None:
    judge = "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 3\n"
    out = verd_mod._maybe_flag_count_flip(judge)  # noqa: SLF001
    assert out.startswith("> VERDICT FLIPPED")
    assert "VERDICT: REQUEST_CHANGES" in out
    assert "VERDICT: APPROVE" not in out
    assert "found 3 CRITICAL finding(s)" in out


def test_maybe_flag_count_flip_fires_on_in_scope_reconciliation() -> None:
    judge = (
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 1\n"
    )
    out = verd_mod._maybe_flag_count_flip(judge)  # noqa: SLF001
    assert "VERDICT: REQUEST_CHANGES" in out
    assert "found 1 CRITICAL finding(s)" in out


def test_restoration_anchor_prefers_earliest_of_the_three_patterns() -> None:
    only_critical = "## Verdict\n\nDIFF_FINDINGS_CRITICAL: 1\n"
    assert verd_mod._restoration_anchor(only_critical) == only_critical.index(  # noqa: SLF001
        "DIFF_FINDINGS_CRITICAL: 1"
    )
    only_in_scope = "## Verdict\n\nDIFF_FINDINGS_CRITICAL_IN_SCOPE: 1\n"
    assert verd_mod._restoration_anchor(only_in_scope) == only_in_scope.index(  # noqa: SLF001
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 1"
    )
    only_verdict = "## Verdict\n\nVERDICT: APPROVE\n"
    assert verd_mod._restoration_anchor(only_verdict) == only_verdict.index(  # noqa: SLF001
        "VERDICT: APPROVE"
    )
    multiple = (
        "## Verdict\n\n"
        "DIFF_FINDINGS_CRITICAL: 1\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    assert verd_mod._restoration_anchor(multiple) == multiple.index(  # noqa: SLF001
        "DIFF_FINDINGS_CRITICAL: 1"
    )
    none_found = "## Verdict\n\nNo machine-readable lines here.\n"
    assert verd_mod._restoration_anchor(none_found) == len(none_found)  # noqa: SLF001


def test_reviewer_count_fallback_parses_injected_value_despite_preceding_stray_count() -> None:
    judge = (
        "## Rationale\n"
        "`DIFF_FINDINGS_CRITICAL: 9`\n\n"
        "## Verdict\n\n"
        "VERDICT: APPROVE\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 3\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert parse_critical_count(out) == 3


@pytest.mark.parametrize(
    ("heading", "next_heading"),
    [
        ("## Verdict", "## Rationale"),
        ("### verdict", "### rationale"),
        ("#### VERDICT", "#### Rationale"),
    ],
)
def test_verdict_heading_matches_case_and_depth_variants(
    heading: str, next_heading: str
) -> None:
    judge = (
        f"{heading}\n\nVERDICT: APPROVE\n\n"
        f"{next_heading}\nExample:\n`DIFF_FINDINGS_CRITICAL: 9`\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 3\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert parse_critical_count(out) == 3


def test_verdict_section_ends_at_a_same_or_shallower_heading_not_a_nested_one() -> None:
    judge = (
        "## Verdict\n\n"
        "VERDICT: APPROVE\n\n"
        "#### Evidence\n"
        "DIFF_FINDINGS_CRITICAL: 0\n\n"
        "## Rationale\ndone\n"
    )
    reviewer = "DIFF_FINDINGS_CRITICAL: 9\n"
    out = verd_mod._apply_reviewer_count_fallback(judge, reviewer)  # noqa: SLF001
    assert out == judge


def test_rewrite_verdict_to_request_changes_is_a_noop_when_already_request_changes() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    assert verd_mod.rewrite_verdict_to_request_changes(judge, "> banner\n\n") == judge


def test_critical_count_report_reported_true_when_judge_reported_even_without_reviewer() -> None:
    report = verd_mod.CriticalCountReport(reviewer=None, judge_reported=True)
    assert report.reported is True


def test_critical_count_report_reported_true_when_reviewer_present_even_without_judge() -> None:
    report = verd_mod.CriticalCountReport(reviewer=0, judge_reported=False)
    assert report.reported is True


def test_critical_count_report_reported_false_when_neither() -> None:
    report = verd_mod.CriticalCountReport(reviewer=None, judge_reported=False)
    assert report.reported is False


@pytest.mark.parametrize(
    ("judge", "expected_judge_reported"),
    [
        ("VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n", True),
        ("## Verdict\n\nVERDICT: APPROVE\n", False),
        (
            "## Verdict\n\nVERDICT: APPROVE\n\n## Rationale\n`DIFF_FINDINGS_CRITICAL: 9`\n",
            False,
        ),
        ("## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n", True),
    ],
)
def test_report_critical_counts_judge_reported_matches_fallback_passthrough(
    judge: str, expected_judge_reported: bool,
) -> None:
    report = verd_mod.report_critical_counts(judge, "DIFF_FINDINGS_CRITICAL: 3\n")
    assert report.judge_reported is expected_judge_reported


def test_report_critical_counts_reviewer_reported_when_judge_silent() -> None:
    report = verd_mod.report_critical_counts(
        "## Verdict\n\nVERDICT: APPROVE\n", "DIFF_FINDINGS_CRITICAL: 4\n"
    )
    assert report.judge_reported is False
    assert report.reviewer == 4
    assert report.reported is True


def test_report_critical_counts_neither_reported() -> None:
    report = verd_mod.report_critical_counts(
        "## Verdict\n\nVERDICT: APPROVE\n", "no machine readable line here"
    )
    assert report.judge_reported is False
    assert report.reviewer is None
    assert report.reported is False


def test_count_floor_reason_none_critical_with_absent_counts_is_not_reported() -> None:
    assert verd_mod.count_floor_reason(None, None) == "count_not_reported"


def test_count_floor_reason_reviewer_zero_judge_silent_clears() -> None:
    counts = verd_mod.CriticalCountReport(reviewer=0, judge_reported=False)
    assert verd_mod.count_floor_reason(0, counts) is None


def test_count_floor_reason_reviewer_nonzero_withholds() -> None:
    counts = verd_mod.CriticalCountReport(reviewer=2, judge_reported=True)
    assert verd_mod.count_floor_reason(0, counts) == "reviewer_nonzero"


def test_count_floor_reason_posted_count_nonzero_withholds() -> None:
    counts = verd_mod.CriticalCountReport(reviewer=0, judge_reported=True)
    assert verd_mod.count_floor_reason(3, counts) == "posted_count_nonzero"


def test_count_floor_reason_neither_role_reported_withholds() -> None:
    counts = verd_mod.CriticalCountReport(reviewer=None, judge_reported=False)
    assert verd_mod.count_floor_reason(0, counts) == "count_not_reported"


def test_report_critical_counts_ignores_stray_count_line_outside_verdict_section() -> None:
    judge = (
        "## Verdict\n\n"
        "VERDICT: APPROVE\n\n"
        "## Rationale\n"
        "Example of a well-formed line:\n"
        "`DIFF_FINDINGS_CRITICAL: 9`\n"
    )
    report = verd_mod.report_critical_counts(judge, "DIFF_FINDINGS_CRITICAL: 3\n")
    assert report.judge_reported is False
    assert report.reviewer == 3
    assert report.reported is True

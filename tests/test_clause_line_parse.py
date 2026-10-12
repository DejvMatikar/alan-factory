from __future__ import annotations

from alan.teams._clause_line_parse import ClauseAuditRow, parse_clause_audit

_IDS = frozenset({"req-1", "req-2"})


def test_canonical_line_parses() -> None:
    result = parse_clause_audit("CLAUSE req-1: PASS — file.py:10 proves it\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "file.py:10 proves it"),)


def test_bold_id_parses_clean_id() -> None:
    result = parse_clause_audit("CLAUSE **req-1**: PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_bold_verdict_parses() -> None:
    result = parse_clause_audit("CLAUSE req-1: **PASS** — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_dash_list_marker_parses() -> None:
    result = parse_clause_audit("- CLAUSE req-1: FAIL — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "FAIL", "evidence"),)


def test_star_list_marker_parses() -> None:
    result = parse_clause_audit("* CLAUSE req-1: PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_numbered_list_marker_parses() -> None:
    result = parse_clause_audit("1. CLAUSE req-1: UNVERIFIABLE — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "UNVERIFIABLE", "evidence"),)


def test_blockquote_marker_parses() -> None:
    result = parse_clause_audit("> CLAUSE req-1: PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_missing_clause_keyword_parses() -> None:
    result = parse_clause_audit("req-1: PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_backticked_id_parses() -> None:
    result = parse_clause_audit("`req-1`: PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_inverted_order_parses() -> None:
    result = parse_clause_audit("req-1 — FAIL: evidence text\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "FAIL", "evidence text"),)


def test_two_decorations_combined_parse() -> None:
    result = parse_clause_audit("- **CLAUSE req-1: FAIL** — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "FAIL", "evidence"),)


def test_all_three_fleet_shapes_in_one_output() -> None:
    text = (
        "CLAUSE req-1: PASS — a\n"
        "- CLAUSE req-2: FAIL — b\n"
    )
    result = parse_clause_audit(text, _IDS)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-1", "PASS", "a"),
        ClauseAuditRow("req-2", "FAIL", "b"),
    )


def test_foreign_id_dropped() -> None:
    text = "CLAUSE req-1: PASS — a\nCLAUSE req-999: FAIL — b\n"
    result = parse_clause_audit(text, _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "a"),)


def test_conflicting_verdicts_reject_whole_output() -> None:
    text = "CLAUSE req-1: PASS — a\nCLAUSE req-1: FAIL — b\n"
    assert parse_clause_audit(text, _IDS) is None


def test_identical_duplicate_collapses_to_first() -> None:
    text = "CLAUSE req-1: PASS — a\nCLAUSE req-1: PASS — a again\n"
    result = parse_clause_audit(text, _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "a"),)


def test_garbage_returns_none() -> None:
    assert parse_clause_audit("nothing to see here at all\n", _IDS) is None


def test_decorated_fail_stays_fail_with_evidence_intact() -> None:
    result = parse_clause_audit("CLAUSE **req-1**: FAIL — the file is missing\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "FAIL", "the file is missing"),)


def test_decorated_fail_with_no_evidence_does_not_match() -> None:
    assert parse_clause_audit("CLAUSE **req-1**: FAIL — \n", _IDS) is None


def test_canonical_fail_with_empty_evidence_still_demotes_to_unverifiable() -> None:
    result = parse_clause_audit("CLAUSE req-1: FAIL — \n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "UNVERIFIABLE", ""),)


def test_count_cross_check_fires_on_decorated_input() -> None:
    text = "- CLAUSE req-1: FAIL — evidence\nCLAUSE_FAILURES: 0\n"
    result = parse_clause_audit(text, _IDS)
    assert result is not None
    assert result.count_mismatch is True


def test_bold_id_with_parenthetical_label_parses() -> None:
    result = parse_clause_audit("**req-1** (audit clause): PASS — evidence\n", _IDS)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req-1", "PASS", "evidence"),)


def test_canonical_id_with_underscore_is_not_rejected_as_decoration() -> None:
    ids = frozenset({"req_custom"})
    result = parse_clause_audit("CLAUSE req_custom: PASS — evidence\n", ids)
    assert result is not None
    assert result.rows == (ClauseAuditRow("req_custom", "PASS", "evidence"),)


def test_decorated_fail_with_underscore_id_survives_mixed_batch() -> None:
    ids = frozenset({"req-normal", "req_custom"})
    text = (
        "CLAUSE req-normal: PASS — fine\n"
        "- CLAUSE req_custom: FAIL — the file is missing\n"
    )
    result = parse_clause_audit(text, ids)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-normal", "PASS", "fine"),
        ClauseAuditRow("req_custom", "FAIL", "the file is missing"),
    )


def test_inverted_fail_with_underscore_id_survives_mixed_batch() -> None:
    ids = frozenset({"req-normal", "req_custom"})
    text = (
        "CLAUSE req-normal: PASS — fine\n"
        "req_custom — FAIL: the file is missing\n"
    )
    result = parse_clause_audit(text, ids)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-normal", "PASS", "fine"),
        ClauseAuditRow("req_custom", "FAIL", "the file is missing"),
    )


def test_canonical_underscore_emphasis_wrapped_id_binds_to_audited_id() -> None:
    ids = frozenset({"req-1", "req-2"})
    text = (
        "CLAUSE req-1: PASS -- fine\n"
        "CLAUSE _req-2_: FAIL -- the file is missing\n"
    )
    result = parse_clause_audit(text, ids)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-1", "PASS", "fine"),
        ClauseAuditRow("req-2", "FAIL", "the file is missing"),
    )


def test_decorated_underscore_emphasis_wrapped_id_binds_to_audited_id() -> None:
    ids = frozenset({"req-1", "req-2"})
    text = (
        "CLAUSE req-1: PASS -- fine\n"
        "- _req-2_: FAIL -- the file is missing\n"
    )
    result = parse_clause_audit(text, ids)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-1", "PASS", "fine"),
        ClauseAuditRow("req-2", "FAIL", "the file is missing"),
    )


def test_inverted_underscore_emphasis_wrapped_id_binds_to_audited_id() -> None:
    ids = frozenset({"req-1", "req-2"})
    text = (
        "CLAUSE req-1: PASS -- fine\n"
        "_req-2_ -- FAIL: the file is missing\n"
    )
    result = parse_clause_audit(text, ids)
    assert result is not None
    assert result.rows == (
        ClauseAuditRow("req-1", "PASS", "fine"),
        ClauseAuditRow("req-2", "FAIL", "the file is missing"),
    )

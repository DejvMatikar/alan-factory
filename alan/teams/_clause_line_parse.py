from __future__ import annotations

import re
from typing import Literal, NamedTuple, cast

ClauseVerdict = Literal["PASS", "FAIL", "UNVERIFIABLE"]


class ClauseAuditRow(NamedTuple):
    req_id: str
    verdict: ClauseVerdict
    evidence: str


class ClauseAuditResult(NamedTuple):
    rows: tuple[ClauseAuditRow, ...]
    reported_failures: int | None
    count_mismatch: bool
    parse_failed: bool


_CLAUSE_LINE_CANONICAL_RE = re.compile(
    r"^CLAUSE\s+(?P<req_id>[^\s:]+):\s*(?P<verdict>PASS|FAIL|UNVERIFIABLE)"
    r"\s*(?:--|—|-)(?P<evidence>.*)$"
)
_CANONICAL_ID_DECORATION_CHARS = frozenset("*`")

_LIST_PREFIX = r"(?:>\s*)?(?:[-*+]\s+|\d+\.\s+)?\s*"
_MD = r"[*_`]*"
_LABEL = r"(?:\s*\([^)]*\))?"

_CLAUSE_LINE_DECORATED_RE = re.compile(
    r"^" + _LIST_PREFIX
    + _MD + r"(?:CLAUSE\s+)?" + _MD
    + r"(?P<req_id>[^\s:*`]+)"
    + _MD + _LABEL + r":\s*" + _MD
    + r"(?P<verdict>PASS|FAIL|UNVERIFIABLE)" + _MD
    + r"\s*(?:--|—|-)\s*(?P<evidence>\S.*)$"
)

_CLAUSE_LINE_INVERTED_RE = re.compile(
    r"^" + _LIST_PREFIX
    + _MD + r"(?:CLAUSE\s+)?" + _MD
    + r"(?P<req_id>[^\s:*`]+)" + _MD
    + r"\s*(?:--|—|-)\s*" + _MD
    + r"(?P<verdict>PASS|FAIL|UNVERIFIABLE)" + _MD + r":\s*"
    + r"(?P<evidence>\S.*)$"
)

_CLAUSE_FAILURES_RE = re.compile(r"^CLAUSE_FAILURES:\s*(?P<count>\d+)", re.MULTILINE)


def _row_from_canonical(match: re.Match[str]) -> ClauseAuditRow | None:
    req_id = match.group("req_id")
    if not _CANONICAL_ID_DECORATION_CHARS.isdisjoint(req_id):
        return None
    verdict = cast("ClauseVerdict", match.group("verdict"))
    evidence = match.group("evidence").strip()
    if verdict == "FAIL" and not evidence:
        verdict = "UNVERIFIABLE"
    return ClauseAuditRow(req_id=req_id, verdict=verdict, evidence=evidence)


def _row_from_decorated(match: re.Match[str]) -> ClauseAuditRow:
    verdict = cast("ClauseVerdict", match.group("verdict"))
    evidence = match.group("evidence").strip()
    return ClauseAuditRow(req_id=match.group("req_id"), verdict=verdict, evidence=evidence)


def _match_line(line: str) -> ClauseAuditRow | None:
    canonical = _CLAUSE_LINE_CANONICAL_RE.match(line)
    if canonical is not None:
        row = _row_from_canonical(canonical)
        if row is not None:
            return row
    decorated = _CLAUSE_LINE_DECORATED_RE.match(line)
    if decorated is not None:
        return _row_from_decorated(decorated)
    inverted = _CLAUSE_LINE_INVERTED_RE.match(line)
    if inverted is not None:
        return _row_from_decorated(inverted)
    return None


def _collect_matches(output: str) -> list[ClauseAuditRow]:
    rows: list[ClauseAuditRow] = []
    for line in output.splitlines():
        row = _match_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _dedupe_or_reject(
    rows: list[ClauseAuditRow],
) -> tuple[ClauseAuditRow, ...] | None:
    first_by_id: dict[str, ClauseAuditRow] = {}
    for row in rows:
        existing = first_by_id.get(row.req_id)
        if existing is None:
            first_by_id[row.req_id] = row
        elif existing.verdict != row.verdict:
            return None
    return tuple(first_by_id.values())


def _reported_failures_and_mismatch(
    output: str, rows: tuple[ClauseAuditRow, ...]
) -> tuple[int | None, bool]:
    failures_match = _CLAUSE_FAILURES_RE.search(output)
    reported = int(failures_match.group("count")) if failures_match else None
    evidenced_fails = sum(1 for row in rows if row.verdict == "FAIL")
    mismatch = reported is not None and reported != evidenced_fails
    return reported, mismatch


def _bind_to_audited_id(req_id: str, audited_ids: frozenset[str]) -> str | None:
    if req_id in audited_ids:
        return req_id
    stripped = req_id.strip("*_`")
    if stripped != req_id and stripped in audited_ids:
        return stripped
    return None


def parse_clause_audit(
    output: str, audited_ids: frozenset[str]
) -> ClauseAuditResult | None:
    matches = _collect_matches(output)
    kept: list[ClauseAuditRow] = []
    for row in matches:
        bound_id = _bind_to_audited_id(row.req_id, audited_ids)
        if bound_id is None:
            continue
        kept.append(row if bound_id == row.req_id else row._replace(req_id=bound_id))
    rows = _dedupe_or_reject(kept)
    if not rows:
        return None
    reported, mismatch = _reported_failures_and_mismatch(output, rows)
    return ClauseAuditResult(
        rows=rows, reported_failures=reported, count_mismatch=mismatch, parse_failed=False,
    )


__all__ = [
    "ClauseAuditResult",
    "ClauseAuditRow",
    "ClauseVerdict",
    "parse_clause_audit",
]

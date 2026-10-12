from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_DECOR = r"[ \t*_`#>~-]*"
_VERDICT_RE = re.compile(
    rf"^{_DECOR}VERDICT:{_DECOR}(APPROVE|REQUEST_CHANGES){_DECOR}$", re.MULTILINE
)
_CRITICAL_RE = re.compile(
    rf"^{_DECOR}DIFF_FINDINGS_CRITICAL:{_DECOR}(\d+)\b", re.MULTILINE
)
_CRITICAL_IN_SCOPE_RE = re.compile(
    rf"^{_DECOR}DIFF_FINDINGS_CRITICAL_IN_SCOPE:{_DECOR}(\d+)\b", re.MULTILINE
)

Verdict = Literal["APPROVE", "REQUEST_CHANGES"]

VERDICT_PREFIX = "VERDICT:"
DIFF_FINDINGS_CRITICAL_PREFIX = "DIFF_FINDINGS_CRITICAL:"
DIFF_FINDINGS_CRITICAL_IN_SCOPE_PREFIX = "DIFF_FINDINGS_CRITICAL_IN_SCOPE:"


def parse_verdict(judge_output: str) -> Verdict | None:
    match = _VERDICT_RE.search(judge_output)
    if match is None:
        return None
    return match.group(1)  # type: ignore[return-value]


def parse_critical_count(judge_output: str) -> int:
    match = _CRITICAL_RE.search(judge_output)
    if match is None:
        return 0
    return int(match.group(1))


def parse_critical_count_or_none(judge_output: str) -> int | None:
    match = _CRITICAL_RE.search(judge_output)
    if match is None:
        return None
    return int(match.group(1))


def parse_in_scope_critical_count(judge_output: str) -> int | None:
    match = _CRITICAL_IN_SCOPE_RE.search(judge_output)
    if match is None:
        return None
    return int(match.group(1))


def effective_critical_count(judge_output: str) -> int:
    in_scope = parse_in_scope_critical_count(judge_output)
    return in_scope if in_scope is not None else parse_critical_count(judge_output)


def _strip_in_scope_critical_line(  # pyright: ignore[reportUnusedFunction]
    judge_output: str,
) -> str:
    if _CRITICAL_IN_SCOPE_RE.search(judge_output) is None:
        return judge_output
    kept = [
        line
        for line in judge_output.splitlines()
        if _CRITICAL_IN_SCOPE_RE.match(line) is None
    ]
    return "\n".join(kept)


_VERDICT_HEADING_RE = re.compile(
    r"^(#{2,6})\s+Verdict\b.*$", re.MULTILINE | re.IGNORECASE
)
_NEXT_HEADING_RE_BY_DEPTH: dict[int, re.Pattern[str]] = {
    n: re.compile(rf"^#{{1,{n}}}\s", re.MULTILINE) for n in range(2, 7)
}


def _section_end(judge_output: str, heading: re.Match[str]) -> int:
    depth = len(heading.group(1))
    next_heading = _NEXT_HEADING_RE_BY_DEPTH[depth].search(judge_output, heading.end())
    return next_heading.start() if next_heading is not None else len(judge_output)


def _verdict_section(judge_output: str) -> str:
    heading = _VERDICT_HEADING_RE.search(judge_output)
    if heading is None:
        return judge_output
    return judge_output[heading.end():_section_end(judge_output, heading)]


def _restoration_anchor(judge_output: str) -> int:
    matches = (
        _CRITICAL_RE.search(judge_output),
        _CRITICAL_IN_SCOPE_RE.search(judge_output),
        _VERDICT_RE.search(judge_output),
    )
    starts = [m.start() for m in matches if m is not None]
    return min(starts) if starts else len(judge_output)


def _apply_reviewer_count_fallback(  # pyright: ignore[reportUnusedFunction]
    judge_output: str, diff_reviewer_output: str
) -> str:
    if _CRITICAL_RE.search(_verdict_section(judge_output)) is not None:
        return judge_output
    reviewer_count = parse_critical_count(diff_reviewer_output)
    injected = f"{DIFF_FINDINGS_CRITICAL_PREFIX} {reviewer_count}\n"
    anchor = _restoration_anchor(judge_output)
    if anchor >= len(judge_output):
        return f"{judge_output.rstrip()}\n{injected}"
    return judge_output[:anchor] + injected + judge_output[anchor:]


@dataclass(frozen=True)
class CriticalCountReport:
    reviewer: int | None
    judge_reported: bool

    @property
    def reported(self) -> bool:
        return self.judge_reported or self.reviewer is not None


def report_critical_counts(judge_output: str, reviewer_output: str) -> CriticalCountReport:
    judge_reported = _CRITICAL_RE.search(_verdict_section(judge_output)) is not None
    reviewer = parse_critical_count_or_none(reviewer_output)
    return CriticalCountReport(reviewer=reviewer, judge_reported=judge_reported)


def count_floor_reason(
    critical: int | None, counts: CriticalCountReport | None,
) -> str | None:
    if critical is None:
        return "count_not_reported"
    if counts is None or not counts.reported:
        return "count_not_reported"
    if counts.reviewer is not None and counts.reviewer > 0:
        return "reviewer_nonzero"
    if critical != 0:
        return "posted_count_nonzero"
    return None


def effective_verdict(judge_output: str) -> Verdict | None:
    verdict = parse_verdict(judge_output)
    if verdict is None:
        return None
    if verdict == "APPROVE" and effective_critical_count(judge_output) > 0:
        return "REQUEST_CHANGES"
    return verdict


_COUNT_FLIP_BANNER_TEMPLATE = (
    "> VERDICT FLIPPED — the judge's own verdict line said APPROVE, but "
    "critical-count reconciliation found {count} CRITICAL finding(s) still "
    "unresolved, so the verdict line below is rewritten to REQUEST_CHANGES. "
    "This banner exists so the posted review body never contradicts the "
    "decision it records.\n\n"
)


def _maybe_flag_count_flip(  # pyright: ignore[reportUnusedFunction]
    judge_output: str,
) -> str:
    if parse_verdict(judge_output) != "APPROVE":
        return judge_output
    if effective_verdict(judge_output) != "REQUEST_CHANGES":
        return judge_output
    banner = _COUNT_FLIP_BANNER_TEMPLATE.format(
        count=effective_critical_count(judge_output)
    )
    rewritten = _VERDICT_RE.sub("VERDICT: REQUEST_CHANGES", judge_output, count=1)
    return banner + rewritten


def rewrite_verdict_to_request_changes(judge_output: str, banner: str) -> str:
    current = parse_verdict(judge_output)
    if current is None or current == "REQUEST_CHANGES":
        return judge_output
    rewritten = _VERDICT_RE.sub("VERDICT: REQUEST_CHANGES", judge_output, count=1)
    return banner + rewritten

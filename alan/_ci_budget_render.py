from __future__ import annotations

from collections.abc import Sequence

from alan import ci_budget as budget_rules
from alan._ci_drift import SLACK_RATIO, SLOW_RATIO, Direction, DriftFinding, RunSample
from alan.ci_budget import (
    FAIL_MIN_S,
    FAIL_RATIO,
    WARN_MIN_S,
    WARN_RATIO,
    Report,
    Status,
    Verdict,
    fail_point_s,
    warn_point_s,
)

COMMENT_MARKER = "<!-- ci-duration-budget -->"
ANNOTATION_TITLE = "CI duration budget"
BUDGET_FILE = ".github/ci-duration-budgets.json"
DOC_PATH = "docs/architecture/ci_duration_budgets.md"
MAX_ANNOTATIONS = 10
NOT_AVAILABLE = "n/a"
ADVISORY_NOTE = (
    "The check is advisory while its thresholds are calibrated, so this does not fail the run."
)


def escape_annotation(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation(level: str, message: str) -> str:
    return f"::{level} title={ANNOTATION_TITLE}::{escape_annotation(message)}"


def _seconds(value: float) -> str:
    return f"{value:.0f}s"


def difference_text(verdict: Verdict) -> str:
    return f"{verdict.over_s:+.0f}s ({verdict.over_pct:+.0f}%)"


def _figures(verdict: Verdict) -> str:
    return (
        f"{verdict.name}: measured {_seconds(verdict.duration_s)} against a budget of "
        f"{_seconds(verdict.budget_s)} ({difference_text(verdict)})"
    )


def slowest_section(slowest: Sequence[tuple[str, float]]) -> str:
    if not slowest:
        return "This PR added or changed no test files with recorded timings."
    listing = ", ".join(f"`{path}` ({seconds:.1f}s)" for path, seconds in slowest)
    return (
        "Slowest test files this PR added or changed (cumulative test time, summed across "
        f"workers): {listing}."
    )


def fail_message(verdict: Verdict, slowest: Sequence[tuple[str, float]] | None) -> str:
    parts = [
        f"{_figures(verdict)}, past the fail point of {_seconds(fail_point_s(verdict.budget_s))}.",
        f"Do not raise the budget in this PR: {BUDGET_FILE} is a tightening-only ratchet, "
        "so find what slowed the job.",
        "A rerun will not change this verdict; push a fix to re-measure.",
    ]
    if not budget_rules.FAIL_BLOCKS_PR:
        parts.append(ADVISORY_NOTE)
    if slowest is not None:
        parts.append(slowest_section(slowest))
    return " ".join(parts)


def warn_message(verdict: Verdict) -> str:
    return (
        f"{_figures(verdict)}. Warns from {_seconds(warn_point_s(verdict.budget_s))}, "
        f"fails from {_seconds(fail_point_s(verdict.budget_s))}."
    )


def contention_message(verdict: Verdict) -> str:
    ratio = verdict.peer_ratio if verdict.peer_ratio is not None else 0.0
    return (
        f"{_figures(verdict)}, which would fail, but every other measured job is also slow "
        f"(median {ratio:.2f}x budget), so host contention is the likely cause. Not failing."
    )


def unbudgeted_message(verdict: Verdict, workflow: str) -> str:
    return (
        f"{verdict.name}: measured {_seconds(verdict.duration_s)} but no duration budget is "
        f'committed. Add one under "{workflow}" in {BUDGET_FILE}.'
    )


def _warning_text(verdict: Verdict, workflow: str) -> str | None:
    if verdict.status is Status.WARN:
        return warn_message(verdict)
    if verdict.status is Status.CONTENTION:
        return contention_message(verdict)
    if verdict.status is Status.UNBUDGETED:
        return unbudgeted_message(verdict, workflow)
    return None


def _fail_level() -> str:
    return "error" if budget_rules.FAIL_BLOCKS_PR else "warning"


def _detail_texts(report: Report) -> list[tuple[str, str]]:
    details: list[tuple[str, str]] = []
    for verdict in report.verdicts:
        if verdict.status is Status.FAIL:
            details.append((_fail_level(), fail_message(verdict, report.slowest.get(verdict.name))))
            continue
        text = _warning_text(verdict, report.workflow)
        if text is not None:
            details.append(("warning", text))
    return details


def annotation_lines(report: Report) -> list[str]:
    details = _detail_texts(report)
    ordered = [d for d in details if d[0] == "error"] + [d for d in details if d[0] != "error"]
    lines = [annotation(level, text) for level, text in ordered]
    lines.extend(annotation("warning", notice) for notice in report.notices)
    return lines[:MAX_ANNOTATIONS]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _measured_cell(verdict: Verdict) -> str:
    if verdict.status is Status.NOT_MEASURED:
        return f"not measured ({verdict.note})"
    return _seconds(verdict.duration_s)


def _budget_cell(verdict: Verdict) -> str:
    return _seconds(verdict.budget_s) if verdict.budget_s > 0 else "none"


def _over_cell(verdict: Verdict) -> str:
    if verdict.status in (Status.NOT_MEASURED, Status.UNBUDGETED):
        return "-"
    return difference_text(verdict)


def _row(verdict: Verdict) -> str:
    cells = (
        _cell(verdict.name),
        _budget_cell(verdict),
        _measured_cell(verdict),
        _over_cell(verdict),
        verdict.status.value,
    )
    return "| " + " | ".join(cells) + " |"


def _table(verdicts: Sequence[Verdict]) -> list[str]:
    if not verdicts:
        return []
    header = [
        "| Job | Budget | Measured | Over budget | Verdict |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    return [*header, *(_row(verdict) for verdict in verdicts), ""]


def _thresholds_line() -> str:
    return (
        f"Warns above +{WARN_RATIO:.0%} and at least {WARN_MIN_S}s over budget; fails above "
        f"+{FAIL_RATIO:.0%} and at least {FAIL_MIN_S}s over budget. See {DOC_PATH}."
    )


def summary_markdown(report: Report) -> str:
    lines = ["## Duration budgets", "", *_table(report.verdicts)]
    lines.extend(f"- {notice}" for notice in report.notices)
    lines.extend(f"- **{level}**: {text}" for level, text in _detail_texts(report))
    lines.extend(["", _thresholds_line()])
    return "\n".join(lines) + "\n"


def comment_body(report: Report, run_url: str) -> str:
    return f"{COMMENT_MARKER}\n{summary_markdown(report)}\n[Run details]({run_url})\n"


def resolved_comment_body(run_url: str) -> str:
    return (
        f"{COMMENT_MARKER}\nDuration budgets: every measured job is within budget on the "
        f"latest run ([run details]({run_url})).\n"
    )


def _run_label(run: RunSample) -> str:
    who = f"#{run.pr}" if run.pr is not None else f"run {run.run_id}"
    return f"{who} ({run.date[:10]})" if run.date else who


def _window_text(window: Sequence[RunSample]) -> str:
    if not window:
        return "unavailable"
    return ", ".join(_run_label(run) for run in window)


def _before_text(finding: DriftFinding) -> str:
    if finding.before_s is None:
        return f"{NOT_AVAILABLE} (fewer than 4 earlier runs)"
    return _seconds(finding.before_s)


def drift_issue_title(finding: DriftFinding) -> str:
    if finding.direction is Direction.SLOW:
        return (
            f'CI job "{finding.job}" drifted above its duration budget: '
            f"{_seconds(finding.after_s)} median vs {_seconds(finding.budget_s)}"
        )
    return (
        f'CI job "{finding.job}" runs well under its duration budget: '
        f"{_seconds(finding.after_s)} median vs {_seconds(finding.budget_s)}"
    )


def _crossing_text(finding: DriftFinding) -> str:
    if finding.direction is Direction.SLOW:
        return f"rose above {SLOW_RATIO:.2f}x the budget"
    return f"fell below {SLACK_RATIO:.2f}x the budget"


def _action_text(finding: DriftFinding) -> str:
    if finding.direction is Direction.SLOW:
        return (
            "Find the change in that window that slowed the job and fix it. Only if the "
            f"slowdown is intended, raise the budget to {finding.proposed_s}s in its own PR: "
            "a raise is a human decision, and the raised budget must keep its fail point "
            "under 95% of the job's timeout."
        )
    return (
        f"Lower the budget to {finding.proposed_s}s in {BUDGET_FILE}. Lowering a budget is "
        "always allowed."
    )


def drift_issue_body(finding: DriftFinding) -> str:
    lines = [
        f"Workflow `{finding.workflow}`, job **{finding.job}**.",
        "",
        "| | Seconds |",
        "| --- | ---: |",
        f"| Committed budget | {_seconds(finding.budget_s)} |",
        f"| Median of the last {finding.samples} successful main runs | "
        f"{_seconds(finding.after_s)} |",
        f"| Median of the 7 runs before those | {_before_text(finding)} |",
        f"| Proposed budget | {finding.proposed_s}s |",
        "",
        f"The rolling median {_crossing_text(finding)} within these merges: "
        f"{_window_text(finding.window)}.",
        "",
        _action_text(finding),
        "",
        f"Background and thresholds: {DOC_PATH}.",
    ]
    return "\n".join(lines) + "\n"

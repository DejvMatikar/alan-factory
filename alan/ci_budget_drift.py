from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from alan import _ci_budget_render as render
from alan import _ci_drift as drift
from alan import _ci_jobs_api as jobs_api
from alan.ci_budget import BudgetFileError, Budgets, load_budgets, workspace_path

DEFAULT_BUDGETS_PATH = ".github/ci-duration-budgets.json"
RUNS_PER_WORKFLOW = 14
MAX_FINDINGS = 20
COLORS = {drift.Direction.SLOW: "D93F0B", drift.Direction.SLACK: "0E8A16"}
DESCRIPTIONS = {
    drift.Direction.SLOW: "A CI job's main-branch median drifted above its duration budget",
    drift.Direction.SLACK: "A CI job's main-branch median sits well below its duration budget",
}


def collect(
    api_url: str, repo: str, token: str, budgets: Budgets, limit: int
) -> tuple[list[drift.DriftFinding], list[str]]:
    findings: list[drift.DriftFinding] = []
    warnings: list[str] = []
    for workflow, jobs in sorted(budgets.items()):
        try:
            samples = jobs_api.main_samples(api_url, repo, workflow, token, limit)
        except jobs_api.ApiUnavailableError as exc:
            warnings.append(f"{workflow}: main-branch durations unavailable, skipped: {exc}")
            continue
        findings.extend(drift.drift_findings(workflow, samples, jobs))
    return findings, warnings


def matrix_entry(finding: drift.DriftFinding) -> dict[str, str]:
    return {
        "label": drift.finding_label(finding.workflow, finding.job, finding.direction),
        "title": render.drift_issue_title(finding),
        "body": render.drift_issue_body(finding),
        "marker": drift.dedup_marker(finding.direction, finding.budget_s, finding.after_s),
        "color": COLORS[finding.direction],
        "description": DESCRIPTIONS[finding.direction],
    }


def output_lines(entries: Sequence[Mapping[str, str]]) -> list[str]:
    matrix = json.dumps({"include": list(entries)}, separators=(",", ":"))
    return [f"matrix={matrix}", f"count={len(entries)}"]


def write_outputs(entries: Sequence[Mapping[str, str]], output_path: str | None) -> None:
    lines = output_lines(entries)
    if not output_path:
        print("\n".join(lines))
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _parse_args(argv: Sequence[str] | None, environ: Mapping[str, str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m alan.ci_budget_drift")
    parser.add_argument("--budgets", type=workspace_path, default=DEFAULT_BUDGETS_PATH)
    parser.add_argument("--repo", default=environ.get("GITHUB_REPOSITORY", ""))
    api_url = environ.get("GITHUB_API_URL", jobs_api.DEFAULT_API_URL)
    parser.add_argument("--api-url", default=api_url)
    parser.add_argument("--runs", type=int, default=RUNS_PER_WORKFLOW)
    return parser.parse_args(argv)


def _load(path: Path) -> tuple[Budgets, list[str]]:
    try:
        return load_budgets(path), []
    except BudgetFileError as exc:
        return {}, [f"Budget file unusable, so no drift was checked: {exc}"]


def main(argv: Sequence[str] | None = None) -> int:
    environ = os.environ
    args = _parse_args(argv, environ)
    budgets, warnings = _load(args.budgets)
    findings, collected = collect(
        args.api_url, args.repo, environ.get("GITHUB_TOKEN", ""), budgets, args.runs
    )
    for warning in [*warnings, *collected]:
        print(render.annotation("warning", warning))
    write_outputs([matrix_entry(f) for f in findings[:MAX_FINDINGS]], environ.get("GITHUB_OUTPUT"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

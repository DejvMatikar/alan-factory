from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from alan import _ci_budget_render as render
from alan import _ci_jobs_api as jobs_api
from alan import _ci_test_timings as timings
from alan import ci_budget as budget_rules
from alan.ci_budget import (
    BudgetFileError,
    Report,
    Status,
    Verdict,
    classify,
    has_failure,
    load_budgets,
    workspace_path,
)

DEFAULT_BUDGETS_PATH = ".github/ci-duration-budgets.json"
DEFAULT_WORKFLOW = "tests.yml"
DEFAULT_COMMENT_PATH = "duration-budget-comment.md"
DEFAULT_RESOLVED_PATH = "duration-budget-resolved.md"
_TimingSource = tuple[str, Callable[[Path], dict[str, float]], Path]
ATTENTION_STATUSES = frozenset({Status.WARN, Status.FAIL, Status.CONTENTION, Status.UNBUDGETED})


def _job_path(value: str) -> tuple[str, Path]:
    job, separator, path = value.rpartition("=")
    if not separator or not job or not path:
        raise argparse.ArgumentTypeError(f"expected JOB=PATH, got {value!r}")
    return job, Path(path)


def _parse_args(argv: Sequence[str] | None, environ: Mapping[str, str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m alan.ci_budget_check")
    parser.add_argument("--budgets", type=workspace_path, default=DEFAULT_BUDGETS_PATH)
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--repo", default=environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id", default=environ.get("GITHUB_RUN_ID", ""))
    api_url = environ.get("GITHUB_API_URL", jobs_api.DEFAULT_API_URL)
    parser.add_argument("--api-url", default=api_url)
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--junit", action="append", type=_job_path, default=[])
    parser.add_argument("--playwright", action="append", type=_job_path, default=[])
    parser.add_argument("--comment-path", type=workspace_path, default=DEFAULT_COMMENT_PATH)
    parser.add_argument("--resolved-path", type=workspace_path, default=DEFAULT_RESOLVED_PATH)
    return parser.parse_args(argv)


def _report_notice(prefix: str, exc: Exception) -> Report:
    return Report(notices=(f"{prefix}: {exc}",))


def _timing_sources(args: argparse.Namespace) -> list[_TimingSource]:
    junit_reports: list[tuple[str, Path]] = args.junit
    playwright_reports: list[tuple[str, Path]] = args.playwright
    sources: list[_TimingSource] = [(job, timings.load_junit, path) for job, path in junit_reports]
    sources.extend((job, timings.load_playwright, path) for job, path in playwright_reports)
    return sources


def _slowest(
    args: argparse.Namespace, failing: frozenset[str], changed: Sequence[str]
) -> dict[str, tuple[tuple[str, float], ...]]:
    return {
        job: tuple(timings.slowest_changed(load(path), changed))
        for job, load, path in _timing_sources(args)
        if job in failing
    }


def _slowest_for_failures(
    args: argparse.Namespace, token: str, verdicts: Sequence[Verdict]
) -> tuple[dict[str, tuple[tuple[str, float], ...]], tuple[str, ...]]:
    failing = frozenset(v.name for v in verdicts if v.status is Status.FAIL)
    if not failing or args.pr is None:
        return {}, ()
    try:
        changed = jobs_api.pull_files(args.api_url, args.repo, args.pr, token)
    except jobs_api.ApiUnavailableError as exc:
        return {}, (f"Changed test files unavailable, so no slowest-file list: {exc}",)
    return _slowest(args, failing, changed), ()


def check(args: argparse.Namespace, token: str) -> Report:
    try:
        budgets = load_budgets(args.budgets).get(args.workflow, {})
    except BudgetFileError as exc:
        return _report_notice("Budget file unusable, so no duration was checked", exc)
    try:
        jobs = jobs_api.run_jobs(args.api_url, args.repo, args.run_id, token)
    except jobs_api.ApiUnavailableError as exc:
        return _report_notice("Job durations unavailable, so nothing was checked", exc)
    verdicts = tuple(classify(jobs, budgets))
    slowest, notices = _slowest_for_failures(args, token, verdicts)
    return Report(verdicts, notices, slowest, args.workflow)


def needs_attention(report: Report) -> bool:
    return bool(report.notices) or any(v.status in ATTENTION_STATUSES for v in report.verdicts)


def _run_url(environ: Mapping[str, str], args: argparse.Namespace) -> str:
    server = environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{args.repo}/actions/runs/{args.run_id}"


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def write_outputs(report: Report, args: argparse.Namespace, environ: Mapping[str, str]) -> None:
    for line in render.annotation_lines(report):
        print(line)
    _append(environ.get("GITHUB_STEP_SUMMARY"), render.summary_markdown(report))
    run_url = _run_url(environ, args)
    if needs_attention(report):
        args.comment_path.write_text(render.comment_body(report, run_url), encoding="utf-8")
    else:
        args.resolved_path.write_text(render.resolved_comment_body(run_url), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    environ = os.environ
    args = _parse_args(argv, environ)
    report = check(args, environ.get("GITHUB_TOKEN", ""))
    write_outputs(report, args, environ)
    return 1 if budget_rules.FAIL_BLOCKS_PR and has_failure(report.verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())

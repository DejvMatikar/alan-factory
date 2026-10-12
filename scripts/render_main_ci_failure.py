"""Render the GitHub issue body for a failed main-branch CI run.

Invoked by the `main-red-report` job in `.github/workflows/tests.yml`, only
when the push-to-main run has at least one failed job. Reads the failing job
names and their structured check-run annotations straight from the GitHub
REST API — the run's own `/actions/runs/{id}/jobs` list, then each failed
job's `/check-runs/{id}/annotations` (an Actions job id and a Checks API
check-run id are the same numeric id) — never raw log text, plus the
originating PR from the merge commit's `/commits/{sha}/pulls` endpoint. Any
network, auth, or parse fault degrades the affected field to "unknown" rather
than crashing the job: the run is already red from the test jobs in that
case, and this reporter must not itself go red and suppress the finding it
exists to file.

The job carries a 5-minute `timeout-minutes`. `collect_annotations` can issue
one sequential request per failing job on top of the jobs-list and
originating-PR calls, so both the per-request timeout (`_REQUEST_TIMEOUT_S`)
and the aggregate annotation-collection budget (`_ANNOTATIONS_BUDGET_S`) stay
well under that ceiling — a partial API degradation across every failing job
must still leave enough of the job's budget to write the body and file the
issue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

_JsonObject = dict[str, object]
_DEFAULT_API_URL = "https://api.github.com"
_MAX_ANNOTATIONS = 20
_MAX_BODY_CHARS = 50_000
_TRUNCATION_MARKER = "\n\n[truncated — see the run for full detail]"
_REQUEST_TIMEOUT_S = 10
_ANNOTATIONS_BUDGET_S = 60.0
_FAILURE_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "startup_failure"})


def _get_json(api_url: str, token: str, path: str) -> object | None:
    request = urllib.request.Request(  # noqa: S310 - fixed api_url + path built from GitHub context, not user input
        f"{api_url}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_S) as response:  # noqa: S310
            return cast("object", json.loads(response.read()))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def failing_jobs(api_url: str, repo: str, run_id: str, token: str) -> list[tuple[int, str]]:
    data = _get_json(api_url, token, f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(data, dict):
        return []
    jobs = cast(_JsonObject, data).get("jobs")
    if not isinstance(jobs, list):
        return []
    found: list[tuple[int, str]] = []
    for job in cast("list[object]", jobs):
        if not isinstance(job, dict):
            continue
        job_obj = cast(_JsonObject, job)
        if job_obj.get("conclusion") not in _FAILURE_CONCLUSIONS:
            continue
        job_id = job_obj.get("id")
        name = job_obj.get("name")
        if isinstance(job_id, int) and isinstance(name, str):
            found.append((job_id, name))
    return found


def originating_pr(api_url: str, repo: str, sha: str, token: str) -> tuple[int, str] | None:
    data = _get_json(api_url, token, f"/repos/{repo}/commits/{sha}/pulls")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    first_obj = cast(_JsonObject, first)
    number = first_obj.get("number")
    html_url = first_obj.get("html_url")
    if isinstance(number, int) and isinstance(html_url, str):
        return (number, html_url)
    return None


def job_annotations(api_url: str, repo: str, token: str, job_id: int) -> list[_JsonObject]:
    data = _get_json(api_url, token, f"/repos/{repo}/check-runs/{job_id}/annotations?per_page=50")
    if not isinstance(data, list):
        return []
    return [cast(_JsonObject, a) for a in cast("list[object]", data) if isinstance(a, dict)]


def collect_annotations(
    api_url: str, repo: str, token: str, jobs: list[tuple[int, str]]
) -> list[_JsonObject]:
    collected: list[_JsonObject] = []
    deadline = time.monotonic() + _ANNOTATIONS_BUDGET_S
    for job_id, _name in jobs:
        if time.monotonic() >= deadline:
            break
        collected.extend(job_annotations(api_url, repo, token, job_id))
        if len(collected) >= _MAX_ANNOTATIONS:
            break
    return collected[:_MAX_ANNOTATIONS]


def _annotation_line(annotation: _JsonObject) -> str:
    level = annotation.get("annotation_level") or "notice"
    path = annotation.get("path") or "?"
    line = annotation.get("start_line")
    message = str(annotation.get("message") or "").strip() or "(no message)"
    location = f"{path}:{line}" if line is not None else str(path)
    return f"- **{level}** `{location}` — {message}"


def _job_lines(job_names: list[str]) -> list[str]:
    if not job_names:
        return ["- (job list unavailable — see the run)"]
    return [f"- {name}" for name in job_names]


def _annotation_lines(annotations: list[_JsonObject]) -> list[str]:
    if not annotations:
        return ["- (none reported)"]
    return [_annotation_line(a) for a in annotations]


def _pr_line(pr: tuple[int, str] | None) -> str:
    if pr is None:
        return "unknown"
    number, html_url = pr
    return f"#{number} — {html_url}"


def render_title(job_names: list[str]) -> str:
    if not job_names:
        return "Main-branch CI failed"
    if len(job_names) == 1:
        return f"Main-branch CI failed: {job_names[0]}"
    return f"Main-branch CI failed: {job_names[0]} (+{len(job_names) - 1} more)"


def render_body(
    *,
    job_names: list[str],
    sha: str,
    run_url: str,
    pr: tuple[int, str] | None,
    annotations: list[_JsonObject],
) -> str:
    lines = [
        "The main-branch test suite failed after a merge. This never blocked",
        "the deploy — it only failed to prove the merge is clean; see",
        "docs/architecture/main_red_issue_filing.md.",
        "",
        "## Failing jobs",
        *_job_lines(job_names),
        "",
        f"**Merge commit:** `{sha}`",
        f"**Originating PR:** {_pr_line(pr)}",
        f"**Run:** {run_url}",
        "",
        "## Annotations",
        *_annotation_lines(annotations),
    ]
    body = "\n".join(lines)
    if len(body) <= _MAX_BODY_CHARS:
        return body
    return body[: _MAX_BODY_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}"


def _write_github_output(pairs: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-path", required=True)
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        raise SystemExit(1) from exc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api_url = os.environ.get("GITHUB_API_URL", _DEFAULT_API_URL)
    token = os.environ.get("GITHUB_TOKEN", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    sha = os.environ.get("GITHUB_SHA", "")

    jobs = failing_jobs(api_url, repo, run_id, token)
    job_names = [name for _job_id, name in jobs]
    pr = originating_pr(api_url, repo, sha, token)
    annotations = collect_annotations(api_url, repo, token, jobs)

    body = render_body(
        job_names=job_names, sha=sha, run_url=_run_url(), pr=pr, annotations=annotations
    )
    Path(args.body_path).write_text(body, encoding="utf-8")
    _write_github_output({"title": render_title(job_names)})
    return 0


if __name__ == "__main__":
    sys.exit(main())

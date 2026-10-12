from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import cast

from alan._ci_drift import RunSample, pr_number
from alan.ci_budget import JobTiming, StepTiming, measured_duration_s

DEFAULT_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_S = 15
FILES_PER_PAGE = 100
MAX_FILE_PAGES = 3
CHANGED_STATUSES = frozenset({"added", "modified", "renamed", "changed"})

_REPO_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
_DOT_SEGMENTS = frozenset({".", ".."})

_JsonObject = dict[str, object]


class ApiUnavailableError(Exception):
    pass


def _base_url(api_url: str) -> str:
    if api_url.rstrip("/") != DEFAULT_API_URL:
        raise ApiUnavailableError(f"refusing to send the token to {api_url!r}")
    return DEFAULT_API_URL


def _repo_path(repo: str) -> str:
    owner, _, name = repo.partition("/")
    parts = (owner, name)
    if not all(_REPO_SEGMENT.fullmatch(part) and part not in _DOT_SEGMENTS for part in parts):
        raise ApiUnavailableError(f"invalid repository {repo!r}")
    return f"{owner}/{name}"


def get_json(api_url: str, token: str, path: str) -> object:
    request = urllib.request.Request(  # noqa: S310
        f"{_base_url(api_url)}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # noqa: S310  # nosec B310
            return cast("object", json.loads(response.read()))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ApiUnavailableError(f"{path}: {exc}") from exc


def _objects(data: object, key: str | None) -> list[_JsonObject]:
    container = data
    if key is not None:
        container = cast(_JsonObject, data).get(key) if isinstance(data, dict) else None
    if not isinstance(container, list):
        raise ApiUnavailableError("unexpected response shape")
    items = cast("list[object]", container)
    return [cast(_JsonObject, item) for item in items if isinstance(item, dict)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _step_timing(step: _JsonObject) -> StepTiming:
    return StepTiming(
        _text(step.get("name")) or "",
        _text(step.get("started_at")),
        _text(step.get("completed_at")),
    )


def _steps(job: _JsonObject) -> tuple[StepTiming, ...]:
    raw = job.get("steps")
    if not isinstance(raw, list):
        return ()
    steps = cast("list[object]", raw)
    return tuple(_step_timing(cast(_JsonObject, s)) for s in steps if isinstance(s, dict))


def _job_timing(job: _JsonObject) -> JobTiming:
    return JobTiming(
        _text(job.get("name")) or "",
        _text(job.get("conclusion")),
        _text(job.get("started_at")),
        _text(job.get("completed_at")),
        _steps(job),
    )


def run_jobs(api_url: str, repo: str, run_id: str | int, token: str) -> list[JobTiming]:
    run = urllib.parse.quote(str(run_id), safe="")
    path = f"/repos/{_repo_path(repo)}/actions/runs/{run}/jobs?filter=latest&per_page=100"
    return [_job_timing(job) for job in _objects(get_json(api_url, token, path), "jobs")]


def _changed_names(files: list[_JsonObject]) -> list[str]:
    return [
        name
        for item in files
        if item.get("status") in CHANGED_STATUSES
        and (name := _text(item.get("filename"))) is not None
    ]


def pull_files(api_url: str, repo: str, pr: int, token: str) -> list[str]:
    names: list[str] = []
    for page in range(1, MAX_FILE_PAGES + 1):
        path = f"/repos/{_repo_path(repo)}/pulls/{pr}/files?per_page={FILES_PER_PAGE}&page={page}"
        files = _objects(get_json(api_url, token, path), None)
        names.extend(_changed_names(files))
        if len(files) < FILES_PER_PAGE:
            break
    return names


def workflow_runs(
    api_url: str, repo: str, workflow: str, token: str, limit: int
) -> list[_JsonObject]:
    path = (
        f"/repos/{_repo_path(repo)}/actions/workflows/{urllib.parse.quote(workflow, safe='')}/runs"
        f"?branch=main&status=success&exclude_pull_requests=true&per_page={limit}"
    )
    return list(reversed(_objects(get_json(api_url, token, path), "workflow_runs")))


def _head_message(run: _JsonObject) -> str:
    commit = run.get("head_commit")
    if not isinstance(commit, dict):
        return ""
    return _text(cast(_JsonObject, commit).get("message")) or ""


def _run_id(run: _JsonObject) -> int:
    value = run.get("id")
    if isinstance(value, int):
        return value
    raise ApiUnavailableError("workflow run without a numeric id")


def run_sample(run: _JsonObject, jobs: list[JobTiming]) -> RunSample:
    durations: dict[str, float] = {}
    for job in jobs:
        duration = measured_duration_s(job)
        if duration is not None:
            durations[job.name] = duration
    date = _text(run.get("run_started_at")) or _text(run.get("created_at")) or ""
    return RunSample(_run_id(run), date, pr_number(_head_message(run)), durations)


def main_samples(api_url: str, repo: str, workflow: str, token: str, limit: int) -> list[RunSample]:
    return [
        run_sample(run, run_jobs(api_url, repo, _run_id(run), token))
        for run in workflow_runs(api_url, repo, workflow, token, limit)
    ]

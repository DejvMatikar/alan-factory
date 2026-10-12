from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

import pytest

from alan import _ci_jobs_api as api
from alan.ci_budget import JobTiming


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _serve(monkeypatch: pytest.MonkeyPatch, routes: Callable[[str], object]) -> list[str]:
    seen: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen.append(request.full_url)
        return _FakeResponse(json.dumps(routes(request.full_url)).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _raise(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _job(name: str, started: str, completed: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": "success",
        "started_at": started,
        "completed_at": completed,
        **extra,
    }


def test_get_json_sends_the_token_and_returns_the_parsed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        captured.append(request)
        assert timeout == api.REQUEST_TIMEOUT_S
        return _FakeResponse(b'{"a": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert api.get_json("https://api.github.com", "tok", "/x") == {"a": 1}
    assert captured[0].full_url == "https://api.github.com/x"
    assert captured[0].get_header("Authorization") == "Bearer tok"


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("network exploded"),
        urllib.error.HTTPError("https://x", 403, "Forbidden", Message(), io.BytesIO(b"")),
        urllib.error.HTTPError("https://x", 429, "Too Many Requests", Message(), io.BytesIO(b"")),
        TimeoutError("slow"),
        OSError("reset"),
    ],
    ids=["url-error", "403", "429", "timeout", "os-error"],
)
def test_get_json_reports_every_transport_failure_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    _raise(monkeypatch, error)
    with pytest.raises(api.ApiUnavailableError, match="/x"):
        api.get_json("https://api.github.com", "tok", "/x")


def test_get_json_reports_invalid_json_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: _FakeResponse(b"<html>")
    )
    with pytest.raises(api.ApiUnavailableError):
        api.get_json("https://api.github.com", "tok", "/x")


@pytest.mark.parametrize(
    "api_url",
    [
        "https://attacker.example",
        "http://api.github.com",
        "https://api.github.com.attacker.example",
        "https://api.github.com@attacker.example",
        "",
    ],
)
def test_get_json_never_sends_the_token_to_another_host(
    monkeypatch: pytest.MonkeyPatch, api_url: str
) -> None:
    seen = _serve(monkeypatch, lambda url: {})
    with pytest.raises(api.ApiUnavailableError, match="refusing to send the token"):
        api.get_json(api_url, "tok", "/x")
    assert seen == []


def test_get_json_accepts_a_trailing_slash_on_the_default_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _serve(monkeypatch, lambda url: {"a": 1})
    assert api.get_json("https://api.github.com/", "tok", "/x") == {"a": 1}
    assert seen == ["https://api.github.com/x"]


@pytest.mark.parametrize("repo", ["", "owner", "a/b/c", "../x", "a/..", "./b", "a/b c", "a/b?x=1"])
def test_every_endpoint_rejects_a_malformed_repository(
    monkeypatch: pytest.MonkeyPatch, repo: str
) -> None:
    seen = _serve(monkeypatch, lambda url: {})
    url = "https://api.github.com"
    for call in (
        lambda: api.run_jobs(url, repo, 1, "tok"),
        lambda: api.pull_files(url, repo, 1, "tok"),
        lambda: api.workflow_runs(url, repo, "tests.yml", "tok", 7),
    ):
        with pytest.raises(api.ApiUnavailableError, match="invalid repository"):
            call()
    assert seen == []


def test_run_jobs_and_workflow_runs_quote_path_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _serve(monkeypatch, lambda url: {"jobs": [], "workflow_runs": []})
    api.run_jobs("https://api.github.com", ".github/x", "1/../2", "tok")
    api.workflow_runs("https://api.github.com", "o/r", "../x.yml", "tok", 1)
    assert seen[0].startswith("https://api.github.com/repos/.github/x/actions/runs/1%2F..%2F2/jobs")
    assert "/workflows/..%2Fx.yml/runs" in seen[1]


def test_run_jobs_asks_for_the_latest_attempt_and_parses_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "jobs": [
            _job(
                "Backend tests + coverage",
                "2026-09-29T10:00:00Z",
                "2026-09-29T10:02:20Z",
                steps=[
                    {
                        "name": "Run tests",
                        "started_at": "2026-09-29T10:00:10Z",
                        "completed_at": None,
                    },
                    "not-a-step",
                ],
            ),
            {"name": None, "conclusion": None, "steps": "nope"},
            "not-a-job",
        ]
    }
    seen = _serve(monkeypatch, lambda url: payload)
    jobs = api.run_jobs("https://api.github.com", "o/r", 42, "tok")
    assert seen == [
        "https://api.github.com/repos/o/r/actions/runs/42/jobs?filter=latest&per_page=100"
    ]
    assert [job.name for job in jobs] == ["Backend tests + coverage", ""]
    assert jobs[0].steps[0].name == "Run tests"
    assert jobs[0].steps[0].completed_at is None
    assert len(jobs[0].steps) == 1
    assert jobs[1].conclusion is None
    assert jobs[1].steps == ()


@pytest.mark.parametrize("payload", [{"jobs": "nope"}, ["not", "a", "dict"], {"other": []}])
def test_run_jobs_rejects_an_unexpected_shape(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    _serve(monkeypatch, lambda url: payload)
    with pytest.raises(api.ApiUnavailableError, match="unexpected response shape"):
        api.run_jobs("https://api.github.com", "o/r", 1, "tok")


def _files_route(pages: dict[int, list[dict[str, str]]]) -> Callable[[str], object]:
    def route(url: str) -> object:
        page = int(url.rsplit("page=", 1)[1])
        return pages.get(page, [])

    return route


def test_pull_files_keeps_added_modified_and_renamed_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = [
        {"filename": "tests/test_new.py", "status": "added"},
        {"filename": "tests/test_old.py", "status": "modified"},
        {"filename": "tests/test_renamed.py", "status": "renamed"},
        {"filename": "tests/test_gone.py", "status": "removed"},
        {"status": "added"},
    ]
    _serve(monkeypatch, _files_route({1: files}))
    assert api.pull_files("https://api.github.com", "o/r", 7, "tok") == [
        "tests/test_new.py",
        "tests/test_old.py",
        "tests/test_renamed.py",
    ]


def test_pull_files_pages_until_a_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    full = [{"filename": f"f{i}.py", "status": "added"} for i in range(api.FILES_PER_PAGE)]
    seen = _serve(
        monkeypatch, _files_route({1: full, 2: [{"filename": "last.py", "status": "added"}]})
    )
    names = api.pull_files("https://api.github.com", "o/r", 7, "tok")
    assert len(names) == api.FILES_PER_PAGE + 1
    assert names[-1] == "last.py"
    assert len(seen) == 2


def test_pull_files_stops_at_the_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    full = [{"filename": f"f{i}.py", "status": "added"} for i in range(api.FILES_PER_PAGE)]
    seen = _serve(monkeypatch, lambda url: full)
    api.pull_files("https://api.github.com", "o/r", 7, "tok")
    assert len(seen) == api.MAX_FILE_PAGES


def test_workflow_runs_are_returned_oldest_first_from_successful_main_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"workflow_runs": [{"id": 3}, {"id": 2}, {"id": 1}]}
    seen = _serve(monkeypatch, lambda url: payload)
    runs = api.workflow_runs("https://api.github.com", "o/r", "ci deploy.yml", "tok", 14)
    assert [run["id"] for run in runs] == [1, 2, 3]
    assert seen == [
        "https://api.github.com/repos/o/r/actions/workflows/ci%20deploy.yml/runs"
        "?branch=main&status=success&exclude_pull_requests=true&per_page=14"
    ]


def test_run_sample_measures_success_jobs_and_reads_the_pr_from_the_head_commit() -> None:
    run = {
        "id": 9,
        "run_started_at": "2026-09-27T04:00:00Z",
        "head_commit": {"message": "Apply, restart and verify a self-update (#1847)\n\nbody"},
    }
    timings = [
        JobTiming(
            "Backend tests + coverage", "success", "2026-09-27T04:00:00Z", "2026-09-27T04:03:00Z"
        ),
        JobTiming("Skipped one", "skipped", None, None),
    ]
    sample = api.run_sample(run, timings)
    assert (sample.run_id, sample.pr, sample.date) == (9, 1847, "2026-09-27T04:00:00Z")
    assert dict(sample.durations) == {"Backend tests + coverage": 180.0}


def test_run_sample_falls_back_to_created_at_and_tolerates_a_missing_head_commit() -> None:
    sample = api.run_sample({"id": 1, "created_at": "2026-09-27T05:00:00Z"}, [])
    assert (sample.date, sample.pr) == ("2026-09-27T05:00:00Z", None)


def test_run_sample_tolerates_a_head_commit_without_a_message() -> None:
    sample = api.run_sample({"id": 1, "head_commit": {"message": 5}}, [])
    assert (sample.date, sample.pr) == ("", None)


def test_a_workflow_run_without_a_numeric_id_is_unavailable_data() -> None:
    with pytest.raises(api.ApiUnavailableError, match="numeric id"):
        api.run_sample({"id": "x"}, [])


def test_main_samples_fetches_each_runs_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    def route(url: str) -> object:
        if "/workflows/" in url:
            return {
                "workflow_runs": [
                    {"id": 2, "head_commit": {"message": "second (#2)"}},
                    {"id": 1, "head_commit": {"message": "first (#1)"}},
                ]
            }
        run_id = int(url.split("/runs/")[1].split("/")[0])
        return {"jobs": [_job("job", "2026-09-27T04:00:00Z", f"2026-09-27T04:0{run_id}:00Z")]}

    _serve(monkeypatch, route)
    samples = api.main_samples("https://api.github.com", "o/r", "tests.yml", "tok", 14)
    assert [(s.run_id, s.pr, dict(s.durations)) for s in samples] == [
        (1, 1, {"job": 60.0}),
        (2, 2, {"job": 120.0}),
    ]

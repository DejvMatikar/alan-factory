from __future__ import annotations

import sys
from pathlib import Path
from urllib.error import URLError

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import render_main_ci_failure as rmcf  # type: ignore[import-not-found]  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_get_json_returns_parsed_body_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rmcf.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(b'{"a": 1}')
    )
    assert rmcf._get_json("https://api.github.com", "tok", "/x") == {"a": 1}


def test_get_json_returns_none_on_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **kw: object) -> None:
        raise URLError("network exploded")

    monkeypatch.setattr(rmcf.urllib.request, "urlopen", _boom)
    assert rmcf._get_json("https://api.github.com", "tok", "/x") is None


def test_get_json_returns_none_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(b"not json"))
    assert rmcf._get_json("https://api.github.com", "tok", "/x") is None


def test_failing_jobs_returns_empty_list_when_data_not_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: ["not", "a", "dict"])
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == []


def test_failing_jobs_returns_empty_list_when_jobs_not_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": "not-a-list"})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == []


def test_failing_jobs_skips_non_dict_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": ["not-a-dict"]})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == []


def test_failing_jobs_skips_non_failure_conclusions(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        {"id": 1, "name": "ok-job", "conclusion": "success"},
        {"id": 2, "name": "skipped-job", "conclusion": "skipped"},
        {"id": 3, "name": "still-running", "conclusion": None},
    ]
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": jobs})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == []


def test_failing_jobs_skips_entries_missing_id_or_name(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        {"id": "not-an-int", "name": "bad-id", "conclusion": "failure"},
        {"id": 4, "name": None, "conclusion": "failure"},
    ]
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": jobs})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == []


def test_failing_jobs_returns_id_name_tuples_for_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        {"id": 10, "name": "Backend tests + coverage", "conclusion": "failure"},
        {"id": 11, "name": "ok-job", "conclusion": "success"},
        {"id": 12, "name": "Python lint", "conclusion": "failure"},
    ]
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": jobs})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == [
        (10, "Backend tests + coverage"),
        (12, "Python lint"),
    ]


def test_failing_jobs_includes_timed_out_startup_failure_and_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = [
        {"id": 20, "name": "timed-out-job", "conclusion": "timed_out"},
        {"id": 21, "name": "startup-failure-job", "conclusion": "startup_failure"},
        {"id": 22, "name": "cancelled-job", "conclusion": "cancelled"},
    ]
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"jobs": jobs})
    assert rmcf.failing_jobs("api", "r/r", "1", "tok") == [
        (20, "timed-out-job"),
        (21, "startup-failure-job"),
        (22, "cancelled-job"),
    ]


def test_originating_pr_returns_none_when_data_not_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: {"not": "a-list"})
    assert rmcf.originating_pr("api", "r/r", "sha", "tok") is None


def test_originating_pr_returns_none_when_list_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: [])
    assert rmcf.originating_pr("api", "r/r", "sha", "tok") is None


def test_originating_pr_returns_none_when_first_not_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: ["not-a-dict"])
    assert rmcf.originating_pr("api", "r/r", "sha", "tok") is None


def test_originating_pr_returns_none_when_fields_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: [{"number": "not-int"}])
    assert rmcf.originating_pr("api", "r/r", "sha", "tok") is None


def test_originating_pr_returns_tuple_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rmcf,
        "_get_json",
        lambda *a: [{"number": 1309, "html_url": "https://github.com/o/r/pull/1309"}],
    )
    assert rmcf.originating_pr("api", "r/r", "sha", "tok") == (
        1309,
        "https://github.com/o/r/pull/1309",
    )


def test_job_annotations_returns_empty_when_not_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: None)
    assert rmcf.job_annotations("api", "r/r", "tok", 1) == []


def test_job_annotations_filters_non_dict_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: [{"message": "m"}, "not-a-dict"])
    assert rmcf.job_annotations("api", "r/r", "tok", 1) == [{"message": "m"}]


def test_collect_annotations_returns_empty_for_no_jobs() -> None:
    assert rmcf.collect_annotations("api", "r/r", "tok", []) == []


def test_collect_annotations_gathers_across_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    per_job = {
        1: [{"message": "a"}],
        2: [{"message": "b"}, {"message": "c"}],
    }
    monkeypatch.setattr(rmcf, "job_annotations", lambda *a: per_job[a[-1]])
    result = rmcf.collect_annotations("api", "r/r", "tok", [(1, "job-a"), (2, "job-b")])
    assert result == [{"message": "a"}, {"message": "b"}, {"message": "c"}]


def test_collect_annotations_stops_once_max_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    big_batch = [{"message": str(i)} for i in range(rmcf._MAX_ANNOTATIONS)]
    monkeypatch.setattr(
        rmcf,
        "job_annotations",
        lambda *a: big_batch if a[-1] == 1 else [{"message": "never"}],
    )
    result = rmcf.collect_annotations("api", "r/r", "tok", [(1, "job-a"), (2, "job-b")])
    assert len(result) == rmcf._MAX_ANNOTATIONS
    assert all(a["message"] != "never" for a in result)


def test_collect_annotations_stops_when_deadline_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = iter([0.0, 1.0, 1000.0])
    monkeypatch.setattr(rmcf.time, "monotonic", lambda: next(readings))
    monkeypatch.setattr(
        rmcf,
        "job_annotations",
        lambda *a: [{"message": f"job-{a[-1]}"}],
    )
    result = rmcf.collect_annotations("api", "r/r", "tok", [(1, "job-a"), (2, "job-b")])
    assert result == [{"message": "job-1"}]


def test_annotation_line_with_line_number() -> None:
    line = rmcf._annotation_line(
        {
            "annotation_level": "failure",
            "path": "tests/test_x.py",
            "start_line": 12,
            "message": "boom",
        }
    )
    assert line == "- **failure** `tests/test_x.py:12` — boom"


def test_annotation_line_without_line_number() -> None:
    line = rmcf._annotation_line(
        {"annotation_level": "failure", "path": "some/file", "message": "m"}
    )
    assert line == "- **failure** `some/file` — m"


def test_annotation_line_defaults_missing_level_path_and_message() -> None:
    assert rmcf._annotation_line({}) == "- **notice** `?` — (no message)"


def test_job_lines_empty() -> None:
    assert rmcf._job_lines([]) == ["- (job list unavailable — see the run)"]


def test_job_lines_nonempty() -> None:
    assert rmcf._job_lines(["a", "b"]) == ["- a", "- b"]


def test_annotation_lines_empty() -> None:
    assert rmcf._annotation_lines([]) == ["- (none reported)"]


def test_annotation_lines_nonempty() -> None:
    assert rmcf._annotation_lines([{"message": "m"}]) == ["- **notice** `?` — m"]


def test_pr_line_none() -> None:
    assert rmcf._pr_line(None) == "unknown"


def test_pr_line_present() -> None:
    assert rmcf._pr_line((7, "https://github.com/o/r/pull/7")) == "#7 — https://github.com/o/r/pull/7"


def test_render_title_no_failures() -> None:
    assert rmcf.render_title([]) == "Main-branch CI failed"


def test_render_title_single_failure() -> None:
    assert rmcf.render_title(["Backend tests"]) == "Main-branch CI failed: Backend tests"


def test_render_title_multiple_failures() -> None:
    assert rmcf.render_title(["a", "b", "c"]) == "Main-branch CI failed: a (+2 more)"


def test_render_body_includes_all_fields() -> None:
    body = rmcf.render_body(
        job_names=["Backend tests + coverage"],
        sha="abc123",
        run_url="https://github.com/o/r/actions/runs/1",
        pr=(1309, "https://github.com/o/r/pull/1309"),
        annotations=[{"annotation_level": "failure", "path": "x.py", "message": "boom"}],
    )
    assert "Backend tests + coverage" in body
    assert "`abc123`" in body
    assert "#1309" in body
    assert "https://github.com/o/r/actions/runs/1" in body
    assert "boom" in body


def test_render_body_truncates_when_oversized() -> None:
    huge_annotations = [{"message": "x" * 5000} for _ in range(rmcf._MAX_ANNOTATIONS)]
    body = rmcf.render_body(
        job_names=["job"],
        sha="abc123",
        run_url="https://example.invalid",
        pr=None,
        annotations=huge_annotations,
    )
    assert len(body) == rmcf._MAX_BODY_CHARS
    assert body.endswith(rmcf._TRUNCATION_MARKER)


def test_run_url_builds_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    assert rmcf._run_url() == "https://github.com/o/r/actions/runs/42"


def test_run_url_uses_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert rmcf._run_url() == "https://github.com//actions/runs/"


def test_write_github_output_noop_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    rmcf._write_github_output({"title": "x"})


def test_write_github_output_writes_pairs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    rmcf._write_github_output({"title": "Main-branch CI failed: x"})
    assert output.read_text(encoding="utf-8") == "title=Main-branch CI failed: x\n"


def test_parse_args_requires_body_path() -> None:
    with pytest.raises(SystemExit) as exc_info:
        rmcf._parse_args([])
    assert exc_info.value.code == 1


def test_parse_args_reraises_help_exit_unchanged() -> None:
    with pytest.raises(SystemExit) as exc_info:
        rmcf._parse_args(["--help"])
    assert exc_info.value.code == 0


def test_main_writes_body_and_title_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "DejvMatikar/alan")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    monkeypatch.setattr(rmcf, "failing_jobs", lambda *a: [(1, "Backend tests + coverage")])
    monkeypatch.setattr(
        rmcf,
        "originating_pr",
        lambda *a: (1309, "https://github.com/DejvMatikar/alan/pull/1309"),
    )
    monkeypatch.setattr(
        rmcf,
        "collect_annotations",
        lambda *a: [{"annotation_level": "failure", "path": "x.py", "message": "boom"}],
    )

    body_path = tmp_path / "body.md"
    rc = rmcf.main(["--body-path", str(body_path)])
    assert rc == 0
    body = body_path.read_text(encoding="utf-8")
    assert "Backend tests + coverage" in body
    assert "#1309" in body
    assert "boom" in body
    assert output.read_text(encoding="utf-8") == (
        "title=Main-branch CI failed: Backend tests + coverage\n"
    )


def test_main_degrades_gracefully_when_api_calls_all_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "DejvMatikar/alan")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(rmcf, "_get_json", lambda *a: None)

    body_path = tmp_path / "body.md"
    rc = rmcf.main(["--body-path", str(body_path)])
    assert rc == 0
    body = body_path.read_text(encoding="utf-8")
    assert "job list unavailable" in body
    assert "unknown" in body
    assert "none reported" in body

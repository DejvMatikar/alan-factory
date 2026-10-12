from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

import pytest

from alan import _ci_jobs_api as jobs_api
from alan import ci_budget as cb
from alan import ci_budget_check as check
from alan.ci_budget import Report, Status, Verdict

_START = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
_BACKEND = "Backend tests + coverage"
_E2E = "Frontend E2E (Playwright, blocking)"
_STRESS = "Stress changed specs (repeat-each 5, no retries)"
_JUNIT = """<testsuite>
<testcase classname="tests.test_big" name="a" time="42.0"/>
<testcase classname="tests.test_small" name="b" time="3.0"/>
<testcase classname="tests.test_untouched" name="c" time="90.0"/>
</testsuite>"""
_PLAYWRIGHT = {
    "suites": [
        {"file": "specs/a.spec.ts", "specs": [{"tests": [{"results": [{"duration": 5000}]}]}]}
    ]
}


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _stamp(offset_s: float) -> str:
    return (_START + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(
    name: str, seconds: float, conclusion: str = "success", steps: list[object] | None = None
) -> dict[str, object]:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": _stamp(0),
        "completed_at": _stamp(seconds),
        "steps": steps or [],
    }


def _api(
    monkeypatch: pytest.MonkeyPatch,
    jobs: list[dict[str, object]],
    files: list[dict[str, str]] | None = None,
) -> list[str]:
    seen: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen.append(request.full_url)
        if "/pulls/" in request.full_url:
            return _FakeResponse(json.dumps(files or []).encode())
        return _FakeResponse(json.dumps({"jobs": jobs}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _failing_api(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _budget_file(tmp_path: Path, budgets: dict[str, dict[str, float]] | None = None) -> Path:
    path = tmp_path / "budgets.json"
    payload = (
        budgets
        if budgets is not None
        else {"tests.yml": {_BACKEND: 100, "Peer A": 100, "Peer B": 100, "Peer C": 100, _E2E: 100}}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Run:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.summary = tmp_path / "summary.md"
        self.comment = tmp_path / "comment.md"
        self.resolved = tmp_path / "resolved.md"
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.setenv("GITHUB_RUN_ID", "555")
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(self.summary))
        monkeypatch.delenv("GITHUB_API_URL", raising=False)
        monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)

    def main(self, *extra: str, budgets: Path | None = None) -> int:
        argv = [
            "--budgets",
            str(budgets or _budget_file(self.tmp_path)),
            "--comment-path",
            str(self.comment),
            "--resolved-path",
            str(self.resolved),
            *extra,
        ]
        return check.main(argv)


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Run:
    return _Run(tmp_path, monkeypatch)


@pytest.fixture
def blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb, "FAIL_BLOCKS_PR", True)


def _peers(seconds: float) -> list[dict[str, object]]:
    return [_job(name, seconds) for name in ("Peer A", "Peer B", "Peer C")]


def test_a_run_within_budget_exits_zero_and_writes_only_the_resolved_comment(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 100), *_peers(100)])
    assert run.main() == 0
    assert capsys.readouterr().out == ""
    assert not run.comment.exists()
    assert run.resolved.read_text(encoding="utf-8").startswith("<!-- ci-duration-budget -->")
    assert "| Backend tests + coverage | 100s | 100s |" in run.summary.read_text(encoding="utf-8")


def test_the_jobs_api_is_asked_for_the_run_in_the_environment(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _api(monkeypatch, [_job(_BACKEND, 100)])
    run.main()
    assert seen == [
        "https://api.github.com/repos/o/r/actions/runs/555/jobs?filter=latest&per_page=100"
    ]


def test_a_warn_exits_zero_with_a_warning_annotation_and_a_comment(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 130), *_peers(100)])
    assert run.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning title=CI duration budget::Backend tests + coverage")
    assert run.comment.read_text(encoding="utf-8").startswith("<!-- ci-duration-budget -->")
    assert not run.resolved.exists()


@pytest.mark.usefixtures("blocking")
def test_a_blocking_fail_exits_one_and_names_job_budget_measured_and_difference(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 200), *_peers(100)])
    assert run.main() == 1
    out = capsys.readouterr().out
    assert out.startswith("::error title=CI duration budget::")
    assert "Backend tests + coverage: measured 200s against a budget of 100s" in out
    assert "(+100s (+100%25))" in out
    assert "Do not raise the budget" in out
    assert "A rerun will not change this verdict" in out
    assert "(+100s (+100%))" in run.comment.read_text(encoding="utf-8")


def test_an_advisory_fail_exits_zero_with_a_warning_and_the_advisory_note(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 200), *_peers(100)])
    assert run.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning title=CI duration budget::")
    assert "::error" not in out
    assert "advisory while its thresholds are calibrated" in out
    assert "advisory while its thresholds are calibrated" in run.comment.read_text(encoding="utf-8")


@pytest.mark.usefixtures("blocking")
def test_a_fail_lists_the_slowest_changed_test_files_of_the_failing_test_job(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = run.tmp_path / "junit.xml"
    junit.write_text(_JUNIT, encoding="utf-8")
    files = [
        {"filename": "tests/test_big.py", "status": "added"},
        {"filename": "tests/test_small.py", "status": "modified"},
        {"filename": "alan/foo.py", "status": "modified"},
    ]
    seen = _api(monkeypatch, [_job(_BACKEND, 200), *_peers(100)], files)
    code = run.main("--pr", "7", "--junit", f"{_BACKEND}={junit}")
    out = capsys.readouterr().out
    assert code == 1
    assert "`tests/test_big.py` (42.0s), `tests/test_small.py` (3.0s)" in out
    assert "test_untouched" not in out
    assert any("/pulls/7/files" in url for url in seen)


@pytest.mark.usefixtures("blocking")
def test_a_failing_job_without_a_timings_source_has_no_slowest_section(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = run.tmp_path / "junit.xml"
    junit.write_text(_JUNIT, encoding="utf-8")
    _api(monkeypatch, [_job(_E2E, 200), *_peers(100)], [])
    assert run.main("--pr", "7", "--junit", f"{_BACKEND}={junit}") == 1
    assert "Slowest test files" not in capsys.readouterr().out


@pytest.mark.usefixtures("blocking")
def test_playwright_timings_are_read_for_the_e2e_job(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = run.tmp_path / "results.json"
    report.write_text(json.dumps(_PLAYWRIGHT), encoding="utf-8")
    files = [{"filename": "alan/web/e2e/specs/a.spec.ts", "status": "modified"}]
    _api(monkeypatch, [_job(_E2E, 200), *_peers(100)], files)
    assert run.main("--pr", "7", "--playwright", f"{_E2E}={report}") == 1
    assert "`alan/web/e2e/specs/a.spec.ts` (5.0s)" in capsys.readouterr().out


@pytest.mark.usefixtures("blocking")
def test_a_fail_without_a_pr_number_skips_the_changed_files_lookup(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _api(monkeypatch, [_job(_BACKEND, 200), *_peers(100)])
    assert run.main() == 1
    assert not any("/pulls/" in url for url in seen)


@pytest.mark.usefixtures("blocking")
def test_a_changed_files_outage_still_fails_the_pr_with_a_notice(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        if "/pulls/" in request.full_url:
            raise urllib.error.URLError("down")
        return _FakeResponse(json.dumps({"jobs": [_job(_BACKEND, 200), *_peers(100)]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert run.main("--pr", "7") == 1
    out = capsys.readouterr().out
    assert "Changed test files unavailable, so no slowest-file list" in out
    assert out.count("::error") == 1


def test_a_uniformly_slow_run_warns_about_contention_and_exits_zero(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 200), *_peers(150)])
    assert run.main() == 0
    out = capsys.readouterr().out
    assert "host contention is the likely cause" in out
    assert "::error" not in out


def test_the_pr_only_stress_step_is_not_charged_to_the_job(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stress = {"name": _STRESS, "started_at": _stamp(10), "completed_at": _stamp(110)}
    _api(monkeypatch, [_job(_E2E, 205, steps=[stress]), *_peers(100)])
    assert run.main() == 0
    assert "| Frontend E2E (Playwright, blocking) | 100s | 105s |" in run.summary.read_text(
        encoding="utf-8"
    )
    assert "::error" not in capsys.readouterr().out


def test_skipped_and_cancelled_jobs_are_listed_not_measured(
    run: _Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _api(
        monkeypatch,
        [_job(_BACKEND, 100), _job("Peer A", 500, "skipped"), _job("Peer B", 500, "cancelled")],
    )
    assert run.main() == 0
    summary = run.summary.read_text(encoding="utf-8")
    assert "not measured (skipped)" in summary
    assert "not measured (cancelled)" in summary


def test_a_job_without_a_budget_warns_and_asks_for_one(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job("Brand new job", 900)])
    assert run.main() == 0
    out = capsys.readouterr().out
    assert "no duration budget is committed" in out
    assert 'Add one under "tests.yml"' in out


def test_the_check_job_is_never_judged(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job("Duration budgets (advisory)", 900)])
    assert run.main() == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("network exploded"),
        urllib.error.HTTPError("https://x", 403, "Forbidden", Message(), io.BytesIO(b"")),
        urllib.error.HTTPError("https://x", 429, "Too Many Requests", Message(), io.BytesIO(b"")),
    ],
    ids=["outage", "403", "429"],
)
def test_an_unreadable_jobs_api_passes_with_a_visible_warning(
    run: _Run,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException,
) -> None:
    _failing_api(monkeypatch, error)
    assert run.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning title=CI duration budget::Job durations unavailable")
    assert run.comment.exists()
    assert "Job durations unavailable" in run.summary.read_text(encoding="utf-8")


def test_a_missing_budget_file_passes_with_a_visible_warning(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _api(monkeypatch, [_job(_BACKEND, 900)])
    assert run.main(budgets=run.tmp_path / "absent.json") == 0
    assert "Budget file unusable, so no duration was checked" in capsys.readouterr().out
    assert seen == []


def test_an_invalid_budget_file_passes_with_a_visible_warning(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 900)])
    bad = run.tmp_path / "bad.json"
    bad.write_text('{"tests.yml": {"job": -1}}', encoding="utf-8")
    assert run.main(budgets=bad) == 0
    assert "Budget file unusable" in capsys.readouterr().out


def test_a_workflow_missing_from_the_budget_file_leaves_every_job_unbudgeted(
    run: _Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, [_job(_BACKEND, 900)])
    assert run.main(budgets=_budget_file(run.tmp_path, {"other.yml": {"x": 1}})) == 0
    assert "no duration budget is committed" in capsys.readouterr().out


def test_an_unexpected_exception_propagates(run: _Run, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object) -> object:
        raise RuntimeError("bug in the checker")

    monkeypatch.setattr(jobs_api, "run_jobs", explode)
    with pytest.raises(RuntimeError, match="bug in the checker"):
        run.main()


def test_the_summary_is_optional(run: _Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")
    _api(monkeypatch, [_job(_BACKEND, 100)])
    assert run.main() == 0
    assert not run.summary.exists()


def test_flags_override_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://ghe.example")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.other.example")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    seen = _api(monkeypatch, [_job(_BACKEND, 130)])
    code = check.main(
        [
            "--budgets",
            str(_budget_file(tmp_path)),
            "--workflow",
            "tests.yml",
            "--repo",
            "x/y",
            "--run-id",
            "9",
            "--api-url",
            "https://api.github.com",
            "--comment-path",
            str(tmp_path / "c.md"),
            "--resolved-path",
            str(tmp_path / "r.md"),
        ]
    )
    assert code == 0
    assert seen == [
        "https://api.github.com/repos/x/y/actions/runs/9/jobs?filter=latest&per_page=100"
    ]
    assert "https://ghe.example/x/y/actions/runs/9" in (tmp_path / "c.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("flag", ["--budgets", "--comment-path", "--resolved-path"])
def test_a_path_outside_the_workspace_is_a_usage_error(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    seen = _api(monkeypatch, [_job(_BACKEND, 100)])
    with pytest.raises(SystemExit) as excinfo:
        check.main([flag, str(tmp_path / "escape.txt")])
    assert excinfo.value.code == 2
    assert "invalid workspace_path value" in capsys.readouterr().err
    assert seen == []
    assert not (tmp_path / "escape.txt").exists()


def test_the_default_paths_resolve_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = check._parse_args([], {})
    assert args.budgets == tmp_path.resolve() / check.DEFAULT_BUDGETS_PATH
    assert args.comment_path == tmp_path.resolve() / check.DEFAULT_COMMENT_PATH
    assert args.resolved_path == tmp_path.resolve() / check.DEFAULT_RESOLVED_PATH


@pytest.mark.parametrize("value", ["no-equals", "=path", "job="])
def test_a_malformed_timings_flag_is_a_usage_error(value: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        check.main(["--junit", value])
    assert excinfo.value.code == 2


def test_needs_attention_is_true_for_notices_and_for_non_ok_verdicts_only() -> None:
    assert not check.needs_attention(
        Report((Verdict("a", Status.OK), Verdict("b", Status.NOT_MEASURED)))
    )
    assert check.needs_attention(Report(notices=("x",)))
    for status in (Status.WARN, Status.FAIL, Status.CONTENTION, Status.UNBUDGETED):
        assert check.needs_attention(Report((Verdict("a", status),)))

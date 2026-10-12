from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from alan import setup_verdicts as sv

RUN_URL = "https://github.com/example-owner/example-repo/actions/runs/1001"
SHA = "a" * 40
CREATED = "2026-09-30T10:00:00Z"
AFTER = "2026-09-30T10:20:00Z"
BEFORE = "2026-09-30T09:00:00Z"


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str, stdin: str = ""
) -> tuple[int, list[str], str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = sv.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _listed(*ids: int) -> str:
    return json.dumps(
        [{"databaseId": i, "createdAt": CREATED, "url": f"{RUN_URL[:-4]}{i}"} for i in ids]
    )


def _job(name: str, status: str = "completed", conclusion: str = "success") -> dict[str, str]:
    return {"name": name, "status": status, "conclusion": conclusion}


def _view(status: str = "completed", jobs: list[dict[str, str]] | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "url": RUN_URL,
            "headSha": SHA,
            "createdAt": CREATED,
            "jobs": [_job("test-backend"), _job("gate")] if jobs is None else jobs,
        }
    )


def test_the_newest_run_after_the_given_number_is_reported_with_its_time_and_address() -> None:
    code, lines = sv.run_newest(["1001"], _listed(1001, 1003, 1002))
    assert code == 0
    assert lines == [f"1003 {CREATED} {RUN_URL[:-4]}1003"]


def test_with_no_number_given_the_newest_run_of_all_is_reported() -> None:
    assert sv.run_newest([], _listed(7, 9, 8))[1][0].startswith("9 ")


def test_no_run_newer_than_the_number_given_exits_one_and_prints_nothing() -> None:
    assert sv.run_newest(["1003"], _listed(1001, 1003)) == (1, [])
    assert sv.run_newest([], "[]") == (1, [])


def test_entries_without_a_run_number_never_count_as_newer() -> None:
    text = json.dumps([{"createdAt": CREATED, "url": RUN_URL}, "junk", {"databaseId": "5"}])
    assert sv.run_newest([], text) == (1, [])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not JSON"),
        ("{}", "not a JSON array"),
    ],
)
def test_a_run_list_that_is_not_a_json_array_is_refused(text: str, message: str) -> None:
    with pytest.raises(sv.VerdictError, match=message):
        sv.run_newest([], text)


def test_a_run_number_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(sv.VerdictError, match="not a number"):
        sv.run_newest(["abc"], _listed(1))


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"databaseId": 5, "createdAt": "yesterday", "url": RUN_URL}, "creation time"),
        ({"databaseId": 5, "createdAt": CREATED, "url": "http://elsewhere.invalid/x"}, "address"),
        ({"databaseId": 5, "createdAt": CREATED}, "address"),
    ],
)
def test_a_run_without_a_readable_time_or_address_is_refused(
    row: dict[str, object], message: str
) -> None:
    with pytest.raises(sv.VerdictError, match=message):
        sv.run_newest([], json.dumps([row]))


def test_a_run_that_has_not_completed_is_waiting() -> None:
    assert sv.run_status([], _view("in_progress")) == (0, ["waiting"])
    assert sv.run_status([], _view("queued")) == (0, ["waiting"])


def test_a_completed_run_whose_gate_job_succeeded_reports_its_head_commit() -> None:
    assert sv.run_status([], _view()) == (0, [f"success {SHA}"])


def test_a_completed_run_whose_gate_job_failed_names_the_failing_job_and_the_run_address() -> None:
    jobs = [_job("test-backend", conclusion="failure"), _job("gate", conclusion="failure")]
    code, lines = sv.run_status([], _view(jobs=jobs))
    assert code == 0
    assert lines == [f"failed job test-backend did not succeed in the CI run {RUN_URL}"]


def test_skipped_and_neutral_jobs_are_not_the_failing_job() -> None:
    jobs = [
        _job("drift-gate", conclusion="skipped"),
        _job("lint", conclusion="neutral"),
        _job("e2e", conclusion="cancelled"),
        _job("gate", conclusion="failure"),
    ]
    assert "job e2e did not succeed" in sv.run_status([], _view(jobs=jobs))[1][0]


def test_a_failed_run_with_no_red_job_names_the_gate_job() -> None:
    jobs = [_job("test-backend"), _job("gate", conclusion="failure")]
    assert sv.run_status([], _view(jobs=jobs))[1][0].startswith("failed job gate did not succeed")


def test_a_completed_run_with_no_jobs_at_all_is_failed_not_successful() -> None:
    assert sv.run_status([], _view(jobs=[]))[1][0].startswith("failed job gate")


def test_an_unnamed_red_job_is_called_unnamed() -> None:
    jobs = [{"status": "completed", "conclusion": "failure"}, _job("gate", conclusion="failure")]
    assert "job unnamed did not succeed" in sv.run_status([], _view(jobs=jobs))[1][0]


def test_a_red_sonar_job_on_a_private_repository_names_the_free_plan_limit() -> None:
    jobs = [_job("SonarCloud analysis", conclusion="failure"), _job("gate", conclusion="failure")]
    line = sv.run_status(["private"], _view(jobs=jobs))[1][0]
    assert line.startswith("failed job SonarCloud analysis did not succeed")
    assert sv.FREE_PLAN_TEXT in line
    assert "Free plan" in line
    assert "private" in line


@pytest.mark.parametrize("visibility", [[], ["public"], ["internal"], ["unknown"]])
def test_a_red_sonar_job_elsewhere_than_a_private_repository_does_not_name_the_limit(
    visibility: list[str],
) -> None:
    jobs = [_job("SonarCloud analysis", conclusion="failure"), _job("gate", conclusion="failure")]
    assert "Free plan" not in sv.run_status(visibility, _view(jobs=jobs))[1][0]


def test_a_private_repository_without_a_red_sonar_job_does_not_name_the_limit() -> None:
    jobs = [_job("test-backend", conclusion="failure"), _job("gate", conclusion="failure")]
    assert "Free plan" not in sv.run_status(["private"], _view(jobs=jobs))[1][0]


def test_a_red_sonar_job_among_several_red_jobs_still_names_the_limit() -> None:
    jobs = [
        _job("test-backend", conclusion="failure"),
        _job("SonarCloud analysis", conclusion="failure"),
        _job("gate", conclusion="failure"),
    ]
    line = sv.run_status(["private"], _view(jobs=jobs))[1][0]
    assert line.startswith("failed job test-backend")
    assert "Free plan" in line


def test_a_job_that_is_still_running_is_not_a_red_job() -> None:
    jobs = [_job("e2e", status="in_progress", conclusion=""), _job("gate", conclusion="failure")]
    assert sv.run_status([], _view(jobs=jobs))[1][0].startswith("failed job gate")


@pytest.mark.parametrize("sha", ["", "abc", "Z" * 40])
def test_a_successful_run_without_a_readable_head_commit_is_refused(sha: str) -> None:
    text = json.loads(_view())
    text["headSha"] = sha
    with pytest.raises(sv.VerdictError, match="head commit"):
        sv.run_status([], json.dumps(text))


def test_a_failed_run_without_a_readable_address_is_refused() -> None:
    jobs = [_job("gate", conclusion="failure")]
    text = json.loads(_view(jobs=jobs))
    text["url"] = "elsewhere"
    with pytest.raises(sv.VerdictError, match="address"):
        sv.run_status([], json.dumps(text))


def _check(slug: str, started: str | None, *, status: str = "completed") -> dict[str, object]:
    row: dict[str, object] = {"name": "SonarCloud Code Analysis", "app": {"slug": slug}}
    row["status"] = status
    if started is not None:
        row["started_at"] = started
    return row


def _status(login: str, created: str, state: str = "success") -> dict[str, object]:
    creator = {"login": login}
    return {"context": "SonarCloud", "state": state, "creator": creator, "created_at": created}


def _evidence(
    tmp_path: Path,
    runs: list[dict[str, object]],
    statuses: list[dict[str, object]] | None = None,
    after: str = CREATED,
) -> str:
    listing = tmp_path / "statuses.json"
    listing.write_text(json.dumps(statuses or []), encoding="utf-8")
    code, lines = sv.sonar_evidence([after, str(listing)], json.dumps({"check_runs": runs}))
    assert code == 0
    return lines[0]


def test_a_completed_check_from_the_sonarcloud_app_after_the_trigger_is_evidence(
    tmp_path: Path,
) -> None:
    assert _evidence(tmp_path, [_check("sonarqubecloud", AFTER)]) == "yes"


def test_a_sonar_named_check_posted_by_github_actions_is_never_evidence(tmp_path: Path) -> None:
    assert _evidence(tmp_path, [_check("github-actions", AFTER)]) == "no"


def test_a_sonarcloud_check_created_before_the_trigger_is_never_evidence(tmp_path: Path) -> None:
    assert _evidence(tmp_path, [_check("sonarqubecloud", BEFORE)]) == "no"


def test_a_sonarcloud_check_created_at_the_trigger_instant_is_not_after_it(tmp_path: Path) -> None:
    assert _evidence(tmp_path, [_check("sonarqubecloud", CREATED)]) == "no"


@pytest.mark.parametrize("started", [None, "not a time", "2026-09-30T10:20:00"])
def test_a_check_without_a_readable_timezone_aware_start_is_never_evidence(
    tmp_path: Path, started: str | None
) -> None:
    assert _evidence(tmp_path, [_check("sonarqubecloud", started)]) == "no"


def test_a_check_that_has_not_completed_is_not_evidence(tmp_path: Path) -> None:
    assert _evidence(tmp_path, [_check("sonarqubecloud", AFTER, status="in_progress")]) == "no"


def test_a_commit_status_from_the_sonarcloud_app_after_the_trigger_is_evidence(
    tmp_path: Path,
) -> None:
    assert _evidence(tmp_path, [], [_status("sonarqubecloud[bot]", AFTER)]) == "yes"
    assert _evidence(tmp_path, [], [_status("SonarCloud[bot]", AFTER, "failure")]) == "yes"


@pytest.mark.parametrize(
    "row",
    [
        _status("github-actions[bot]", AFTER),
        _status("sonarqubecloud[bot]", BEFORE),
        _status("sonarqubecloud[bot]", AFTER, "pending"),
        _status("someone-else", AFTER),
    ],
    ids=["actions-bot", "before-trigger", "pending", "other-creator"],
)
def test_other_commit_statuses_are_never_evidence(tmp_path: Path, row: dict[str, object]) -> None:
    assert _evidence(tmp_path, [], [row]) == "no"


def test_a_missing_or_unreadable_status_listing_leaves_the_check_runs_to_decide(
    tmp_path: Path,
) -> None:
    text = json.dumps({"check_runs": [_check("sonarqubecloud", AFTER)]})
    assert sv.sonar_evidence([CREATED], text) == (0, ["yes"])
    assert sv.sonar_evidence([CREATED, str(tmp_path / "absent.json")], text) == (0, ["yes"])
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert sv.sonar_evidence([CREATED, str(broken)], text) == (0, ["yes"])
    assert sv.sonar_evidence([CREATED, str(broken)], json.dumps({"check_runs": []})) == (
        0,
        ["no"],
    )
    assert sv.sonar_evidence([CREATED], json.dumps({"check_runs": []})) == (0, ["no"])


def test_a_trigger_time_that_is_not_a_timestamp_is_refused() -> None:
    with pytest.raises(sv.VerdictError, match="not a timestamp"):
        sv.sonar_evidence(["yesterday"], json.dumps({"check_runs": []}))
    with pytest.raises(sv.VerdictError, match="needs an argument"):
        sv.sonar_evidence([], json.dumps({"check_runs": []}))


def test_the_evidence_verdict_needs_a_json_object() -> None:
    with pytest.raises(sv.VerdictError, match="not JSON"):
        sv.sonar_evidence([CREATED], "")


def _findings(status: str | None) -> str:
    rows = [{"check": "repo_clonable", "status": "pass"}]
    if status is not None:
        rows.append({"check": "reviewer_distinct_from_author", "status": status})
    return json.dumps({"findings": rows})


@pytest.mark.parametrize("status", ["pass", "fail", "unknown"])
def test_the_reviewer_distinctness_verdict_repeats_the_findings_status(status: str) -> None:
    assert sv.reviewer_distinct([], _findings(status)) == (0, [status])


def test_a_missing_or_blank_distinctness_finding_is_absent_or_unknown() -> None:
    assert sv.reviewer_distinct([], _findings(None)) == (0, ["absent"])
    assert sv.reviewer_distinct([], _findings("")) == (0, ["unknown"])


def _entry(engine: str, requirement: str, state: str, message: str = "why") -> dict[str, str]:
    return {"engine": engine, "requirement": requirement, "state": state, "message": message}


def _health(*entries: object) -> str:
    return json.dumps({"ok": True, "dispatch_readiness": list(entries)})


def test_an_empty_readiness_list_passes_with_no_rows() -> None:
    assert sv.readiness([], _health()) == (0, [])


def test_a_warning_passes_and_is_listed_without_a_step() -> None:
    row = _entry("a/b", "reviewer_credential", "warning", "expires soon")
    assert sv.readiness([], _health(row)) == (
        0,
        ["warning - a/b reviewer_credential warning expires soon"],
    )


@pytest.mark.parametrize(
    ("requirement", "step"),
    [
        ("reviewer_credential", "verifier"),
        ("code_quality", "latch"),
        ("default_engine", "profile"),
        ("something_new", "confirm"),
    ],
)
@pytest.mark.parametrize("state", ["not_ready", "unknown"])
def test_a_not_ready_or_unknown_entry_fails_naming_the_engine_the_requirement_and_a_step(
    requirement: str, step: str, state: str
) -> None:
    code, rows = sv.readiness([], _health(_entry("other-owner/other-repo", requirement, state)))
    assert code == 1
    assert rows == [f"blocked {step} other-owner/other-repo {requirement} {state} why"]


def test_one_blocking_entry_among_warnings_fails_and_every_entry_is_listed() -> None:
    entries = (
        _entry("a/b", "reviewer_credential", "warning"),
        _entry("c/d", "code_quality", "unknown"),
    )
    code, rows = sv.readiness([], _health(*entries))
    assert code == 1
    assert [row.split()[0] for row in rows] == ["warning", "blocked"]


@pytest.mark.parametrize("entry", ["junk", 5, None, {}, {"state": "mystery"}])
def test_an_entry_that_is_not_a_warning_is_read_literally_as_blocking(entry: object) -> None:
    code, rows = sv.readiness([], _health(entry))
    assert code == 1
    assert rows[0].startswith("blocked confirm unknown unknown")


def test_words_with_spaces_and_long_messages_stay_one_line() -> None:
    row = _entry("a/b", "two words", "not_ready", "line one\nline two " + "x" * 400)
    rows = sv.readiness([], _health(row))[1]
    assert len(rows) == 1
    assert "\n" not in rows[0]
    assert rows[0].startswith("blocked confirm a/b two_words not_ready line one line two")
    assert len(rows[0]) < 300


def test_a_health_answer_without_a_readiness_list_is_refused() -> None:
    for text in ('{"ok": true}', '{"dispatch_readiness": "no"}', "[]", "not json"):
        with pytest.raises(sv.VerdictError):
            sv.readiness([], text)


def test_the_new_commands_are_reachable_through_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines, _ = _run(monkeypatch, capsys, "run-newest", "0", stdin=_listed(3, 4))
    assert (code, lines[0].split()[0]) == (0, "4")
    code, lines, _ = _run(monkeypatch, capsys, "run-status", "private", stdin=_view("in_progress"))
    assert (code, lines) == (0, ["waiting"])
    code, lines, _ = _run(monkeypatch, capsys, "reviewer-distinct", stdin=_findings("pass"))
    assert (code, lines) == (0, ["pass"])
    code, lines, _ = _run(monkeypatch, capsys, "readiness", stdin=_health())
    assert (code, lines) == (0, [])
    code, lines, err = _run(monkeypatch, capsys, "sonar-evidence", "junk", stdin="{}")
    assert (code, lines) == (2, [])
    assert "not a timestamp" in err
    code, lines, err = _run(monkeypatch, capsys, "readiness", stdin='{"ok": true}')
    assert (code, err.startswith("setup_verdicts: ")) == (2, True)

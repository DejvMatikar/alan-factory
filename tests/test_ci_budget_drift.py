from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import _ci_drift as drift
from alan import _ci_jobs_api as jobs_api
from alan import ci_budget_drift as watch


def _samples(values: list[float], job: str = "job") -> list[drift.RunSample]:
    return [
        drift.RunSample(index, f"2026-09-{20 + index:02d}T04:00:00Z", 100 + index, {job: value})
        for index, value in enumerate(values)
    ]


def _serve(
    monkeypatch: pytest.MonkeyPatch, by_workflow: dict[str, list[drift.RunSample] | str]
) -> None:
    def fake_main_samples(
        api_url: str, repo: str, workflow: str, token: str, limit: int
    ) -> list[drift.RunSample]:
        served = by_workflow[workflow]
        if isinstance(served, str):
            raise jobs_api.ApiUnavailableError(served)
        return served

    monkeypatch.setattr(jobs_api, "main_samples", fake_main_samples)


def _budgets(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _parsed_output(path: Path) -> tuple[dict[str, list[dict[str, str]]], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    fields = dict(line.split("=", 1) for line in lines)
    return json.loads(fields["matrix"]), int(fields["count"])


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    return path


def test_collect_gathers_findings_across_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {"a.yml": _samples([200.0] * 7), "b.yml": _samples([100.0] * 7)})
    findings, warnings = watch.collect(
        "https://api", "o/r", "tok", {"a.yml": {"job": 100.0}, "b.yml": {"job": 100.0}}, 14
    )
    assert [finding.workflow for finding in findings] == ["a.yml"]
    assert warnings == []


def test_collect_skips_a_workflow_whose_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, {"a.yml": "403 Forbidden", "b.yml": _samples([200.0] * 7)})
    findings, warnings = watch.collect(
        "https://api", "o/r", "tok", {"a.yml": {"job": 100.0}, "b.yml": {"job": 100.0}}, 14
    )
    assert [finding.workflow for finding in findings] == ["b.yml"]
    assert warnings == ["a.yml: main-branch durations unavailable, skipped: 403 Forbidden"]


def test_matrix_entry_carries_everything_the_filing_action_needs() -> None:
    [finding] = drift.drift_findings("tests.yml", _samples([200.0] * 7), {"job": 100.0})
    entry = watch.matrix_entry(finding)
    assert entry["label"] == drift.finding_label("tests.yml", "job", drift.Direction.SLOW)
    assert entry["marker"] == "slow:100:200"
    assert entry["color"] == "D93F0B"
    assert "drifted above" in entry["description"]
    assert entry["title"].startswith('CI job "job" drifted above')
    assert "| Proposed budget | 200s |" in entry["body"]


def test_slack_entries_use_the_slack_label_and_color() -> None:
    [finding] = drift.drift_findings("tests.yml", _samples([40.0] * 7), {"job": 100.0})
    entry = watch.matrix_entry(finding)
    assert entry["label"].startswith("ci-budget-slack:")
    assert entry["color"] == "0E8A16"
    assert "well below" in entry["description"]


def test_output_lines_are_a_compact_matrix_and_a_count() -> None:
    lines = watch.output_lines([{"label": "x", "body": "a\nb"}])
    assert lines == ['matrix={"include":[{"label":"x","body":"a\\nb"}]}', "count=1"]


def test_output_lines_of_nothing_are_an_empty_include_and_zero() -> None:
    assert watch.output_lines([]) == ['matrix={"include":[]}', "count=0"]


def test_write_outputs_appends_to_the_output_file(tmp_path: Path) -> None:
    path = tmp_path / "out"
    path.write_text("existing=1\n", encoding="utf-8")
    watch.write_outputs([], str(path))
    assert path.read_text(encoding="utf-8") == 'existing=1\nmatrix={"include":[]}\ncount=0\n'


def test_write_outputs_prints_when_there_is_no_output_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    watch.write_outputs([], None)
    assert capsys.readouterr().out == 'matrix={"include":[]}\ncount=0\n'


def test_main_files_one_matrix_entry_per_finding_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path
) -> None:
    budgets = _budgets(tmp_path, {"tests.yml": {"job": 100, "fast": 100, "steady": 100}})
    samples = [
        drift.RunSample(i, "2026-09-20", 100 + i, {"job": 200.0, "fast": 40.0, "steady": 100.0})
        for i in range(7)
    ]
    _serve(monkeypatch, {"tests.yml": samples})
    assert watch.main(["--budgets", str(budgets)]) == 0
    matrix, count = _parsed_output(output)
    assert count == 2
    assert sorted(entry["marker"] for entry in matrix["include"]) == [
        "slack:100:40",
        "slow:100:200",
    ]


def test_main_caps_the_number_of_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path
) -> None:
    jobs = {f"job {i:02d}": 100 for i in range(watch.MAX_FINDINGS + 5)}
    samples = [
        drift.RunSample(i, "2026-09-20", None, {name: 300.0 for name in jobs}) for i in range(7)
    ]
    _serve(monkeypatch, {"tests.yml": samples})
    assert watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": jobs}))]) == 0
    assert _parsed_output(output)[1] == watch.MAX_FINDINGS


def test_main_with_no_drift_files_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path
) -> None:
    _serve(monkeypatch, {"tests.yml": _samples([100.0] * 7)})
    assert watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": {"job": 100}}))]) == 0
    assert _parsed_output(output) == ({"include": []}, 0)


def test_main_warns_and_exits_zero_when_a_workflows_history_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _serve(monkeypatch, {"tests.yml": "503 Service Unavailable"})
    assert watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": {"job": 100}}))]) == 0
    assert capsys.readouterr().out.startswith("::warning title=CI duration budget::tests.yml")
    assert _parsed_output(output)[1] == 0


def test_main_warns_and_exits_zero_when_the_budget_file_is_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _serve(monkeypatch, {})
    assert watch.main(["--budgets", str(tmp_path / "absent.json")]) == 0
    assert "Budget file unusable, so no drift was checked" in capsys.readouterr().out
    assert _parsed_output(output)[1] == 0


def test_main_reads_the_repository_and_token_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path
) -> None:
    seen: list[tuple[str, str, str, str, int]] = []

    def fake_main_samples(
        api_url: str, repo: str, workflow: str, token: str, limit: int
    ) -> list[drift.RunSample]:
        seen.append((api_url, repo, workflow, token, limit))
        return []

    monkeypatch.setattr(jobs_api, "main_samples", fake_main_samples)
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": {"job": 100}}))])
    assert seen == [("https://api.github.com", "o/r", "tests.yml", "tok", 14)]


def test_flags_override_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: Path
) -> None:
    seen: list[tuple[str, str, int]] = []

    def fake_main_samples(
        api_url: str, repo: str, workflow: str, token: str, limit: int
    ) -> list[drift.RunSample]:
        seen.append((api_url, repo, limit))
        return []

    monkeypatch.setattr(jobs_api, "main_samples", fake_main_samples)
    watch.main(
        [
            "--budgets",
            str(_budgets(tmp_path, {"tests.yml": {"job": 100}})),
            "--repo",
            "x/y",
            "--api-url",
            "https://ghe.example/api",
            "--runs",
            "5",
        ]
    )
    assert seen == [("https://ghe.example/api", "x/y", 5)]


def test_main_prints_the_matrix_when_no_output_file_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _serve(monkeypatch, {"tests.yml": _samples([100.0] * 7)})
    assert watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": {"job": 100}}))]) == 0
    assert capsys.readouterr().out.endswith('matrix={"include":[]}\ncount=0\n')


def test_a_budget_file_outside_the_workspace_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.chdir(workspace_dir)
    _serve(monkeypatch, {})
    with pytest.raises(SystemExit) as excinfo:
        watch.main(["--budgets", str(_budgets(tmp_path, {"tests.yml": {"job": 100}}))])
    assert excinfo.value.code == 2
    assert "invalid workspace_path value" in capsys.readouterr().err

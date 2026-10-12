from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan import e2e_failures, process_runner, secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_audit_log, _pr_branch, _pr_flake_ledger, _pr_ready
from tests import _pr_branch_support as support
from tests._pr_branch_support import (
    _E2E_GATE,
    _FLAKY_GATE,
    _WIDGET_FORGE,
    _audit_entries,
    _fake_gh,
    _gate_fake,
    _gate_readiness,
)

_E2E_ANNOTATIONS = json.dumps([
    {
        "annotation_level": "failure", "path": ".github", "start_line": 25,
        "title": "", "message": "Process completed with exit code 1.",
    },
    {
        "annotation_level": "notice", "path": ".github", "start_line": 155,
        "title": "🎭 Playwright Run Summary",
        "message": (
            "  1 flaky\n    [chromium] › e2e/explore-chat.spec.ts:12:3 › Explore chat renders"
        ),
    },
    {
        "annotation_level": "failure", "path": "alan/web/e2e/explore-chat.spec.ts",
        "start_line": 12, "title": "Explore chat renders", "message": "expected 1 got 0",
    },
    {
        "annotation_level": "notice", "path": ".github", "start_line": 52,
        "title": "🎭 Playwright Run Summary", "message": "  10 passed (8.3s)",
    },
])

_EXPLORE_FAILURE = e2e_failures.FailingE2ETest(
    spec="alan/web/e2e/explore-chat.spec.ts",
    title="Explore chat renders",
    message="expected 1 got 0",
)

_E2E_ACTIONS_DETAILS_URL = "https://github.com/o/r/actions/runs/123456/job/789"

_E2E_CHECK_RUNS = json.dumps({
    "check_runs": [
        {"name": "Other", "check_suite": {"id": 11}},
        {"name": _E2E_GATE, "conclusion": "failure", "check_suite": {"id": 8888}},
    ]
})

_E2E_CHECK_RUNS_ACTIONS = json.dumps({
    "check_runs": [
        {"name": "Other", "check_suite": {"id": 11}},
        {
            "name": _E2E_GATE,
            "conclusion": "failure",
            "check_suite": {"id": 8888},
            "details_url": _E2E_ACTIONS_DETAILS_URL,
        },
    ]
})

@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


def test_shared_flaky_allowlist_membership_is_engine_independent() -> None:
    assert _E2E_GATE in _pr_flake_ledger.SHARED_FLAKY_ALLOWLIST
    assert _WIDGET_FORGE.repo.slug != DEFAULT_FORGE_TARGET.repo.slug


def test_find_check_suite_id_for_name_no_match() -> None:
    runs: list[dict[str, object]] = [{"name": "Other", "check_suite": {"id": 9}}]
    assert _pr_branch._find_check_suite_id_for_name(runs, _E2E_GATE) is None


def test_find_check_suite_id_for_name_suite_not_dict() -> None:
    runs: list[dict[str, object]] = [{"name": _E2E_GATE, "check_suite": "nope"}]
    assert _pr_branch._find_check_suite_id_for_name(runs, _E2E_GATE) is None


def test_find_check_suite_id_for_name_id_not_int() -> None:
    runs: list[dict[str, object]] = [{"name": _E2E_GATE, "check_suite": {"id": "x"}}]
    assert _pr_branch._find_check_suite_id_for_name(runs, _E2E_GATE) is None


def test_find_check_suite_id_for_name_success() -> None:
    runs: list[dict[str, object]] = [
        {"name": "Other", "check_suite": {"id": 9}},
        {"name": _E2E_GATE, "check_suite": {"id": 8888}},
    ]
    assert _pr_branch._find_check_suite_id_for_name(runs, _E2E_GATE) == 8888


def test_find_actions_run_id_for_name_success() -> None:
    runs: list[dict[str, object]] = [
        {"name": "Other", "details_url": "https://github.com/o/r/actions/runs/1/job/2"},
        {"name": _E2E_GATE, "details_url": _E2E_ACTIONS_DETAILS_URL},
    ]
    assert _pr_branch._find_actions_run_id_for_name(runs, _E2E_GATE) == 123456


def test_find_actions_run_id_for_name_no_name_match() -> None:
    runs: list[dict[str, object]] = [
        {"name": "Other", "details_url": _E2E_ACTIONS_DETAILS_URL},
    ]
    assert _pr_branch._find_actions_run_id_for_name(runs, _E2E_GATE) is None


def test_find_actions_run_id_for_name_non_actions_details_url() -> None:
    runs: list[dict[str, object]] = [
        {"name": _E2E_GATE, "details_url": "https://sonarcloud.io/dashboard?id=x"},
    ]
    assert _pr_branch._find_actions_run_id_for_name(runs, _E2E_GATE) is None


def test_rerun_external_gate_sha_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert any(
        e.get("action") == "external_gate_rerun_failed"
        and e.get("reason") == "sha_lookup_failed"
        for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_check_runs_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="api error")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert any(
        e.get("action") == "external_gate_rerun_failed"
        and e.get("reason") == "check_runs_lookup_failed"
        for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_check_suite_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    no_match = json.dumps({"check_runs": [{"name": "Other", "check_suite": {"id": 9}}]})
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=no_match, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert any(
        e.get("action") == "external_gate_rerun_failed"
        and e.get("reason") == "check_suite_not_found"
        for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_rerequest_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_E2E_CHECK_RUNS, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="forbidden")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    assert any(
        e.get("action") == "external_gate_rerun_failed"
        and e.get("reason") == "rerequest_failed"
        for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_full_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(cmd, 0, stdout=_E2E_CHECK_RUNS, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    assert any(
        e.get("action") == "external_gate_rerun_ok" for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_actions_run_rerun_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    commands: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(cmd)
        if len(commands) == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if len(commands) == 2:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=_E2E_CHECK_RUNS_ACTIONS, stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    assert commands[2][:4] == ["gh", "run", "rerun", "123456"]
    assert "--failed" in commands[2]
    assert any(
        e.get("action") == "external_gate_rerun_ok" for e in _audit_entries(tmp_path)
    )


def test_rerun_external_gate_actions_run_rerun_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    call_n: list[int] = [0]

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        call_n[0] += 1
        if call_n[0] == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha123\n", stderr="")
        if call_n[0] == 2:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=_E2E_CHECK_RUNS_ACTIONS, stderr=""
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="run not rerunnable")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    entries = _audit_entries(tmp_path)
    assert any(
        e.get("action") == "external_gate_rerun_failed"
        and e.get("reason") == "run_rerun_failed"
        for e in entries
    )
    assert any(e.get("action") == "external_gate_query_failed" for e in entries)


def test_rerun_external_gate_failure_uses_neutral_audit_not_sonar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.rerun_external_gate(
        42, "task-x", _E2E_GATE, forge_target=DEFAULT_FORGE_TARGET,
    ) is False
    actions = [e.get("action") for e in _audit_entries(tmp_path)]
    assert "external_gate_query_failed" in actions
    assert "list_sonarcloud_annotations_failed" not in actions


def test_rerun_flaky_checks_returns_true_if_any_check_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_rerun(_pr: int, _t: str, check: str, **_kw: object) -> bool:
        seen.append(check)
        return check == "b"

    monkeypatch.setattr(_pr_branch, "rerun_external_gate", fake_rerun)
    assert _pr_branch.rerun_flaky_checks(
        42, "task-x", ("a", "b", "c"), forge_target=DEFAULT_FORGE_TARGET,
    ) is True
    assert seen == ["a", "b", "c"]


def test_rerun_flaky_checks_returns_false_when_none_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", lambda *_a, **_kw: False)
    assert _pr_branch.rerun_flaky_checks(
        42, "task-x", ("a", "b"), forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_rerun_flaky_checks_empty_failing_checks_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not be called with no failing checks")

    monkeypatch.setattr(_pr_branch, "rerun_external_gate", boom)
    assert _pr_branch.rerun_flaky_checks(
        42, "task-x", (), forge_target=DEFAULT_FORGE_TARGET,
    ) is False


def test_shared_flaky_allowlist_locked_membership() -> None:
    assert _pr_flake_ledger.SHARED_FLAKY_ALLOWLIST == frozenset(
        {
            _E2E_GATE,
            "Backend tests + coverage",
            "Frontend tests + coverage (100/100/100/100)",
        }
    )


def test_max_gate_reruns_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_MAX_EXTERNAL_GATE_RERUNS", raising=False)
    assert _pr_flake_ledger.max_gate_reruns() == 2
    monkeypatch.setenv("ALAN_MAX_EXTERNAL_GATE_RERUNS", "5")
    assert _pr_flake_ledger.max_gate_reruns() == 5


def test_actionable_failing_checks_empty_when_nothing_failing() -> None:
    assert _pr_flake_ledger.actionable_failing_checks(_gate_readiness()) == ()


def test_actionable_failing_checks_excludes_shared_flaky_allowlist() -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    assert _pr_flake_ledger.actionable_failing_checks(readiness) == ()


def test_actionable_failing_checks_excludes_quarantined() -> None:
    readiness = _gate_readiness(
        failing_checks=("Layered architecture (import-linter)",),
        quarantined_checks=("Layered architecture (import-linter)",),
    )
    assert _pr_flake_ledger.actionable_failing_checks(readiness) == ()


def test_actionable_failing_checks_keeps_genuine_and_drops_flaky() -> None:
    readiness = _gate_readiness(
        failing_checks=(_FLAKY_GATE, "SonarCloud (blocking quality gate)"),
    )
    assert _pr_flake_ledger.actionable_failing_checks(readiness) == (
        "SonarCloud (blocking quality gate)",
    )


def test_flaky_only_failing_empty_failing_is_false() -> None:
    assert _pr_flake_ledger.flaky_only_failing(_gate_readiness()) is False


def test_flaky_only_failing_all_allowlisted_is_true() -> None:
    readiness = _gate_readiness(failing_checks=(_FLAKY_GATE,))
    assert _pr_flake_ledger.flaky_only_failing(readiness) is True


def test_flaky_only_failing_mixed_allowlisted_and_genuine_is_false() -> None:
    readiness = _gate_readiness(
        failing_checks=(_FLAKY_GATE, "SonarCloud (blocking quality gate)"),
    )
    assert _pr_flake_ledger.flaky_only_failing(readiness) is False


def test_flaky_only_failing_dirty_is_false() -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="DIRTY",
        review_decision="",
        failing_checks=(_FLAKY_GATE,),
    )
    assert _pr_flake_ledger.flaky_only_failing(readiness) is False


def test_flaky_only_failing_changes_requested_is_false() -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        failing_checks=(_FLAKY_GATE,),
    )
    assert _pr_flake_ledger.flaky_only_failing(readiness) is False


def test_fetch_failing_check_details_returns_title_summary_and_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _gate_fake())
    details = _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis",),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert len(details) == 1
    assert details[0].name == "SonarCloud Code Analysis"
    assert details[0].title == "Quality Gate failed"
    assert "6.0% Duplication on New Code" in details[0].summary
    assert details[0].details_url == "https://sonarcloud.io/dashboard?pullRequest=855"


def test_fetch_failing_check_details_falls_back_to_html_url_and_blank_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _gate_fake())
    details = _pr_branch.fetch_failing_check_details(
        855, (_FLAKY_GATE,), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert len(details) == 1
    assert details[0].title == ""
    assert details[0].summary == ""
    assert details[0].details_url == "https://github.com/o/r/actions/runs/1/job/2"


def test_fetch_failing_check_details_truncates_long_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    long_runs = json.dumps({
        "check_runs": [{
            "id": 1,
            "name": "SonarCloud Code Analysis",
            "output": {"title": "t", "summary": "x" * 5000},
        }]
    })
    monkeypatch.setattr(
        process_runner, "run_subprocess", _gate_fake(check_runs_stdout=long_runs)
    )
    details = _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis",), forge_target=DEFAULT_FORGE_TARGET,
    )
    assert len(details[0].summary) == 2000


def test_fetch_failing_check_details_empty_when_no_names_requested() -> None:
    assert _pr_branch.fetch_failing_check_details(855, (), forge_target=DEFAULT_FORGE_TARGET) == ()


def test_fetch_failing_check_details_empty_when_head_sha_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _gate_fake(sha_rc=1))
    assert _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis",), forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_fetch_failing_check_details_empty_when_check_runs_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        process_runner, "run_subprocess", _gate_fake(check_runs_stdout="[]")
    )
    assert _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis",), forge_target=DEFAULT_FORGE_TARGET,
    ) == ()


def test_fetch_failing_check_details_audits_under_its_own_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _gate_fake(sha_rc=1))
    assert _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis",), forge_target=DEFAULT_FORGE_TARGET,
    ) == ()
    audit = (tmp_path / "logs" / _pr_audit_log.AUDIT_FILE).read_text()
    assert "fetch_failing_check_details_failed" in audit
    assert "list_sonarcloud_annotations_failed" not in audit


def test_failed_job_id_extracts_id_from_details_url() -> None:
    check = {
        "conclusion": "FAILURE",
        "detailsUrl": "https://github.com/o/r/actions/runs/9/job/87583018028",
    }
    assert _pr_branch.failed_job_id(check) == 87583018028


@pytest.mark.parametrize("conclusion", ["FAILURE", "TIMED_OUT", "ACTION_REQUIRED"])
def test_failed_job_id_treats_all_failing_conclusions(conclusion: str) -> None:
    check = {"conclusion": conclusion, "detailsUrl": "https://x/actions/runs/9/job/7"}
    assert _pr_branch.failed_job_id(check) == 7


def test_failed_job_id_none_when_passing_conclusion() -> None:
    assert _pr_branch.failed_job_id({"conclusion": "SUCCESS", "detailsUrl": ".../job/1"}) is None


def test_failed_job_id_none_when_url_not_str() -> None:
    assert _pr_branch.failed_job_id({"conclusion": "FAILURE", "detailsUrl": 42}) is None


def test_failed_job_id_none_when_no_job_segment() -> None:
    check = {"conclusion": "FAILURE", "detailsUrl": "https://x/runs/9"}
    assert _pr_branch.failed_job_id(check) is None


def test_failing_job_ids_collects_all_failures_skipping_noise() -> None:
    rollup: list[object] = [
        "not-a-dict",
        {"conclusion": "SUCCESS", "detailsUrl": ".../job/1"},
        {"conclusion": "FAILURE", "detailsUrl": ".../job/222"},
        {"conclusion": "TIMED_OUT", "detailsUrl": ".../job/333"},
    ]
    assert _pr_branch.failing_job_ids(rollup) == [222, 333]


def test_failing_job_ids_empty_when_all_pass() -> None:
    rollup: list[object] = [{"conclusion": "SUCCESS", "detailsUrl": ".../job/1"}]
    assert _pr_branch.failing_job_ids(rollup) == []


def test_fetch_failing_job_ids_returns_all_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"statusCheckRollup": [
        {"conclusion": "FAILURE",
         "detailsUrl": "https://github.com/o/r/actions/runs/9/job/555"},
        {"conclusion": "FAILURE",
         "detailsUrl": "https://github.com/o/r/actions/runs/9/job/556"},
    ]})
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(payload))
    assert _pr_branch.fetch_failing_job_ids(
        42, 10.0, forge_target=DEFAULT_FORGE_TARGET,
    ) == [555, 556]


def test_fetch_failing_job_ids_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(""))
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("{bad"))
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("[]"))
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_non_list_rollup(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"statusCheckRollup": "x"})
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(payload))
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10.0)

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_empty_on_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert _pr_branch.fetch_failing_job_ids(42, 10.0, forge_target=DEFAULT_FORGE_TARGET) == []


def test_fetch_failing_job_ids_targets_forge_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    _pr_branch.fetch_failing_job_ids(1, 10.0, forge_target=_WIDGET_FORGE)
    assert seen[0][seen[0].index("--repo") + 1] == "acme-corp/widget-app"


def test_fetch_failing_job_ids_passes_forge_scoped_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[object] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    def _resolve(engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        del env_fallback
        assert purpose == secrets_store.TRANSPORT_PURPOSE
        return "widget-token" if engine == _WIDGET_FORGE.repo.slug else "wrong-token"

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    _pr_branch.fetch_failing_job_ids(1, 10.0, forge_target=_WIDGET_FORGE)
    assert captured_envs == [{"GH_TOKEN": "widget-token"}]


def test_fetch_failed_log_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("log text"))
    assert _pr_branch.fetch_failed_log(555, 10.0, forge_target=DEFAULT_FORGE_TARGET) == "log text"


def test_fetch_failed_log_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("x", returncode=2))
    assert _pr_branch.fetch_failed_log(555, 10.0, forge_target=DEFAULT_FORGE_TARGET) == ""


def test_fetch_failed_log_empty_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10.0)

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert _pr_branch.fetch_failed_log(555, 10.0, forge_target=DEFAULT_FORGE_TARGET) == ""


def test_fetch_failed_log_empty_on_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        raise FileNotFoundError("gh")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    assert _pr_branch.fetch_failed_log(555, 10.0, forge_target=DEFAULT_FORGE_TARGET) == ""


def test_fetch_failed_log_targets_forge_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="log", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    _pr_branch.fetch_failed_log(1, 10.0, forge_target=_WIDGET_FORGE)
    assert seen[0][seen[0].index("--repo") + 1] == "acme-corp/widget-app"


def test_fetch_failed_log_passes_forge_scoped_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[object] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout="log", stderr="")

    def _resolve(engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        del env_fallback
        assert purpose == secrets_store.TRANSPORT_PURPOSE
        return "widget-token" if engine == _WIDGET_FORGE.repo.slug else "wrong-token"

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    _pr_branch.fetch_failed_log(1, 10.0, forge_target=_WIDGET_FORGE)
    assert captured_envs == [{"GH_TOKEN": "widget-token"}]


def test_fetch_failing_e2e_tests_none_when_no_job_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [])
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_check_run_annotations_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh(_E2E_ANNOTATIONS))
    payload = _pr_branch.fetch_check_run_annotations(_WIDGET_FORGE, 789, 42)
    assert payload is not None
    assert [a["annotation_level"] for a in payload] == ["failure", "notice", "failure", "notice"]


def test_fetch_check_run_annotations_targets_the_check_run_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    assert _pr_branch.fetch_check_run_annotations(_WIDGET_FORGE, 789, 42) == []
    assert calls == [
        ["gh", "api", "repos/acme-corp/widget-app/check-runs/789/annotations?per_page=100"],
    ]


def test_fetch_check_run_annotations_none_on_nonzero_rc_and_reports_on_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons: list[tuple[int, str]] = []
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    result = _pr_branch.fetch_check_run_annotations(
        _WIDGET_FORGE, 789, 42, on_fail=lambda pr, reason: reasons.append((pr, reason)),
    )
    assert result is None
    assert reasons == [(42, "gh returncode=1: ")]


def test_fetch_check_run_annotations_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons: list[str] = []

    def boom(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    result = _pr_branch.fetch_check_run_annotations(
        _WIDGET_FORGE, 789, 42, on_fail=lambda _pr, reason: reasons.append(reason),
    )
    assert result is None
    assert reasons == ["gh subprocess error: Command 'gh' timed out after 1 seconds"]


def test_fetch_check_run_annotations_none_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons: list[str] = []
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh('{"not": "a list"}'))
    result = _pr_branch.fetch_check_run_annotations(
        _WIDGET_FORGE, 789, 42, on_fail=lambda _pr, reason: reasons.append(reason),
    )
    assert result is None
    assert reasons == ["annotations_json_malformed"]


def test_fetch_check_run_annotations_defaults_to_the_sonar_audit_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.fetch_check_run_annotations(_WIDGET_FORGE, 789, 42) is None
    assert [e["action"] for e in _audit_entries(tmp_path)] == [
        "list_sonarcloud_annotations_failed",
    ]


def test_fetch_check_run_annotations_passes_env_and_timeout_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(dict(kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "transport_credential", lambda _t: "t")
    _pr_branch.fetch_check_run_annotations(
        _WIDGET_FORGE, 789, 42, as_transport=True, timeout_s=7.0,
    )
    assert captured == [{"cwd": None, "timeout": 7.0, "env": {"GH_TOKEN": "t"}}]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r/actions/runs/9/job/87583018028", 87583018028),
        ("https://github.com/o/r/actions/runs/9", None),
        ("https://github.com/o/r/actions/runs/9/job/", None),
        ("https://sonarcloud.io/dashboard?pullRequest=855", None),
        ("", None),
    ],
)
def test_job_id_from_details_url_reads_only_a_numeric_job_segment(
    url: str, expected: int | None,
) -> None:
    assert _pr_branch.job_id_from_details_url(url) == expected


def test_failed_job_id_agrees_with_job_id_from_details_url() -> None:
    url = "https://github.com/o/r/actions/runs/9/job/31"
    check = {"conclusion": "FAILURE", "detailsUrl": url}
    assert _pr_branch.failed_job_id(check) == _pr_branch.job_id_from_details_url(url) == 31


def test_fetch_failing_check_details_carries_the_check_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _gate_fake())
    details = _pr_branch.fetch_failing_check_details(
        855, ("SonarCloud Code Analysis", _FLAKY_GATE),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert [d.check_run_id for d in details] == [1, 2]


@pytest.mark.parametrize("raw_id", ["7", None, 7.5])
def test_coerce_check_failure_defaults_a_non_integer_id_to_zero(raw_id: object) -> None:
    detail = _pr_branch._coerce_check_failure(  # noqa: SLF001
        {"name": "ci", "id": raw_id}, frozenset({"ci"})
    )
    assert detail is not None
    assert detail.check_run_id == 0


def test_check_failure_detail_check_run_id_defaults_to_zero() -> None:
    detail = _pr_branch.CheckFailureDetail(name="n", title="t", summary="s", details_url="u")
    assert detail.check_run_id == 0


def test_fetch_check_annotations_returns_the_payload_for_the_check_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_E2E_ANNOTATIONS, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    payload = _pr_branch.fetch_check_annotations(789, forge_target=_WIDGET_FORGE)
    assert payload is not None
    assert len(payload) == 4
    assert calls == [
        ["gh", "api", "repos/acme-corp/widget-app/check-runs/789/annotations?per_page=100"],
    ]


def test_fetch_check_annotations_passes_the_forge_scoped_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[object] = []

    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    def _resolve(engine: str, purpose: str, *, env_fallback: str | None) -> str | None:
        return "widget-token"

    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "resolve_secret", _resolve)
    _pr_branch.fetch_check_annotations(789, forge_target=_WIDGET_FORGE)
    assert captured_envs == [{"GH_TOKEN": "widget-token"}]


def test_fetch_check_annotations_none_on_failure_and_writes_no_audit_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.fetch_check_annotations(789, forge_target=DEFAULT_FORGE_TARGET) is None
    assert _audit_entries(tmp_path) == []


def test_fetch_failing_e2e_tests_none_when_annotations_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [1])
    monkeypatch.setattr(_pr_branch, "fetch_check_run_annotations", lambda *_a, **_kw: None)
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_failing_e2e_tests_none_when_annotations_name_no_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step_only = [json.loads(_E2E_ANNOTATIONS)[0]]
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [1])
    monkeypatch.setattr(
        _pr_branch, "fetch_check_run_annotations", lambda *_a, **_kw: step_only,
    )
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=DEFAULT_FORGE_TARGET) is None


def test_fetch_failing_e2e_tests_returns_spec_rows_and_drops_step_and_summary_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [1])
    monkeypatch.setattr(
        _pr_branch, "fetch_check_run_annotations",
        lambda *_a, **_kw: json.loads(_E2E_ANNOTATIONS),
    )
    assert _pr_branch.fetch_failing_e2e_tests(
        42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (_EXPLORE_FAILURE,)


def test_fetch_failing_e2e_tests_dedups_across_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [1, 2])
    monkeypatch.setattr(
        _pr_branch, "fetch_check_run_annotations",
        lambda *_a, **_kw: json.loads(_E2E_ANNOTATIONS),
    )
    assert _pr_branch.fetch_failing_e2e_tests(
        42, forge_target=DEFAULT_FORGE_TARGET,
    ) == (_EXPLORE_FAILURE,)


def test_fetch_failing_e2e_tests_caps_jobs_inspected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def fake(
        _forge_target: object, check_run_id: int, _pr: int, **_kw: object,
    ) -> list[dict[str, object]]:
        seen.append(check_run_id)
        return []

    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: list(range(10)))
    monkeypatch.setattr(_pr_branch, "fetch_check_run_annotations", fake)
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=DEFAULT_FORGE_TARGET) is None
    assert seen == [0, 1, 2, 3, 4]


def test_fetch_failing_e2e_tests_reads_the_failing_job_check_run_as_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[object, int, int, dict[str, object]]] = []

    def fake(
        forge_target: object, check_run_id: int, pr: int, **kw: object,
    ) -> list[dict[str, object]]:
        captured.append((forge_target, check_run_id, pr, dict(kw)))
        return []

    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [789])
    monkeypatch.setattr(_pr_branch, "fetch_check_run_annotations", fake)
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=_WIDGET_FORGE, timeout_s=7.0) is None
    [(forge_target, check_run_id, pr, kw)] = captured
    assert (forge_target, check_run_id, pr) == (_WIDGET_FORGE, 789, 42)
    assert kw["as_transport"] is True
    assert kw["timeout_s"] == 7.0


def test_fetch_failing_e2e_tests_audits_annotation_failures_under_its_own_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_kw: [789])
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh("", returncode=1))
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=DEFAULT_FORGE_TARGET) is None
    assert [(e["action"], e["pr_number"]) for e in _audit_entries(tmp_path)] == [
        ("fetch_e2e_annotations_failed", 42),
    ]
